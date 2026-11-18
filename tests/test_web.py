"""Offline tests of the web server: fake API client, synthetic site data, no network."""

import json
from types import SimpleNamespace

import pytest
from conftest import CONTACTS, MUNICIPALITIES, PROFILE, PROGRAMS
from fastapi.testclient import TestClient

from member_assistant import config
from member_assistant.web import Settings, create_app


def _usage():
    return SimpleNamespace(input_tokens=100, output_tokens=50, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0)


class ToolThenTextClient:
    """Fake API client: the first call asks for a knowledge-base search, the second replies with text."""

    def __init__(self):
        self.calls = 0

        def create(**kwargs):
            self.calls += 1
            if self.calls % 2 == 1:
                block = SimpleNamespace(type="tool_use", id=f"tu_{self.calls}", name="search_knowledge_base",
                                        input={"query": "solar panels", "source_type": "any"})
                return SimpleNamespace(content=[block], stop_reason="tool_use", stop_details=None,
                                       model="fake-model", usage=_usage())
            text = SimpleNamespace(type="text", citations=None,
                                   text="The Solar program offers free help: https://example-council.org/solar/")
            return SimpleNamespace(content=[text], stop_reason="end_turn", stop_details=None,
                                   model="fake-model", usage=_usage())

        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))


@pytest.fixture
def site(tmp_path, monkeypatch):
    files = {"PROFILE_PATH": PROFILE, "CONTACTS_PATH": CONTACTS, "PROGRAMS_PATH": PROGRAMS,
             "MUNICIPALITIES_PATH": MUNICIPALITIES, "SITE_ISSUES_PATH": []}
    for attr, data in files.items():
        path = tmp_path / f"{attr.lower()}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(config, attr, path)
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    return tmp_path


def make(kb, settings=None):
    client = ToolThenTextClient()
    return TestClient(create_app(settings or Settings(), client=client, kb=kb)), client


def events(response) -> list[tuple[str, dict]]:
    out = []
    for block in response.text.split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":") and ": " in line)
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def start(http, code=""):
    response = http.post("/api/session", json={"access_code": code})
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def test_config_exposes_name_and_access_code_flag(site, kb):
    http, _ = make(kb, Settings(access_code="cmu"))
    body = http.get("/api/config").json()
    assert body["org_name"] == "Example Council"
    assert body["access_code_required"] is True
    assert any("Future of Fire" in s for s in body["suggestions"])


def test_access_code_is_checked(site, kb):
    http, _ = make(kb, Settings(access_code="cmu"))
    assert http.post("/api/session", json={"access_code": "wrong"}).status_code == 401
    start(http, "cmu")


def test_chat_streams_tool_steps_then_the_reply_and_logs_it(site, kb):
    http, client = make(kb)
    sid = start(http)
    response = http.post("/api/chat", json={"session_id": sid, "message": "Do you help with solar panels?"})
    assert response.status_code == 200
    got = events(response)
    assert [name for name, _ in got] == ["step", "reply"]
    assert got[0][1]["label"] == "Searched the website for “solar panels”"
    assert "Solar" in got[1][1]["text"]
    assert client.calls == 2
    logged = (site / "logs" / "interactions.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(logged) == 1 and json.loads(logged[0])["guardrails"] is True


def test_emergency_is_answered_without_calling_the_model(site, kb):
    http, client = make(kb)
    sid = start(http)
    got = events(http.post("/api/chat", json={"session_id": sid, "message": "Someone is not breathing here"}))
    assert got[-1][0] == "reply" and "911" in got[-1][1]["text"]
    assert client.calls == 0


def test_unknown_session_and_long_messages_are_refused(site, kb):
    http, _ = make(kb, Settings(max_message_chars=20))
    assert http.post("/api/chat", json={"session_id": "nope", "message": "hi"}).status_code == 404
    sid = start(http)
    response = http.post("/api/chat", json={"session_id": sid, "message": "x" * 21})
    assert response.status_code == 413 and "20 characters" in response.json()["error"]


def test_per_visitor_rate_limit(site, kb):
    http, _ = make(kb, Settings(ip_messages=2))
    sid = start(http)
    for _ in range(2):
        assert http.post("/api/chat", json={"session_id": sid, "message": "solar?"}).status_code == 200
    assert http.post("/api/chat", json={"session_id": sid, "message": "solar?"}).status_code == 429


def test_turn_limit_per_session(site, kb):
    http, _ = make(kb, Settings(max_turns=1))
    sid = start(http)
    assert http.post("/api/chat", json={"session_id": sid, "message": "solar?"}).status_code == 200
    assert http.post("/api/chat", json={"session_id": sid, "message": "solar?"}).status_code == 429


def test_daily_budget_stops_answers_once_spent(site, kb):
    http, _ = make(kb, Settings(daily_budget_usd=0.000001))
    sid = start(http)
    assert http.post("/api/chat", json={"session_id": sid, "message": "solar?"}).status_code == 200
    response = http.post("/api/chat", json={"session_id": sid, "message": "solar?"})
    assert response.status_code == 503 and "info@example-council.org" in response.json()["error"]


def test_budget_counts_spend_already_logged_today(site, kb):
    http, _ = make(kb, Settings(daily_budget_usd=0.000001))
    start(http)
    http.post("/api/chat", json={"session_id": start(http), "message": "solar?"})
    restarted, _ = make(kb, Settings(daily_budget_usd=0.000001))  # e.g. the host restarted the server
    response = restarted.post("/api/chat", json={"session_id": start(restarted), "message": "solar?"})
    assert response.status_code == 503


def test_refuses_to_start_when_the_model_has_no_price(site, kb, monkeypatch):
    monkeypatch.setattr(config, "MODEL", "some-unpriced-model")
    with pytest.raises(RuntimeError, match="PRICES"):
        create_app(Settings(), client=ToolThenTextClient(), kb=kb)

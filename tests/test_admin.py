"""Offline tests of the staff dashboard: synthetic logs, no network."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from test_web import ToolThenTextClient, site  # noqa: F401  (site is a fixture)

from member_assistant import admin, config, logsync
from member_assistant.web import Settings, create_app

PASSWORD = "staff-secret"


def ts(days_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


def record(outcome, topic="solar", days_ago=0, need="Visitor asked about solar.", guardrails=True, session="s1"):
    return {"ts": ts(days_ago), "session": session, "guardrails": guardrails, "question": "q", "topic": topic,
            "need": need, "visitor_role": "municipal_staff", "outcome": outcome, "sources": [],
            "municipality_profiles": [], "redactions": {}, "escalation_refs": [], "tools_used": [],
            "guardrail_events": [], "cost_usd": 0.05, "latency_s": 10.0}


def write_logs(records, escalations=()):
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.LOG_DIR / "interactions.jsonl", "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r) + "\n" for r in records)
    with open(config.LOG_DIR / "escalations.jsonl", "w", encoding="utf-8") as f:
        f.writelines(json.dumps(e) + "\n" for e in escalations)


ESCALATIONS = [
    {"reference": "REQ-AAA111", "ts": ts(2), "topic": "solar", "urgency": "routine",
     "summary": "Wants help with a solar assessment.", "routed_to": "energy@example-council.org"},
    {"reference": "REQ-BBB222", "ts": ts(1), "topic": "future_of_fire", "urgency": "time_sensitive",
     "summary": "Council votes on a fire merger next week.", "routed_to": "chief@example-council.org"},
]


def client(kb, password=PASSWORD):
    app = create_app(Settings(admin_password=password), client=ToolThenTextClient(), kb=kb)
    return TestClient(app)


def signed_in(kb):
    c = client(kb)
    assert c.post("/api/admin/login", json={"password": PASSWORD}).status_code == 200
    return c


def test_dashboard_is_off_without_a_password(site, kb):
    c = client(kb, password="")
    assert c.get("/admin").status_code == 404
    assert c.get("/api/admin/overview").status_code == 404


def test_sign_in(site, kb):
    c = client(kb)
    assert c.get("/admin").status_code == 200
    assert c.get("/api/admin/overview").status_code == 401
    assert c.post("/api/admin/login", json={"password": "wrong"}).status_code == 401
    r = c.post("/api/admin/login", json={"password": PASSWORD})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert c.get("/api/admin/overview").status_code == 200
    c.post("/api/admin/logout")
    assert c.get("/api/admin/overview").status_code == 401


def test_login_needs_json_and_is_rate_limited(site, kb):
    c = client(kb)
    assert c.post("/api/admin/login", data={"password": PASSWORD}).status_code == 415
    for _ in range(10):
        c.post("/api/admin/login", json={"password": "wrong"})
    assert c.post("/api/admin/login", json={"password": PASSWORD}).status_code == 429


def test_tokens_expire_and_cannot_be_forged():
    now = 1_000_000.0
    token = admin.make_token(PASSWORD, now)
    assert admin.token_valid(PASSWORD, token, now + 60)
    assert not admin.token_valid(PASSWORD, token, now + admin.COOKIE_TTL_S + 1)
    assert not admin.token_valid("another password", token, now + 60)
    expires, sig = token.split(".")
    assert not admin.token_valid(PASSWORD, f"{int(expires) + 999}.{sig}", now + 60)
    assert not admin.token_valid(PASSWORD, "garbage", now)
    assert not admin.token_valid(PASSWORD, None, now)


def test_overview_counts(site, kb):
    write_logs([
        record("answered", session="a"), record("escalated", session="a"),
        record("no_answer_found", days_ago=3, session="b"), record("safety_redirect", topic="other", session="c"),
        record("answered", days_ago=20, session="d"),
        record("answered", guardrails=False, session="e"),  # unredacted test record: never shown
    ], ESCALATIONS)
    d = signed_in(kb).get("/api/admin/overview").json()
    assert d["today"]["questions"] == 3 and d["today"]["conversations"] == 2
    assert d["week"]["questions"] == 4
    assert d["week"]["answered_share"] == pytest.approx(1 / 3)  # the emergency isn't a need
    assert d["total_questions"] == 5
    assert d["open_requests"] == 2
    assert len(d["daily"]) == admin.CHART_DAYS
    assert d["daily"][-1] == {"date": d["daily"][-1]["date"], "answered": 1, "staff": 1, "unanswered": 0, "other": 1}
    assert d["log_backup"] is False


def test_requests_can_be_marked_handled_and_reopened(site, kb):
    write_logs([], ESCALATIONS)
    c = signed_in(kb)
    rows = c.get("/api/admin/requests").json()["requests"]
    assert [r["reference"] for r in rows] == ["REQ-BBB222", "REQ-AAA111"]  # time-sensitive first
    assert rows[0]["topic"] == "Future of Fire"

    assert c.post("/api/admin/requests/REQ-BBB222", json={"status": "handled"}).status_code == 200
    rows = c.get("/api/admin/requests").json()["requests"]
    assert [(r["reference"], r["status"]) for r in rows] == [("REQ-AAA111", "open"), ("REQ-BBB222", "handled")]
    c.post("/api/admin/requests/REQ-BBB222", json={"status": "open"})
    assert c.get("/api/admin/overview").json()["open_requests"] == 2

    assert c.post("/api/admin/requests/REQ-NOPE00", json={"status": "handled"}).status_code == 404
    assert c.post("/api/admin/requests/REQ-AAA111", json={"status": "deleted"}).status_code == 400
    assert c.post("/api/admin/requests/REQ-AAA111", data={"status": "handled"}).status_code == 415


def test_gaps_and_needs(site, kb):
    write_logs([record("no_answer_found", need=f"Wanted solar dues {i}") for i in range(3)] +
               [record("answered", topic="membership", need="Asked how to join"),
                record("no_answer_found", topic="membership", days_ago=40, need="Old question")])
    c = signed_in(kb)
    gaps = c.get("/api/admin/gaps?days=30").json()
    assert len(gaps["unanswered"]) == 3
    assert [t["label"] for t in gaps["gap_topics"]] == ["Solar"]
    assert len(c.get("/api/admin/gaps?days=0").json()["unanswered"]) == 4  # all time
    needs = c.get("/api/admin/needs?days=30").json()
    assert {t["label"]: t["questions"] for t in needs["topics"]} == {"Solar": 3, "Membership": 1}
    report = c.get("/api/admin/report.md?days=30")
    assert report.headers["content-disposition"].startswith("attachment")
    assert "# Website needs report" in report.text and "Old question" not in report.text


def test_log_backup_is_off_without_a_dataset(tmp_path, monkeypatch):
    monkeypatch.delenv("LOG_DATASET", raising=False)
    assert logsync.start_from_env(tmp_path) is None


def test_log_backup_failure_does_not_stop_the_server(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_DATASET", "someone/logs")

    def broken(*args, **kwargs):
        raise RuntimeError("no network")

    monkeypatch.setattr(logsync, "LogSync", broken)
    assert logsync.start_from_env(tmp_path) is None

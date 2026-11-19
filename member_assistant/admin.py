"""Staff dashboard: what visitors asked, requests for staff, and where the website falls short.

    GET  /admin                       the dashboard page (static/admin.html)
    POST /api/admin/login             {"password": ...}; sets a signed, HttpOnly cookie for 12 hours
    POST /api/admin/logout
    GET  /api/admin/overview          counts, outcomes, a 14-day chart, latency, today's spend
    GET  /api/admin/requests          requests for staff (escalations) with their status
    POST /api/admin/requests/{ref}    {"status": "open" | "handled"}
    GET  /api/admin/gaps              questions the website couldn't answer, topics with gaps, site problems
    GET  /api/admin/needs             questions by topic and visitor role, as in the needs report
    GET  /api/admin/report.md         the needs report (report.py) without the model-drafted section

The dashboard exists only when ADMIN_PASSWORD is set; otherwise these routes return 404. It reads the
same redacted logs as report.py, so it never shows anything the privacy filter removed.
"""

import hashlib
import hmac
import json
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from . import config, report
from .privacy import Redactor
from .tools import append_jsonl

STATIC_DIR = Path(__file__).resolve().parent / "static"
COOKIE = "ma_admin"
COOKIE_TTL_S = 12 * 3600
CHART_DAYS = 14

# How the dashboard groups the assistant's outcomes (tools.OUTCOMES).
OUTCOME_GROUPS = {
    "answered": ("Answered from the website", {"answered", "referred_to_resource"}),
    "staff": ("Sent to staff", {"referred_to_staff", "escalated"}),
    "unanswered": ("No answer found", {"no_answer_found"}),
    "other": ("Out of scope or emergency", {"out_of_scope", "safety_redirect"}),
}
OUTCOME_LABELS = {
    "answered": "Answered", "referred_to_resource": "Pointed to a page or form",
    "referred_to_staff": "Gave a staff contact", "escalated": "Request sent to staff",
    "no_answer_found": "No answer found", "out_of_scope": "Out of scope", "safety_redirect": "Emergency redirect",
}


class Login(BaseModel):
    password: str


class StatusUpdate(BaseModel):
    status: str


def _day(ts: str) -> str:
    return str(ts or "")[:10]


def _cutoff(days: int | None) -> str | None:
    if not days:
        return None
    return (datetime.now(timezone.utc).date() - timedelta(days=days - 1)).isoformat()


class Dashboard:
    """Reads the logs on every request (they are small), so it always shows the latest state."""

    def __init__(self, programs: list[dict], budget=None, log_backup: bool = False):
        self.labels = {p["id"]: p["name"] for p in programs}
        self.programs = programs
        self.budget = budget
        self.log_backup = log_backup

    # ------------------------------------------------------------ data

    @property
    def status_path(self) -> Path:
        return config.LOG_DIR / "request_status.jsonl"

    def topic_label(self, topic: str) -> str:
        if topic in self.labels:
            return self.labels[topic]
        return str(topic or "other").replace("_", " ").capitalize()

    def records(self, days: int | None = None) -> list[dict]:
        return report.load_records([config.LOG_DIR], _cutoff(days))[0]

    def statuses(self) -> dict[str, dict]:
        latest = {}
        if self.status_path.exists():
            for line in self.status_path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                latest[row["reference"]] = row
        return latest

    def overview(self) -> dict:
        records = self.records()
        today = datetime.now(timezone.utc).date()
        days = [(today - timedelta(days=i)).isoformat() for i in range(CHART_DAYS - 1, -1, -1)]
        week_start = days[-7]
        group_of = {o: g for g, (_, outcomes) in OUTCOME_GROUPS.items() for o in outcomes}

        daily = {d: Counter() for d in days}
        for r in records:
            if _day(r["ts"]) in daily:
                daily[_day(r["ts"])][group_of.get(r["outcome"], "other")] += 1
        week = [r for r in records if _day(r["ts"]) >= week_start]
        today_rows = [r for r in records if _day(r["ts"]) == days[-1]]
        latencies = [r["latency_s"] for r in week if r.get("latency_s") and r["outcome"] != "safety_redirect"]
        needs = [r for r in week if r["outcome"] not in report.NOT_NEEDS]
        open_requests = sum(1 for e in self.requests() if e["status"] == "open")

        return {
            "today": {
                "questions": len(today_rows),
                "conversations": len({r.get("session") for r in today_rows}),
                "spent_usd": round(self.budget.spent if self.budget else sum(r.get("cost_usd") or 0 for r in today_rows), 2),
                "budget_usd": self.budget.limit if self.budget else None,
            },
            "week": {
                "questions": len(week),
                "conversations": len({r.get("session") for r in week}),
                "answered_share": (sum(r["outcome"] in OUTCOME_GROUPS["answered"][1] for r in needs) / len(needs))
                if needs else None,
                "avg_latency_s": round(sum(latencies) / len(latencies), 1) if latencies else None,
                "cost_usd": round(sum(r.get("cost_usd") or 0 for r in week), 2),
            },
            "open_requests": open_requests,
            "total_questions": len(records),
            "first_logged": min((r["ts"] for r in records), default=None),
            "outcomes": [{"key": k, "label": OUTCOME_LABELS.get(k, k), "count": n}
                         for k, n in Counter(r["outcome"] for r in week).most_common()],
            "groups": [{"key": g, "label": label} for g, (label, _) in OUTCOME_GROUPS.items()],
            "daily": [{"date": d, **{g: daily[d][g] for g in OUTCOME_GROUPS}} for d in days],
            "log_backup": self.log_backup,
        }

    def requests(self) -> list[dict]:
        statuses = self.statuses()
        rows = []
        for e in report.load_escalations([config.LOG_DIR], None):
            status = statuses.get(e["reference"], {})
            rows.append({
                "reference": e["reference"], "ts": e["ts"], "topic": self.topic_label(e.get("topic")),
                "urgency": e.get("urgency", "routine"), "summary": e.get("summary", ""),
                "routed_to": e.get("routed_to", ""), "status": status.get("status", "open"),
                "status_changed": status.get("ts"),
            })
        rows.sort(key=lambda r: r["ts"], reverse=True)
        rows.sort(key=lambda r: (r["status"] != "open", r["urgency"] != "time_sensitive"))
        return rows

    def set_status(self, reference: str, status: str) -> dict:
        if status not in ("open", "handled"):
            raise HTTPException(400, "Status must be open or handled.")
        if reference not in {e["reference"] for e in report.load_escalations([config.LOG_DIR], None)}:
            raise HTTPException(404, "No request with that reference.")
        row = {"reference": reference, "status": status, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        append_jsonl(self.status_path, row)
        return row

    def gaps(self, days: int | None) -> dict:
        records = self.records(days)
        stats = report.analyze(records, self.programs)
        unanswered = [{"ts": r["ts"], "topic": self.topic_label(r["topic"]), "need": r["need"],
                       "outcome": OUTCOME_LABELS.get(r["outcome"], r["outcome"])}
                      for r in records if r["outcome"] in report.UNANSWERED]
        unanswered.sort(key=lambda r: r["ts"], reverse=True)
        return {
            "unanswered": unanswered[:200],
            "gap_topics": [{k: t[k] for k in ("label", "questions", "unanswered", "unanswered_share", "examples")}
                           for t in stats["topics"] if t["gap"]],
            "thresholds": {"min_questions": report.MIN_QUESTIONS, "share": report.GAP_SHARE},
            "site_issues": [{"type": i["type"].replace("_", " "), "url": i.get("url"), "detail": i.get("detail"),
                             "found_on": i.get("found_on", [])[:3]}
                            for i in config.load_json(config.SITE_ISSUES_PATH, [])],
        }

    def needs(self, days: int | None) -> dict:
        stats = report.analyze(self.records(days), self.programs)
        return {
            "topics": [{k: t[k] for k in ("label", "questions", "unanswered", "unanswered_share", "top_role", "gap",
                                          "examples")} for t in stats["topics"]],
            "roles": [{"role": role.replace("_", " "), "count": n} for role, n in stats["roles"].most_common()],
            "segments": [{"segment": s, "count": n} for s, n in stats["segments"].most_common()],
            "total": stats["total"], "needs": stats["needs"],
            "out_of_scope": stats["out_of_scope"], "safety": stats["safety"],
        }

    def report_markdown(self, days: int | None) -> str:
        since = _cutoff(days)
        records = report.load_records([config.LOG_DIR], since)[0]
        contacts = config.load_json(config.CONTACTS_PATH, {})
        redactor = Redactor(config.load_json(config.MUNICIPALITIES_PATH, []),
                            keep_emails={c["email"] for c in contacts.get("contacts", [])})
        return report.render(report.analyze(records, self.programs), config.load_json(config.SITE_ISSUES_PATH, []),
                             report.load_escalations([config.LOG_DIR], since), since, None, redactor)


# ---------------------------------------------------------------- sign-in


def _signing_key(password: str) -> bytes:
    # Derived from the password, so changing ADMIN_PASSWORD signs everyone out, and sign-ins
    # survive a restart.
    return hmac.new(password.encode(), b"member-assistant admin cookie", hashlib.sha256).digest()


def make_token(password: str, now: float | None = None) -> str:
    expires = int((now or time.time()) + COOKIE_TTL_S)
    sig = hmac.new(_signing_key(password), str(expires).encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{sig}"


def token_valid(password: str, token: str | None, now: float | None = None) -> bool:
    try:
        expires, sig = (token or "").split(".", 1)
        expected = hmac.new(_signing_key(password), expires.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected) and int(expires) > (now or time.time())
    except ValueError:
        return False


def router(password: str, dashboard: Dashboard, login_limit, client_ip, trust_proxy: bool) -> APIRouter:
    r = APIRouter()

    def signed_in(request: Request) -> None:
        if not token_valid(password, request.cookies.get(COOKIE)):
            raise HTTPException(401, "Please sign in.")

    def json_only(request: Request) -> None:
        # Together with the SameSite=Strict cookie this stops other sites from posting here.
        if not request.headers.get("content-type", "").startswith("application/json"):
            raise HTTPException(415, "Send JSON.")

    def https(request: Request) -> bool:
        if trust_proxy and request.headers.get("x-forwarded-proto") == "https":
            return True
        return request.url.scheme == "https"

    @r.get("/admin")
    def page():
        return FileResponse(STATIC_DIR / "admin.html", media_type="text/html",
                            headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})

    @r.post("/api/admin/login", dependencies=[Depends(json_only)])
    def login(body: Login, request: Request, response: Response):
        if not login_limit.allow(client_ip(request)):
            raise HTTPException(429, "Too many sign-in attempts. Wait a few minutes.")
        if not hmac.compare_digest(body.password.encode(), password.encode()):
            raise HTTPException(401, "That password isn't right.")
        response.set_cookie(COOKIE, make_token(password), max_age=COOKIE_TTL_S, httponly=True,
                            samesite="strict", secure=https(request), path="/")
        return {"ok": True}

    @r.post("/api/admin/logout")
    def logout(response: Response):
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    auth = [Depends(signed_in)]

    @r.get("/api/admin/overview", dependencies=auth)
    def overview():
        return dashboard.overview()

    @r.get("/api/admin/requests", dependencies=auth)
    def requests_():
        return {"requests": dashboard.requests()}

    @r.post("/api/admin/requests/{reference}", dependencies=[*auth, Depends(json_only)])
    def update_request(reference: str, body: StatusUpdate):
        return dashboard.set_status(reference, body.status)

    @r.get("/api/admin/gaps", dependencies=auth)
    def gaps(days: int = 30):
        return dashboard.gaps(days or None)

    @r.get("/api/admin/needs", dependencies=auth)
    def needs(days: int = 30):
        return dashboard.needs(days or None)

    @r.get("/api/admin/report.md", dependencies=auth)
    def report_md(days: int = 30):
        name = f"needs_report_{datetime.now(timezone.utc).date().isoformat()}.md"
        return PlainTextResponse(dashboard.report_markdown(days or None), media_type="text/markdown",
                                 headers={"Content-Disposition": f'attachment; filename="{name}"'})

    return r


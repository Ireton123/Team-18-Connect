"""Web server: the assistant as a chat widget that sits in the corner of a website.

    python -m member_assistant.web                 # http://127.0.0.1:8000
    uvicorn member_assistant.web:create_app --factory --host 0.0.0.0 --port $PORT   # hosting

Routes:
    GET  /             demo page: the organization's public website with the widget on top
    GET  /widget.js    the widget; any page can embed it with
                       <script src="https://<this server>/widget.js" defer></script>
    GET  /api/config   organization name, suggested questions, whether an access code is needed
    POST /api/session  start a visitor session (checks the access code)
    POST /api/chat     one visitor message; streams each tool call, then the reply (Server-Sent Events)
    GET  /healthz      liveness check for the hosting platform

Every message goes through the same ``Assistant.ask()`` as the command line and the evaluation, with
guardrails on. Because the endpoint is public and every message costs money, the server also enforces
limits, all set by environment variables (see ``Settings``): an optional access code, a daily spending
cap, a site-wide and a per-visitor message rate, a maximum message length, and a maximum number of
turns per session.
"""

import hmac
import json
import logging
import os
import queue
import re
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import config
from .agent import PRICES, Assistant, Reply
from .kb import KnowledgeBase

STATIC_DIR = Path(__file__).resolve().parent / "static"
log = logging.getLogger("member_assistant.web")


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@dataclass
class Settings:
    """Server limits. ``Settings.from_env()`` reads them from environment variables of the same name."""

    access_code: str = ""                # ACCESS_CODE: if set, visitors must enter it before chatting
    daily_budget_usd: float = 10.0       # DAILY_BUDGET_USD: stop answering for the rest of the UTC day at this spend
    site_messages_per_hour: int = 300    # SITE_MESSAGES_PER_HOUR: all visitors together
    ip_messages: int = 15                # IP_MESSAGES / IP_WINDOW_S: per visitor address
    ip_window_s: int = 600
    ip_sessions_per_hour: int = 30       # IP_SESSIONS_PER_HOUR
    max_message_chars: int = 1000        # MAX_MESSAGE_CHARS
    max_turns: int = 20                  # MAX_TURNS: messages per session
    session_ttl_s: int = 1800            # SESSION_TTL_S: idle sessions are dropped after this
    max_sessions: int = 300              # MAX_SESSIONS: oldest idle sessions are dropped beyond this
    trust_proxy_headers: bool = False    # TRUST_PROXY_HEADERS=1 behind a hosting proxy (Render, Fly, ...)
    allowed_origins: list[str] = field(default_factory=list)  # ALLOWED_ORIGINS: other sites that embed the widget

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            access_code=os.environ.get("ACCESS_CODE", "").strip(),
            daily_budget_usd=_env_float("DAILY_BUDGET_USD", cls.daily_budget_usd),
            site_messages_per_hour=_env_int("SITE_MESSAGES_PER_HOUR", cls.site_messages_per_hour),
            ip_messages=_env_int("IP_MESSAGES", cls.ip_messages),
            ip_window_s=_env_int("IP_WINDOW_S", cls.ip_window_s),
            ip_sessions_per_hour=_env_int("IP_SESSIONS_PER_HOUR", cls.ip_sessions_per_hour),
            max_message_chars=_env_int("MAX_MESSAGE_CHARS", cls.max_message_chars),
            max_turns=_env_int("MAX_TURNS", cls.max_turns),
            session_ttl_s=_env_int("SESSION_TTL_S", cls.session_ttl_s),
            max_sessions=_env_int("MAX_SESSIONS", cls.max_sessions),
            trust_proxy_headers=os.environ.get("TRUST_PROXY_HEADERS", "0") == "1",
            allowed_origins=[o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()],
        )


# ---------------------------------------------------------------- limits


class RateLimiter:
    """Sliding-window counter: at most ``limit`` events per ``window_s`` seconds for each key."""

    def __init__(self, limit: int, window_s: float):
        self.limit, self.window_s = limit, window_s
        self._events: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            events = self._events.setdefault(key, deque())
            while events and now - events[0] > self.window_s:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            if len(self._events) > 10_000:  # forget idle keys so memory stays bounded
                self._events = {k: v for k, v in self._events.items() if v and now - v[-1] <= self.window_s}
            return True


class Budget:
    """Model spending for the current UTC day, seeded from today's interaction log at startup."""

    def __init__(self, limit_usd: float, log_path: Path):
        self.limit = limit_usd
        self._lock = threading.Lock()
        self._day = self._today()
        self._spent = self._spent_in_log(log_path, self._day)

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    @staticmethod
    def _spent_in_log(path: Path, day: str) -> float:
        if not path.exists():
            return 0.0
        total = 0.0
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(record.get("ts", "")).startswith(day):
                total += float(record.get("cost_usd") or 0)
        return total

    def _roll(self) -> None:
        today = self._today()
        if today != self._day:
            self._day, self._spent = today, 0.0

    def exhausted(self) -> bool:
        with self._lock:
            self._roll()
            return self.limit > 0 and self._spent >= self.limit

    def add(self, cost: float) -> None:
        with self._lock:
            self._roll()
            self._spent += cost

    @property
    def spent(self) -> float:
        with self._lock:
            self._roll()
            return self._spent


# ---------------------------------------------------------------- sessions


@dataclass
class Session:
    assistant: Assistant
    last_used: float
    turns: int = 0
    busy: threading.Lock = field(default_factory=threading.Lock)


class SessionStore:
    def __init__(self, ttl_s: float, max_sessions: int):
        self.ttl_s, self.max_sessions = ttl_s, max_sessions
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        for sid in [s for s, v in self._sessions.items() if now - v.last_used > self.ttl_s]:
            del self._sessions[sid]
        while len(self._sessions) >= self.max_sessions:
            del self._sessions[min(self._sessions, key=lambda s: self._sessions[s].last_used)]

    def add(self, assistant: Assistant) -> str:
        sid = secrets.token_urlsafe(24)
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            self._sessions[sid] = Session(assistant, now)
        return sid

    def get(self, sid: str) -> Session | None:
        now = time.monotonic()
        with self._lock:
            session = self._sessions.get(sid)
            if session is None or now - session.last_used > self.ttl_s:
                self._sessions.pop(sid, None)
                return None
            session.last_used = now
            return session

    def __len__(self) -> int:
        return len(self._sessions)


# ---------------------------------------------------------------- what the widget sees


def _short(text, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def step_view(step: dict) -> dict:
    """A tool call as the widget shows it: a plain-language label, not the raw arguments."""
    args = step.get("input") or {}
    tool = step["tool"]
    if tool == "search_knowledge_base":
        label = f"Searched the website for “{_short(args.get('query'), 80)}”"
    elif tool == "read_document":
        label = f"Read {_short(str(args.get('url', '')).split('://', 1)[-1], 80)}"
    elif tool == "list_programs":
        category = args.get("category")
        label = "Listed programs" + (f" ({category})" if category and category != "all" else "")
    elif tool == "find_contact":
        label = f"Looked up who handles {_short(str(args.get('topic', '')).replace('_', ' '), 60)}"
    elif tool == "escalate_to_staff":
        label = "Sent the request to staff"
    elif tool == "log_visitor_need":
        label = "Saved an anonymous summary of the question"
    else:
        label = tool
    detail = "" if tool in ("log_visitor_need", "escalate_to_staff") else _short(step.get("result"), 160)
    if tool == "find_contact":  # the result is JSON; show the best match instead
        try:
            match = json.loads(step.get("output") or "{}").get("matches", [{}])[0]
            detail = " · ".join(x for x in (match.get("role"), match.get("email")) if x)
        except (ValueError, AttributeError, IndexError):
            detail = ""
    elif tool == "list_programs":  # the result is a JSON list; show the program names
        try:
            names = [p["name"] for p in json.loads(step.get("output") or "[]") if p.get("name")]
            detail = _short(", ".join(names), 160)
        except (ValueError, TypeError, KeyError, AttributeError):
            detail = ""
    return {"tool": tool, "label": label, "detail": detail, "is_error": bool(step.get("is_error"))}


_CHUNK_SUFFIX = re.compile(r"\s*\(part \d+ of \d+\)$")


def source_cards(citations: list[dict]) -> list[dict]:
    """One card per page. The knowledge base splits pages into chunks ("About (part 2 of 3)"),
    and several chunks of one page often back the same answer."""
    cards, seen = [], set()
    for c in citations:
        if c["source"] in seen:
            continue
        seen.add(c["source"])
        cards.append({"title": _CHUNK_SUFFIX.sub("", c["title"]), "url": c["source"]})
    return cards


def reply_view(reply: Reply) -> dict:
    return {
        "text": reply.text,
        "citations": source_cards(reply.citations),
        "escalations": [{"reference": e["reference"], "routed_to": e["routed_to"]} for e in reply.escalations],
        "outcome": (reply.need or {}).get("outcome"),
        "guardrail_events": reply.guardrail_events,
        "latency_s": reply.latency_s,
    }


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class SessionIn(BaseModel):
    access_code: str = ""


class ChatIn(BaseModel):
    session_id: str
    message: str


# ---------------------------------------------------------------- app


def create_app(settings: Settings | None = None, client: anthropic.Anthropic | None = None,
               kb: KnowledgeBase | None = None) -> FastAPI:
    """Build the server. Tests pass a fake ``client`` and a synthetic ``kb``; hosting uses the defaults."""
    settings = settings or Settings.from_env()
    if settings.daily_budget_usd > 0 and config.MODEL not in PRICES:
        # Without a price the spend would read $0 and the daily cap would never trigger.
        raise RuntimeError(f"No price for {config.MODEL!r} in agent.PRICES; add one before serving it publicly.")

    profile = config.load_profile()
    kb = kb or KnowledgeBase.load()
    contacts = config.load_json(config.CONTACTS_PATH, {})
    programs = config.load_json(config.PROGRAMS_PATH, [])
    contact = contacts.get("general_email") or contacts.get("contact_page") or profile.get("site_url", "")
    shared_client = client  # created lazily so the server can start (and show errors) without a key

    sessions = SessionStore(settings.session_ttl_s, settings.max_sessions)
    budget = Budget(settings.daily_budget_usd, config.LOG_DIR / "interactions.jsonl")
    site_limit = RateLimiter(settings.site_messages_per_hour, 3600)
    ip_limit = RateLimiter(settings.ip_messages, settings.ip_window_s)
    session_limit = RateLimiter(settings.ip_sessions_per_hour, 3600)

    app = FastAPI(title="Member assistant", docs_url=None, redoc_url=None, openapi_url=None)
    if settings.allowed_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins,
                           allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    def client_ip(request: Request) -> str:
        # Behind a hosting proxy the socket address is the proxy's; the visitor is in X-Forwarded-For.
        # A visitor can forge that header, so the per-address limit is a courtesy limit only; the
        # site-wide rate and the daily budget are the hard caps.
        if settings.trust_proxy_headers:
            forwarded = request.headers.get("x-forwarded-for", "")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def refuse(status: int, message: str):
        raise HTTPException(status_code=status, detail=message)

    def get_client() -> anthropic.Anthropic:
        nonlocal shared_client
        if shared_client is None:
            shared_client = anthropic.Anthropic()
        return shared_client

    suggestions = [f"What does {profile['org_name']} do?", "How can our municipality become a member?"]
    if programs:
        suggestions.append(f"Tell me about the {programs[0]['name']} program")

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "documents": len(kb.documents), "sessions": len(sessions)}

    @app.get("/api/config")
    def get_config():
        return {
            "org_name": profile["org_name"],
            "site_url": profile.get("site_url"),
            "contact": contact,
            "access_code_required": bool(settings.access_code),
            "max_message_chars": settings.max_message_chars,
            "suggestions": suggestions,
        }

    @app.post("/api/session")
    def start_session(body: SessionIn, request: Request):
        if settings.access_code and not hmac.compare_digest(body.access_code.strip().encode(),
                                                            settings.access_code.encode()):
            refuse(401, "That access code isn't right.")
        if not session_limit.allow(client_ip(request)):
            refuse(429, "Too many new conversations from this network. Please try again later.")
        try:
            api_client = get_client()
        except anthropic.AnthropicError:  # no API key configured on the server
            log.error("Anthropic client could not be created; is ANTHROPIC_API_KEY set?")
            refuse(503, "The assistant isn't configured yet. Please try again later.")
        assistant = Assistant(guardrails=True, client=api_client, kb=kb)
        return {"session_id": sessions.add(assistant)}

    @app.post("/api/chat")
    def chat(body: ChatIn, request: Request):
        message = body.message.strip()
        session = sessions.get(body.session_id)
        if session is None:
            refuse(404, "This conversation has expired. Please start a new one.")
        if not message:
            refuse(400, "Please type a question.")
        if len(message) > settings.max_message_chars:
            refuse(413, f"Please keep questions under {settings.max_message_chars} characters.")
        if session.turns >= settings.max_turns:
            refuse(429, "This conversation has reached its length limit. Please start a new one.")
        if budget.exhausted():
            refuse(503, f"The assistant has reached today's usage limit. Please email {contact} instead.")
        if not site_limit.allow("site"):
            refuse(503, "The assistant is busy right now. Please try again in a few minutes.")
        if not ip_limit.allow(client_ip(request)):
            refuse(429, "You're sending messages quickly. Please wait a few minutes and try again.")
        if not session.busy.acquire(blocking=False):
            refuse(409, "Please wait for the current answer to finish.")
        session.turns += 1

        events: queue.Queue = queue.Queue()

        def work():
            try:
                reply = session.assistant.ask(message, on_step=lambda step: events.put(("step", step_view(step))))
                budget.add(reply.cost_usd)
                events.put(("reply", reply_view(reply)))
            except anthropic.AuthenticationError:
                log.error("Anthropic API key missing or invalid")
                events.put(("error", {"message": "The assistant isn't configured yet. Please try again later."}))
            except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError):
                session.turns -= 1
                events.put(("error", {"message": "The assistant is busy right now. Please try again in a minute."}))
            except Exception:
                log.exception("chat turn failed")
                session.turns -= 1
                events.put(("error", {"message": f"Something went wrong. Please try again, or email {contact}."}))
            finally:
                session.busy.release()
                events.put(None)

        threading.Thread(target=work, daemon=True).start()

        def stream():
            yield ": connected\n\n"
            while True:
                try:
                    item = events.get(timeout=15)
                except queue.Empty:
                    yield ": keep-alive\n\n"  # stops proxies from closing a quiet connection
                    continue
                if item is None:
                    return
                yield sse(*item)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.exception_handler(HTTPException)
    def http_error(request: Request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.get("/widget.js")
    def widget():
        return FileResponse(STATIC_DIR / "widget.js", media_type="text/javascript",
                            headers={"Cache-Control": "public, max-age=300"})

    @app.get("/")
    def demo_page():
        return FileResponse(STATIC_DIR / "demo.html", media_type="text/html")

    return app


def main() -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Run the website assistant locally.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("member_assistant.web:create_app", factory=True, host=args.host, port=args.port)


if __name__ == "__main__":
    main()

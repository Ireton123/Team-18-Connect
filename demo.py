"""Live demo: how the website assistant answers one question, and what keeps it safe.

The one idea it shows: a language model on its own can't be trusted to answer for an
organization, so the model works through tools we wrote, and checks we wrote run before and
after it. Four parts, each a few seconds:

  1. The tools the model can call, with the documentation the model reads.
  2. Guardrails that run without the model: emergency screen, redaction, link check.
  3. The agent at work: one visitor question, each tool call printed as it happens.
  4. (--compare) The same visitor message with and without guardrails, and what each version
     stores about the visitor.

    python demo.py                       # real crawl in private/ if present, else demo_data/
    python demo.py --synthetic           # always the made-up organization in demo_data/
    python demo.py --offline             # no API key: a scripted stand-in plays the model in part 3
    python demo.py --question "..."      # your own question for part 3
    python demo.py --compare             # add part 4 (two more model calls)
    python demo.py --parts 3             # only some parts, e.g. "2,3"
    python demo.py --no-pause            # don't wait for Enter between parts

Parts 1 and 2 never call the model. Parts 3 and 4 use Claude through ANTHROPIC_API_KEY,
about $0.05 per question. Nothing is written to the real logs.
"""

import argparse
import json
import os
import re
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--synthetic", action="store_true", help="Use demo_data/ even if private/ has a crawl")
    parser.add_argument("--offline", action="store_true", help="No API key: script the model's part")
    parser.add_argument("--question", help="The visitor question for part 3")
    parser.add_argument("--compare", action="store_true", help="Add part 4: with vs. without guardrails")
    parser.add_argument("--no-pause", action="store_true", help="Don't wait for Enter between parts")
    parser.add_argument("--parts", default="1,2,3,4", help="Which parts to run, e.g. '3' or '2,3' (default: all)")
    return parser.parse_args()


ARGS = parse_args()
REAL = ROOT / "private"
USE_REAL = not ARGS.synthetic and (REAL / "kb" / "chunks.jsonl").exists()
# config.py reads this when it is imported, so set it first.
os.environ["MEMBER_ASSISTANT_PRIVATE_DIR"] = str(REAL if USE_REAL else ROOT / "demo_data")

from member_assistant import config  # noqa: E402
from member_assistant.agent import Assistant  # noqa: E402
from member_assistant.guardrails import emergency_reply, is_emergency  # noqa: E402
from member_assistant.kb import KnowledgeBase, chunk_document  # noqa: E402

# ---------------------------------------------------------------- output

COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def style(text, code):
    return f"\033[{code}m{text}\033[0m" if COLOR else text


def bold(t): return style(t, "1")
def dim(t): return style(t, "2")
def green(t): return style(t, "32")
def red(t): return style(t, "31")
def blue(t): return style(t, "34")


def wrap(text, indent="    ", width=96):
    return "\n".join(textwrap.fill(line, width, initial_indent=indent, subsequent_indent=indent) if line else ""
                     for line in str(text).splitlines())


def labeled(label, text, width=96):
    """``label`` on the first line, the wrapped text aligned after it."""
    pad = " " * len(label)
    lines = []
    for para in str(text).split("\n"):
        lines += textwrap.wrap(para, width - len(label)) or [""]
    return "\n".join((label if i == 0 else pad) + line for i, line in enumerate(lines))


SHOWN = []


def part(n, title, why):
    if SHOWN and not ARGS.no_pause and sys.stdin.isatty():
        input(dim(f"\n[Enter] for part {n}"))
    SHOWN.append(n)
    print("\n" + bold(f"{n}. {title}"))
    print(dim(wrap(why, indent="   ")))
    print()


# ---------------------------------------------------------------- setup


def knowledge_base():
    if USE_REAL:
        return KnowledgeBase.load()
    docs = [json.loads(line) for line in (config.KB_DIR / "documents.jsonl").read_text().splitlines() if line]
    return KnowledgeBase(docs, [c for d in docs for c in chunk_document(d)], policy={})


class ScriptedModel:
    """Stands in for Claude when there is no API key (--offline).

    It always makes the same three tool calls (search, find a contact, log the need) and then
    writes its answer from what the real tools returned. Our tools, guardrails and logging all run
    for real; only the model's decisions are scripted.
    """

    def __init__(self):
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    @staticmethod
    def _response(content, stop):
        usage = SimpleNamespace(input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        return SimpleNamespace(content=content, stop_reason=stop, stop_details=None, model="scripted", usage=usage)

    def _tool(self, n, name, args):
        return self._response([SimpleNamespace(type="tool_use", id=f"scripted_{n}", name=name, input=args)], "tool_use")

    def create(self, messages, **kwargs):
        # Everything after the visitor's last typed message belongs to this turn.
        start = max(i for i, m in enumerate(messages) if m["role"] == "user" and isinstance(m["content"], str))
        question = messages[start]["content"]
        results = [b for m in messages[start + 1:] if m["role"] == "user" for b in m["content"]
                   if isinstance(b, dict) and b.get("type") == "tool_result"]
        if len(results) == 0:
            return self._tool(0, "search_knowledge_base", {"query": question, "source_type": "any"})
        hits = [b for b in results[0]["content"] if isinstance(b, dict) and b.get("type") == "search_result"] \
            if isinstance(results[0]["content"], list) else []
        if len(results) == 1:
            return self._tool(1, "find_contact", {"topic": question})
        if len(results) == 2:
            program = next((p["id"] for p in config.load_json(config.PROGRAMS_PATH, [])
                            if hits and p["url"] == hits[0]["source"]), "other")
            return self._tool(2, "log_visitor_need", {
                "topic": program, "need": "Visitor asked the question shown in the demo.", "visitor_role": "unknown",
                "outcome": "answered" if hits else "no_answer_found", "sources": [h["source"] for h in hits[:1]]})
        contact = json.loads(results[1]["content"]) if isinstance(results[1]["content"], str) else {}
        best = (contact.get("matches") or [{}])[0].get("email") or contact.get("general_inbox")
        if hits:
            top = hits[0]
            lines = " ".join(t["text"] for t in top["content"]).split("\n")
            body = " ".join(line for line in lines[1:] if not line.startswith("STAGE OF ENGAGEMENT"))
            excerpt = body[:280].rsplit(" ", 1)[0]
            text = f"From **{top['title']}**: {excerpt}…\n\nMore: {top['source']}"
        else:
            text = "I couldn't find that on the website."
        if best:
            text += f"\n\nThe right person to contact: {best}"
        text += "\n\n_(Scripted stand-in for the model: with an API key, Claude writes this answer.)_"
        return self._response([SimpleNamespace(type="text", text=text, citations=None)], "end_turn")


def make_assistant(guardrails=True):
    client = ScriptedModel() if ARGS.offline else None
    return Assistant(guardrails=guardrails, client=client, kb=KB, log_dir=LOG_DIR)


# ---------------------------------------------------------------- parts


def show_tools(assistant):
    for tool in assistant.tools:
        first = tool["description"].split(". ")[0].rstrip(".") + "."
        params = ", ".join(tool["input_schema"]["properties"])
        print(f"  {blue(tool['name'])}({dim(params)})")
        print(wrap(first, indent="      "))
    print()
    print(wrap("Topics, visitor roles and outcomes are fixed lists (enums) in the schemas, so every logged need "
               "can be counted. The full text the model reads is in member_assistant/tools.py.", indent="  "))


def show_guardrails(assistant):
    town = next((m["name"] for m in config.load_json(config.MUNICIPALITIES_PATH, []) if not m["ambiguous"]), "Millbrook")
    site = assistant.profile["site_url"].rstrip("/")

    message = "There's a fire in my building and smoke is coming under the door"
    print(bold("  a) Emergency screen") + dim("  runs before the model; a match never reaches it"))
    print(f"     visitor: {message}")
    print(f"     is_emergency() = {red(is_emergency(message))}")
    print(labeled("     reply:   ", emergency_reply(assistant.profile)))

    message = f"Hi, I'm Dana Smith from {town}. Please call me at 412-555-0187 about joining."
    redaction = assistant.redactor.redact(message)
    print("\n" + bold("  b) Redaction") + dim("  before anything is stored"))
    print(f"     visitor: {message}")
    print(f"     stored:  {green(redaction.text)}")
    print(dim(f"     removed: {dict(redaction.counts)}"))

    draft = (f"See {site}/ for details, or apply at https://grants-portal.example.com/apply "
             "and email grants@made-up-office.org.")
    cleaned, removed = assistant.verifier.clean(draft, fallback_contact=assistant.contact)
    print("\n" + bold("  c) Link and email check") + dim("  after the model; only what the website publishes survives"))
    print(f"     model wrote: {draft}")
    print(green(labeled("     visitor saw: ", cleaned)))
    for r in removed:
        print(dim(f"     removed {r['kind']} {r['value']} ({r['reason'].replace('_', ' ')})"))


def show_agent(assistant, question):
    print(f"  {bold('Visitor:')} {question}\n")
    started = time.monotonic()

    def on_step(step):
        args = ", ".join(f"{k}={v!r}" for k, v in step["input"].items() if k != "sources")
        print(f"  {dim(f'{time.monotonic() - started:5.1f}s')}  {blue(step['tool'])}({textwrap.shorten(args, 80)})")
        if step["result"]:
            print(dim(f"           → {textwrap.shorten(step['result'], 84)}"))

    if not ARGS.offline:
        print(dim("  (Claude is deciding which tool to call…)"))
    reply = assistant.ask(question, on_step=on_step)

    print(f"\n  {bold('Assistant:')}")
    print(wrap(reply.text))
    if reply.citations:
        print(dim("\n  Sources: " + "; ".join(sorted({c['source'] for c in reply.citations}))))
    if reply.guardrail_events:
        print(dim("  Guardrails that acted: " + ", ".join(reply.guardrail_events)))
    print(dim(f"\n  Stored for staff (anonymous): topic={reply.need['topic']}, outcome={reply.need['outcome']}, "
              f"need={reply.need['need']!r}"))
    print(dim(f"  {len(reply.trace)} tool calls, {reply.latency_s} s, ${reply.cost_usd:.3f}"))


def show_compare(question):
    print(f"  {bold('Visitor:')} {question}\n")
    log = LOG_DIR / "interactions.jsonl"
    for guarded in (True, False):
        label = green("WITH guardrails") if guarded else red("WITHOUT guardrails (one-line prompt, same tools)")
        print(f"  {bold(label)}")
        reply = make_assistant(guardrails=guarded).ask(question)
        plain = re.sub(r"\*\*|^\s*[-*] ", "", reply.text, flags=re.M)  # a one-paragraph preview
        print(wrap(textwrap.shorten(plain.replace("\n", " "), 330)))
        record = json.loads(log.read_text().splitlines()[-1])  # what this reply just logged
        color = green if guarded else red
        print(f"    {bold('Stored for staff:')}")
        print(color(labeled("      question: ", record["question"])))
        print(color(labeled("      need:     ", record["need"])))
        print(dim(f"      tools: {', '.join(s['tool'] for s in reply.trace) or 'none'}\n"))


# ---------------------------------------------------------------- main

KB = knowledge_base()
LOG_DIR = Path(tempfile.mkdtemp(prefix="demo-logs-"))


def main():
    if not ARGS.offline and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY, or run `python demo.py --offline` to script the model's part.")
    assistant = make_assistant()
    org = assistant.profile["org_name"]
    source = "the team's crawl of the real website (private/)" if USE_REAL else "the made-up organization in demo_data/"
    print(bold(f"{org}: website assistant demo"))
    print(dim(f"Data: {source}, {len(KB.chunks)} searchable passages. "
              f"Model: {'scripted stand-in' if ARGS.offline else config.MODEL}."))

    parts = {int(x) for x in ARGS.parts.split(",") if x.strip()}
    if 1 in parts:
        part(1, "The tools the model can call",
         "The model can't browse or guess. It can only call these six functions, and each one's description is "
         "written for the model to read: when to use it, what it returns, what not to do with it.")
        show_tools(assistant)

    if 2 in parts:
        part(2, "Guardrails that don't depend on the model",
             "Plain code, so they behave the same every time. They run before the model sees a message and after "
             "it writes a reply.")
        show_guardrails(assistant)

    question = ARGS.question or (f"Our volunteer fire company can't find enough people anymore. Does {org} have "
                                 "anything that could help, and who should I talk to?")
    if 3 in parts:
        part(3, "The agent at work",
             "One visitor question. The model plans its own steps; each line below is a tool call as it happens.")
        show_agent(assistant, question)

    if ARGS.compare and 4 in parts:
        if ARGS.offline:
            print(dim("\n(Part 4 needs the real model; skipped with --offline.)"))
        else:
            part(4, "With vs. without guardrails",
                 "The same message, once with our system prompt and checks, once with a one-line prompt and no "
                 "checks. Both answer politely; the difference is what they keep about the visitor.")
            town = next((m["name"] for m in config.load_json(config.MUNICIPALITIES_PATH, []) if not m["ambiguous"]),
                        "Millbrook")
            show_compare(f"Hi, I'm Dana Smith, the borough manager in {town}. How does our borough join? "
                         "You can reach me at 412-555-0187.")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()

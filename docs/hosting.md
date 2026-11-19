# Hosting the website assistant

The assistant runs as a small web server (`member_assistant/web.py`, FastAPI) that serves a chat widget for the corner of a website. Every message goes through the same `Assistant.ask()` as the command line and the evaluation, with guardrails on.

## What visitors see

- `GET /`: a demo page that shows the organization's live public website in a frame, with the widget in the bottom-right corner. A thin bar at the top labels it as a prototype.
- The widget: a chat button that opens a panel. While the agent works, each tool call appears as a step ("Searched the website for …", "Read …", "Looked up who handles …"). The answer then shows its source pages as cards, any staff reference number, and a collapsible "How I answered" list of the steps.
- On a phone the panel fills the screen.

To put the widget on another site (for example the organization's own website), add one line to its pages and list that site in `ALLOWED_ORIGINS`:

```html
<script src="https://<assistant server>/widget.js" data-accent="#0f4c75" defer></script>
```

## Staff dashboard

Set `ADMIN_PASSWORD` and staff can sign in at `/admin`. It is a separate password from the visitors' `ACCESS_CODE`. The dashboard reads the same redacted logs as the needs report (`report.py`), so it shows nothing the privacy filter removed. It has four tabs:

- **Overview**: questions today and over the last 7 days, the share answered from the website, open requests for staff, average answer time, today's AI cost against the daily cap, and a 14-day chart of how questions ended.
- **Requests for staff**: every escalation with its reference number, urgency, topic, the anonymous summary and who it was routed to. Staff mark each one handled, or reopen it; the status is kept in `request_status.jsonl` next to the other logs.
- **Content gaps**: the questions the website couldn't answer, the topics that pass the needs report's gap threshold, and the broken links the crawler found.
- **Visitor needs**: questions by topic, visitor role and municipality type (never by name).

"Download needs report" saves the Markdown needs report for the chosen period, without the model-drafted suggestions; run `python -m member_assistant.report` for those.

The sign-in sets an HttpOnly, SameSite=Strict cookie that lasts 12 hours. Changing `ADMIN_PASSWORD` signs everyone out. After 10 failed attempts in 15 minutes from one address, sign-in is blocked for a while. Open the dashboard at the `hf.space` address: inside the `huggingface.co/spaces/...` frame the browser doesn't send the cookie.

## Run it locally

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
python -m member_assistant.crawl --site https://<client-site> --county-wiki "<Wikipedia county article>"   # once
python -m member_assistant.web          # http://127.0.0.1:8000
```

## Limits for a public endpoint

Every message costs money. With Claude Opus 5.5 the average in the evaluation was about $0.04. In a live test of the website, single answers cost $0.03 to $0.10 (more when the agent escalates or reads several pages) and took 7 to 19 seconds. Emergency messages are answered without calling the model and cost nothing. The server enforces these limits. Each is an environment variable.

| Variable | Default | What it does |
| --- | --- | --- |
| `ACCESS_CODE` | empty (open) | If set, visitors enter this code before chatting. Use it until the client approves a public launch. |
| `DAILY_BUDGET_USD` | 10 | Stops answering for the rest of the UTC day once the day's model spend reaches this. It counts spend already in today's log, so a restart doesn't reset it. |
| `SITE_MESSAGES_PER_HOUR` | 300 | All visitors together. |
| `IP_MESSAGES` / `IP_WINDOW_S` | 15 per 600 s | Per visitor address. Behind a proxy the address comes from `X-Forwarded-For`, which a visitor can forge, so this is a courtesy limit; the two above are the hard caps. |
| `IP_SESSIONS_PER_HOUR` | 30 | New conversations per address. |
| `MAX_MESSAGE_CHARS` | 1000 | Longest accepted message. |
| `MAX_TURNS` | 20 | Messages per conversation. |
| `SESSION_TTL_S` / `MAX_SESSIONS` | 1800 / 300 | Idle conversations are dropped after 30 minutes; the oldest go first beyond 300. |
| `TRUST_PROXY_HEADERS` | 0 | Set to 1 behind a hosting proxy. |
| `ALLOWED_ORIGINS` | empty | Other sites allowed to embed the widget, comma-separated. |

The server refuses to start if the configured model has no price in `agent.PRICES`, because the daily cap would then never trigger. As a last backstop, give the hosting key its own spend limit in the Anthropic Console.

## Deploy on Hugging Face Spaces

The hosted demo runs as a Docker Space. Hosting a Docker Space needs a Hugging Face PRO subscription on the account that owns it ($9 a month in November 2026); without one, creating the Space fails with "402 Payment Required". The Space itself runs on the free `cpu-basic` hardware, which is enough because the model runs at Anthropic. It sleeps only after 48 hours without visitors, so it stays awake through a presentation. Don't change the hardware in the Space settings: other hardware is billed by the hour.

```bash
pip install huggingface_hub
hf auth login                                   # once; a token with write access
python deploy/hf_space.py <username>/<space-name>
```

The script creates a public Space if needed and asks for its secrets: `ANTHROPIC_API_KEY`, `CLIENT_SITE`, `COUNTY_WIKI` and `ACCESS_CODE`. They are stored as Space secrets, which visitors can't see. It then uploads only what the image needs: the `Dockerfile`, `requirements.txt`, `member_assistant/` and `deploy/start.sh`, plus `deploy/huggingface/README.md` as the Space's card. Hugging Face builds the image and crawls the public website as part of the build, which takes about 5 minutes. The app is then at `https://<username>-<space-name>.hf.space`.

To ship new code, run the script again with `--code-only`. Each new build crawls the website again, so the knowledge base is as fresh as the last deploy.

Notes:

- The Space's file system is reset whenever it restarts or wakes up. The script therefore also creates a private dataset, `<space-name>-logs`, and sets the `LOG_DATASET` variable. With an `HF_TOKEN` secret that can write to that dataset, the app restores the logs from it when it starts and uploads them every 2 minutes (`member_assistant/logsync.py`). Without the token the app still runs, but the logs and the staff dashboard start empty after each restart.
- Use the `hf.space` address for the demo. The `huggingface.co/spaces/...` page shows the app inside a Hugging Face frame.

## Deploy on Render (alternative)

`render.yaml` describes the service. In Render: **New → Blueprint**, pick this repository, then fill in the values it asks for:

- `ANTHROPIC_API_KEY`: a key used only for this site
- `CLIENT_SITE` and `COUNTY_WIKI`: the same values as the crawl command above. The knowledge base is built from the public website during each deploy (about 3 minutes), so no crawled content is stored in the repository.
- `ACCESS_CODE`: optional, see above

Notes:

- The free plan sleeps after 15 minutes without traffic and takes about a minute to wake. Switch to a paid plan for the presentation week.
- Render's file system is reset on each deploy, so the interaction log there starts empty after every deploy. Download it before redeploying if you need it for the needs report.

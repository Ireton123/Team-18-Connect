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

## Deploy on Render

`render.yaml` describes the service. In Render: **New → Blueprint**, pick this repository, then fill in the values it asks for:

- `ANTHROPIC_API_KEY`: a key used only for this site
- `CLIENT_SITE` and `COUNTY_WIKI`: the same values as the crawl command above. The knowledge base is built from the public website during each deploy (about 3 minutes), so no crawled content is stored in the repository.
- `ACCESS_CODE`: optional, see above

Notes:

- The free plan sleeps after 15 minutes without traffic and takes about a minute to wake. Switch to a paid plan for the presentation week.
- Render's file system is reset on each deploy, so the interaction log there starts empty after every deploy. Download it before redeploying if you need it for the needs report.

# Synthetic demo data

A made-up organization, the Riverbend Council of Governments (`riverbend.example.org`), so that `demo.py` runs without crawling a real website. The `.example.org` addresses can't belong to anyone. The people, towns, numbers and the staffing study are invented.

The files use the same format the crawler (`member_assistant/crawl.py`) writes to `private/`:

- `kb/documents.jsonl`: six pages and one report PDF (given as a list of page texts). `demo.py` splits them into retrieval chunks with `kb.chunk_document`, as the crawler does.
- `profile.json`, `contacts.json`, `programs.json`: what the tools look up.
- `municipalities.json`: the town list the redactor uses to turn "Millbrook" into `[municipality: small borough]`.
- `site_issues.json`: one broken link, so the link check has something to catch.

To keep the data realistic, the pages copy the structure of a real membership organization's site: program pages with a stage of engagement and a contact line, an About page that explains membership without listing dues, and a report linked from a program page. The dues amount is deliberately missing, as it is on the real site, so the demo can show the assistant saying so instead of inventing one.

---
title: Website Assistant
emoji: 💬
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
short_description: Agentic AI assistant for CONNECT's public website
---

# Website assistant (prototype)

A course project prototype from CMU Heinz College (Introduction to AI, Fall 2026, Team 18), built for CONNECT (the Congress of Neighboring Communities), which gave permission to be named. Open the app to see CONNECT's public website with a chat assistant in the bottom-right corner. The assistant answers from the site's published pages and reports, cites its sources, points visitors to the right staff contact, and logs requests for staff. It runs on Claude with guardrails for emergencies, prompt injection and personal data.

This Space is deployed from the team's GitHub repository by `deploy/hf_space.py`; see `docs/hosting.md` there. Don't edit files here directly, because the next deploy replaces them.

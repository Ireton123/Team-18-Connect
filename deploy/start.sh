#!/bin/sh
# Container entry point. Normally the knowledge base was built with the image; if it wasn't
# (no build secret), crawl the website now, which takes about 3 minutes.
set -e
if [ ! -f "${MEMBER_ASSISTANT_PRIVATE_DIR:-private}/kb/chunks.jsonl" ]; then
  if [ -z "$CLIENT_SITE" ]; then
    echo "No knowledge base and no CLIENT_SITE: set CLIENT_SITE (and COUNTY_WIKI) as Space secrets." >&2
    exit 1
  fi
  python -m member_assistant.crawl --site "$CLIENT_SITE" --county-wiki "$COUNTY_WIKI"
fi
exec uvicorn member_assistant.web:create_app --factory --host 0.0.0.0 --port "${PORT:-7860}"

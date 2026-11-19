# syntax=docker/dockerfile:1
# Container for the website assistant, used by the Hugging Face Space (see docs/hosting.md).
#
# The knowledge base is crawled from the public website while the image builds, using the
# CLIENT_SITE and COUNTY_WIKI build secrets, so no crawled content is stored in the repository.
# Locally:
#   CLIENT_SITE=https://... COUNTY_WIKI="..." docker build \
#     --secret id=CLIENT_SITE,env=CLIENT_SITE --secret id=COUNTY_WIKI,env=COUNTY_WIKI -t member-assistant .
#   docker run -p 7860:7860 -e ANTHROPIC_API_KEY member-assistant
FROM python:3.12-slim

# Hugging Face runs the container as user 1000.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PORT=7860 \
    TRUST_PROXY_HEADERS=1
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user member_assistant member_assistant
COPY --chown=user deploy/start.sh deploy/start.sh

RUN --mount=type=secret,id=CLIENT_SITE,mode=0444,required=false \
    --mount=type=secret,id=COUNTY_WIKI,mode=0444,required=false \
    if [ -s /run/secrets/CLIENT_SITE ]; then \
      python -m member_assistant.crawl --site "$(cat /run/secrets/CLIENT_SITE)" \
        --county-wiki "$(cat /run/secrets/COUNTY_WIKI 2>/dev/null)"; \
    else \
      echo "No CLIENT_SITE build secret: the website will be crawled when the container starts."; \
    fi

EXPOSE 7860
CMD ["sh", "deploy/start.sh"]

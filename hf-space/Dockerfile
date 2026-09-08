# broker-branch-tracker — full ingest (TWSE + TPEx) on a Hugging Face Space.
# GitHub Actions IPs are rate-limited by TWSE BSR and hard-blocked by TPEx's
# Cloudflare edge; a normal HF Space IP is neither. GitHub Actions only pings
# POST /run once per trading day to kick this off.
FROM python:3.13-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HOME=/app \
    CODE_REPO=https://github.com/jjerry0519/broker-branch-tracker \
    GH_REPO=jjerry0519/broker-branch-tracker-data

RUN apt-get update && apt-get install -y --no-install-recommends \
      git curl ca-certificates xvfb \
    && rm -rf /var/lib/apt/lists/*

# gh CLI (storage.py uploads release assets with it)
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update && apt-get install -y gh && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# app deps
COPY requirements.txt /app/space-requirements.txt
RUN pip install --no-cache-dir -r /app/space-requirements.txt

# the pipeline itself (ingest/, reference.db, its requirements) — cloned at build.
# Rebuild the Space (empty commit) to pick up ingest/ changes.
RUN git clone --depth 1 "$CODE_REPO" /app/src \
 && pip install --no-cache-dir -r /app/src/requirements.txt \
 && python -m patchright install --with-deps chromium \
 && cp -r /app/src/ingest /app/ingest \
 && cp /app/src/reference.db /app/reference.db \
 && cp /app/src/conftest.py /app/conftest.py || true

COPY app.py run_once.sh /app/
RUN chmod +x /app/run_once.sh

# HF Spaces route external traffic to $PORT (default 7860)
ENV PORT=7860
EXPOSE 7860
CMD ["python", "app.py"]

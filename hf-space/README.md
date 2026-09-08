---
title: Broker Branch Ingest
emoji: 📈
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---

# broker-branch-tracker — ingest worker

Runs the full daily TWSE + TPEx broker-branch scrape from a normal cloud IP
(GitHub Actions IPs are rate-limited by TWSE BSR and hard-blocked by TPEx's
Cloudflare edge). Uploads Parquet to the private data repo
`jjerry0519/broker-branch-tracker-data` as GitHub Releases assets.

GitHub Actions (`.github/workflows/trigger_space.yml` in the code repo) fires
`POST /run` once per trading day (~17:45 Asia/Taipei) and also pings `GET /`
every few hours so the free Space never pauses.

## Space secrets (Settings → Variables and secrets)

| name | value |
|---|---|
| `DATA_REPO_TOKEN` | a GitHub PAT with `repo` (contents write) on `broker-branch-tracker-data` |
| `RUN_TOKEN` | any long random string; the same value goes in the code repo's `SPACE_RUN_TOKEN` secret |

The Dockerfile maps `DATA_REPO_TOKEN` → `GH_TOKEN` at runtime (see Space
variable `GH_TOKEN=${DATA_REPO_TOKEN}` — set it as a **variable** referencing the
secret, or just name the secret `GH_TOKEN` directly).

## Endpoints

- `GET /` — health / keep-alive
- `GET /status` — last run rc, timing, last 40 log lines
- `POST /run` (header `X-Token: <RUN_TOKEN>`, optional `?date=YYYY-MM-DD&force=1`) — start one ingest in the background

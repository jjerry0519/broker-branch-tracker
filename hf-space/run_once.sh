#!/usr/bin/env bash
# One full ingest pass (TWSE + TPEx), writing/uploading via the pipeline's own
# logic. Env expected: GH_TOKEN (write access to the data repo), GH_REPO.
set -o pipefail
cd /app
export TPEX_ENABLED="${TPEX_ENABLED:-1}"
export GH_REPO="${GH_REPO:-jjerry0519/broker-branch-tracker-data}"

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
echo "[$(ts)] ingest start (TPEX_ENABLED=$TPEX_ENABLED)"

xvfb-run -a python -m ingest.run "$@"
rc=$?

echo "[$(ts)] ingest finished rc=$rc"

# Mirror manifest.json + logs into the data repo as a 'meta' release so the
# web app has a single private place to read them (the code repo is public).
if [ -f manifest.json ] && [ -n "$GH_TOKEN" ]; then
  gh release view meta --repo "$GH_REPO" >/dev/null 2>&1 \
    || gh release create meta --repo "$GH_REPO" --title meta --notes "manifest + logs" >/dev/null 2>&1
  gh release upload meta manifest.json --repo "$GH_REPO" --clobber || true
  [ -f logs/ingest_log.jsonl ] && gh release upload meta logs/ingest_log.jsonl --repo "$GH_REPO" --clobber || true
  echo "[$(ts)] mirrored manifest/logs to $GH_REPO :: meta"
fi
exit $rc

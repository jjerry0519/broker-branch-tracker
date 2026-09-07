# Progress Ledger — broker-branch-tracker pipeline

Plan: docs/superpowers/plans/2026-09-03-broker-branch-tracker-pipeline.md
Base commit (branch start): 4e50b1e

Task 1: complete (commits 1653318..5f8714c, review clean ✅)
  - venv Python 3.13.7 = portable interp at C:\Users\user\OneDrive\桌面\CB\codex券報查詢\python313 (works; keep).
  - Playwright bundled node v22 segfaults on this machine; local runs need PLAYWRIGHT_NODEJS_PATH=<system node v24>. CI (ubuntu) unaffected. Applies to Task 8 + its integration test only.
  - deps pinned: httpx 0.27.2 / ddddocr 1.6.1 / playwright 1.49.1 / bs4 4.12.3 / lxml 5.3.0 / pandas 2.2.3 / pyarrow 18.1.0 / pytest 8.3.3 / duckdb 1.1.3
Task 2: complete (commit ff3350d, review clean ✅)
Task 3: complete (commit a875ec0, review clean ✅ — minor: test lacks branch_name propagation assertion, add in final review)
Task 4 fixture: tests/fixtures/bsr_sample.csv (real 2330, 2026-09-03, utf-8-BOM 289KB).
Task 6 fixtures: 6 verified captcha PNGs (filename=answer).
PLAN CORRECTIONS (real-site verified 2026-09-03): BSR CSV is utf-8-sig NOT big5; no date/close in CSV (close comes from universe.Stock); 11-col doubled rows idx5 empty; broker cell = cell[:4]+name NO delimiter, case-sensitive (9B2z vs 9B2Z); must follow #HyperLink_DownloadCSV href. Briefs 4/6/7/11 regenerated.
Task 4: complete (commit b8d0c5d, review ✅ — minors: silent-empty on bad input [handled at client layer], 2-pass csv parse style). _split_broker uses " ".join(cell[4:].split()) — brief reference code had a bug, this is the fix.

Task 5 fixture: tests/fixtures/tpex_sample.json (real 6488 brokerBS, 2026-09-03). Structure: {stat:"ok", tables:[summary(fields incl 收盤價), detail(fields=[序號,券商,價格,買進股數,賣出股數], ~13483 rows)]}. Broker cell HAS space "1020 合庫".
Task 8 REWRITE: TPEx POST needs cf-turnstile-response=<token> in body; token single-use; headless got no token, need headed Playwright. New iface: TpexBrowser context manager, token-per-request via page.evaluate, sequential only. Task 11 imports/orchestration updated (TpexBrowser, workers=1 for tpex leg).
OPEN DECISION (defer to after Task 8 integration test measures real TPEx timing): TPEx sequential token-per-request ~= 65-110 min/day -> monthly ~2100 min may exceed 2000 free private-repo Actions minutes. Options: self-hosted runner / public-code-repo+Drive-data / accept overage. Decide with real numbers post-Task-8.
Task 5: complete (commit ee474cc, review ✅ — minors: _num→0.0 on "--" [handled: Task11 _tpex_one "close = page.close_price or s.close_price" falls back to OpenAPI], bare subscription on tables). No fixture-value edits needed.
Task 6: complete (commit 3bb7f48, review ✅ no issues, 100% captcha sample hit rate).
Task 7: complete (commit 4cab86b, review ✅ — minors: no raise_for_status, guid AttributeError not wrapped in BsrError, retry-count not asserted; all brief-inherited). Live fetch_stock verified: 2330 6104 rows 1 captcha attempt 3.1s.
BLOCKER FOUND: import pyarrow BEFORE onnxruntime -> DLL load fail on this Windows box. Fix pending (conftest.py + ingest/__init__.py import onnxruntime first). Currently 2 test_captcha failures in full-suite runs.
Task 7b: DLL-order fix committed bea8501 (conftest.py + ingest/__init__.py import onnxruntime first). Full suite green 21 passed.
Task 8: code complete (commit f7f0c5f — build_body pure + TpexBrowser headed Playwright, token-per-request via page.evaluate). Unit: 3 passed. Full suite: 24 passed 0 failed. Review pending.
  MILESTONE-1 INTEGRATION: **FAILED — hard-blocked, N=0 stocks.** Cloudflare Turnstile never issues the first token; repeating client `[Cloudflare Turnstile] Error: 600010.`; `cf-turnstile-response` value stuck "" for 125s; widget stuck on "正在驗證…". Dies in __enter__/reload() (wait_for_function 45s timeout) before any stock query. So: NO per-stock timing, NO reload cadence.
  Verified NOT a local env fault: headed renders fine, PLAYWRIGHT_NODEJS_PATH=v24 works, no segfault. Identical block with (a) bundled Chromium headed, (b) real Chrome channel=chrome + --disable-blink-features=AutomationControlled + JS stealth. Root cause = Turnstile runtime detection of Playwright's CDP fingerprint, not IP. Brief's 2026-09-03 "headed got a token" no longer holds as of 2026-09-07 (CF detection update or TPEx Turnstile-config tighten).
  CONSEQUENCE: "TPEx leg -> self-hosted runner" (brief fallback) is necessary but NOT sufficient — my mitigation run was already on residential IP. vanilla-Playwright TpexBrowser is dead. Recommend: (1) nodriver spike (non-CDP driver, same flow), fallback (2) manual 1-click Turnstile solve + connect_over_cdp on self-hosted runner, (3) check TPEx OpenAPI/flat CSV for a non-gated brokerBS-equivalent, (4) ship TWSE-only for M1 (TPEX_ENABLED default flip). ingest/tpex_client.py stays as the request-mechanics reference; only the browser launch/keepalive layer needs replacing.
  OPEN DECISION (TPEx Actions-minutes cost) is moot for the GH-hosted path — TPEx can't run there at all. "~65-110 min/day" estimate still unverified. Full report: .superpowers/sdd/task-8-report.md
Task 8: review = NEEDS FIXES. (a) __enter__ leaks browser+driver on partial failure [Important]; (b) no browser-free retry test [Minor]; (c) COORDINATOR ADD: vanilla playwright blocked by Turnstile 600010 -> swap to patchright (spikes 7/8, 5/5 from residential IP; see tpex_spike_evidence.txt). reload-per-query ~7s/stock ~= 1.5-2h for OTC. OPEN: patchright from GH-Actions datacenter IP unverified -> decide at Task 14.

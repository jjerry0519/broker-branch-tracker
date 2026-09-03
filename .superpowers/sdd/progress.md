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

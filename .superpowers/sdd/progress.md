# Progress Ledger — broker-branch-tracker pipeline

Plan: docs/superpowers/plans/2026-09-03-broker-branch-tracker-pipeline.md
Base commit (branch start): 4e50b1e

Task 1: complete (commits 1653318..5f8714c, review clean ✅)
  - venv Python 3.13.7 = portable interp at C:\Users\user\OneDrive\桌面\CB\codex券報查詢\python313 (works; keep).
  - Playwright bundled node v22 segfaults on this machine; local runs need PLAYWRIGHT_NODEJS_PATH=<system node v24>. CI (ubuntu) unaffected. Applies to Task 8 + its integration test only.
  - deps pinned: httpx 0.27.2 / ddddocr 1.6.1 / playwright 1.49.1 / bs4 4.12.3 / lxml 5.3.0 / pandas 2.2.3 / pyarrow 18.1.0 / pytest 8.3.3 / duckdb 1.1.3

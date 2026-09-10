# broker-branch-tracker

每交易日盤後，從 **SysJust `.djhtm` 聚合器**（MoneyDJ／富邦 e 財網等券商白牌鏡像）抓取
**全部上市（TWSE）＋ 全部上櫃（TPEx）** 個股的 **券商分點** 買賣資料，落地成每日 Parquet，
上傳到私有資料 repo `jjerry0519/broker-branch-tracker-data` 的 GitHub Releases（按月分包
`data-YYYY-MM`），滾動保留 13 個月。純 `httpx`、無瀏覽器、無驗證碼 —— **電腦不需要開機**。

查詢網頁（個股籌碼排行 / 個股 × 分點 FIFO 成本 / 分點總覽）為 Plan 2，另案處理，只讀我們的庫。

> 架構演進：官方來源（TWSE BSR、TPEx brokerBS）在 GitHub-Actions runner IP 上被限速／被
> Cloudflare 封鎖，無法滿足「免費＋不綁卡＋不開電腦」。改採 `.djhtm` 聚合器後三者同時成立。
> 官方來源的實作（圖形驗證碼、Turnstile 瀏覽器）保留在 `fallback/`，供所有鏡像失效時啟用。
> 詳見 `docs/specs/2026-09-03-broker-branch-tracker-design.md` §「v2 — 架構變更」。

---

## 資料來源

| 端點（鏡像 `fubon-ebrokerdj.fbs.com.tw` 為主，`moneydj.com` 等為備援） | 回傳 | 用途 |
|---|---|---|
| `…/z/zc/zco/zco0/zco0.djhtm?A=<股>&BHID=<分點>&b=<分點>&C=1&D=<起ISO>&E=<迄ISO>` | 該（股×分點）**逐日**買/賣/買賣超（**張**），一次請求涵蓋任意日期範圍、可回溯約 15 個月，末列附 `期間累計買賣超張數` 檢查碼（`djhtm_parse` 會驗證列和相符） | 權威每日資料、歷史回補、Plan 2 的功能 2/3 |
| `…/z/zc/zco/zco.djhtm?a=<股>` | 最新一交易日的 **前 15 買 + 前 15 賣**（分點中文名、無代號）＋ 站方自算 `平均買超成本`／`平均賣超成本` | 每日榜單掃描 → `latest.parquet`；Plan 2 的功能 1 排行 |
| TWSE OpenAPI `exchangeReport/STOCK_DAY_ALL` | 當日有成交的上市股清單 ＋ 各檔收盤價 ＋ **最新交易日**（非交易日守門） | `ingest/universe.py` |
| TPEx OpenAPI `tpex_mainboard_daily_close_quotes` | 當日有成交的上櫃股清單 ＋ 收盤價 | `ingest/universe.py` |
| TWSE OpenAPI `opendata/OpenData_BRK02` ＋ `brokerService/brokerList` | `broker_branch` 參考表（`branch_id` 即 `BHID`；`parent_id = code[:3]+"0"`） | `ingest/reference.py`，每週一更新 `reference.db` |

**單位轉換**：`.djhtm` 以「張」計，落地時 ×1000 存為股數，`schema.BranchFlow`
（`date, market, stock_id, branch_id, branch_name, buy_shares, sell_shares, net_shares, close_price`）
欄位不變。`net_shares = buy_shares − sell_shares`。歷史列 `close_price` 為 `0.0`（Plan 2 查詢時另接收盤價補齊）。
唯一鍵：`(date, stock_id, branch_id)`。

---

## Architecture A — 20 天滾動分片

私有庫落地 **完整** 的 全市場 × 全分點 × 逐日矩陣，**不做 Top-N 裁切**。矩陣約
1,900 股 × 880 分點 ≈ 167 萬格，實測任一 60 天窗口內幾乎沒有「從未交易」的組合，
所以不能每天全抓（會被鏡像封）。做法：

- **每日 `zco` 榜單掃描**：每檔股票 1 次請求 → 當日前 15 買／前 15 賣 → `latest.parquet`（覆寫）。
  每檔最大 30 名分點**當日**即進庫，零延遲。
- **滾動 `zco0` 分片**：`schedule.next_shard()` 每次取 1/`CYCLE_DAYS`（預設 20）的股票，
  對其每一個分點打一次 `zco0` **區間**請求，範圍 = `[該股上次補到的日+1 .. 最新交易日]`。
  因為是區間查詢，一次補滿中間每一個交易日、**零缺口**；跑完一圈 20 天剛好每格輪到一次。
  最冷門格子的「最新一天」最多延遲 `CYCLE_DAYS` 天。
- **查詢即時補抓**（Plan 2）：網頁打開某股／某組合時，若庫裡該格過期就當場打一次 `zco0`
  （1–2 秒）補到最新再顯示。使用者碰到的東西永遠是最新的。
- **一次性回補**：`--mode backfill --shard i/N`（建議 N≈24），每片對其負責的股票 × 全分點
  各打一次 15 個月的 `zco0`，寫入**該片專屬**的 `<date>__bfs<i>-<N>.parquet` staging 檔
  （不下載合併 → 多片可並行、互不覆寫），每 10 檔 checkpoint 一次。全部跑完後跑一次
  `--mode compact`（`compact.yml`）把 staging 併進正式 `<date>.parquet`、刪除 staging。

`refresh_state.json`（`{stock_id: 補到的日}`）與 `manifest.json`、`logs/` 每日提交回程式碼 repo。

---

## Repo 佈局

```
broker-branch-tracker/
├── ingest/                    # 每日路徑（純 httpx，CI 會裝的只有 requirements.txt）
│   ├── djhtm_parse.py         # zco0 / zco 的 BIG5 結構化解析（含檢查碼驗證）
│   ├── djhtm_client.py        # 多鏡像 failover 的 httpx 客戶端
│   ├── schedule.py            # 20 天滾動分片：next_shard / window_for / mark_done（純函式）
│   ├── schema.py              # BranchFlow、PARQUET_SCHEMA
│   ├── universe.py            # twse_traded / tpex_traded / resolved_trading_date
│   ├── reference.py           # reference.db 更新 ＋ load_branches（分點軸）
│   ├── storage.py             # write_parquet / download_asset / read_flows / dedup_flows /
│   │                          #   write_latest / upload_assets / prune_old_releases / manifest
│   └── run.py                 # orchestrator：守門 → zco 掃描 → zco0 分片 → 合併上傳 → 清理
├── fallback/                  # 官方來源備援（所有 .djhtm 鏡像失效時才用）
│   ├── twse_client.py / twse_parse.py / captcha.py   # BSR：ddddocr 圖形驗證碼
│   ├── tpex_client.py / tpex_parse.py                # brokerBS：patchright 過 Turnstile
│   └── aggregate.py
├── tests/                     # 純函式 / mock transport；tests/fallback/ 需 requirements-fallback.txt
├── docs/specs/ · docs/superpowers/plans/
├── .github/workflows/
│   ├── daily_ingest.yml       # cron 週一~五 18:30 台灣時間 ＋ workflow_dispatch
│   └── backfill.yml           # workflow_dispatch，--shard i/N
├── reference.db               # company / broker_branch（提交進 git，每週更新）
├── manifest.json · refresh_state.json
└── requirements.txt / requirements-fallback.txt
```

---

## 執行

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt          # httpx / pyarrow / duckdb / pytest

# 單日（本機測試，不上傳；--stock-limit 縮小範圍）
.venv/Scripts/python -m ingest.run --mode daily --repo "" --stock-limit 5

# 正式：上傳到私有資料 repo 當月 Release、更新 manifest、清理逾期
GH_REPO=jjerry0519/broker-branch-tracker-data GH_TOKEN=<PAT> \
  .venv/Scripts/python -m ingest.run --mode daily

# 一次性回補（分 24 片，dispatch backfill.yml 各片；跑完再 dispatch compact.yml）
.venv/Scripts/python -m ingest.run --mode backfill --shard 1/24 --months 15
.venv/Scripts/python -m ingest.run --mode compact          # 併 staging → 正式逐日檔

# 測試
.venv/Scripts/python -m pytest                          # 核心（tests/fallback 自動 skip）
.venv/Scripts/pip install -r requirements-fallback.txt  # 要跑 fallback 測試才需要
```

非交易日：`resolved_trading_date()` 取到的最新交易日已在 manifest 且 `sweep_done` → 寫一筆
`skipped` log 後正常結束（exit 0）。

Secrets：`daily_ingest.yml` / `backfill.yml` 用 repo secret `DATA_REPO_TOKEN`（對資料 repo 有
`contents:write` 的 PAT）→ 對映到 `GH_TOKEN`；程式碼 repo 的 manifest 提交用內建 `GITHUB_TOKEN`。
**README 與 repo 內不放任何 token。**

---

## FIFO 平均成本 — 免責聲明（Plan 2）

> 此為簡化估算：以每日買賣超 × 當日收盤價作為交易紀錄，並以 FIFO（先進先出）配對計算。
> 券商分點買賣超為該分點多個帳戶淨額相抵後的結果，無法反映真實單筆成交價格，也看不到本工具
> 涵蓋期間之前既有的部位。此數字僅供相對比較與趨勢觀察，**非真實成交均價**。

`.djhtm` 的 `zco0` 提供逐日買賣張數但不含每日收盤價；Plan 2 會另接 TWSE/TPEx 歷史收盤價
（或 `zco` 的站方均價估值）補齊 FIFO 所需價格。回補涵蓋約 15 個月，`had_oversell` 旗標仍會標示
期初部位造成的偏差。

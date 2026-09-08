# broker-branch-tracker

每交易日盤後，自動抓取**全部上市（TWSE）＋ 全部上櫃（TPEx）**個股的**券商分點**買賣明細，
依分點加總、併入收盤價、算出每日買賣超，寫成兩檔 Parquet（`YYYY-MM-DD_twse.parquet`、
`YYYY-MM-DD_tpex.parquet`），上傳到本 repo 的**私有 GitHub Releases**（按月分包
`data-YYYY-MM`），滾動保留 13 個月。git 歷史只留程式碼、`manifest.json`、參考表
`reference.db`；事實資料一律走 Releases assets，repo 本體恆為數 MB。查詢網頁（個股籌碼 /
個股 × 分點 FIFO 成本 / 分點總覽）為 Plan 2，另案處理。

來源條款（TWSE、TPEx 均載明「此資料不得逕自散布或販售」）→ **repo 設為 private**，
資料放私有 Releases，網頁只呈現加工後視圖（買賣超排名、FIFO 估算成本），不提供原始分點表整批下載。

---

## 資料來源與欄位對照

寫入每日 Parquet 的每一欄（schema：`date, market, stock_id, branch_id, branch_name,
buy_shares, sell_shares, net_shares, close_price`；唯一鍵語意 `(date, market, stock_id, branch_id)`）：

| 欄位 | 來源 |
|---|---|
| `buy_shares` / `sell_shares` / `branch_id` / `branch_name` | **TWSE**：券商買賣證券日報表查詢系統 `bsr.twse.com.tw/bshtm/`（逐檔查詢，5 碼圖形驗證碼，下載檔為 **UTF-8-BOM CSV**，非 BIG5；`券商` 欄格式 `<4碼代號><空白><名稱>`，左右兩組明細併排都要解析，依 `branch_id` 跨所有成交價加總）。**TPEx**：`tpex.org.tw/.../brokerBS` JSON 端點（逐檔查詢，前置 **Cloudflare managed Turnstile**；`券商` 欄格式 `<代號><空白><名稱>`）。 |
| `close_price` | **TWSE**：**不在 BSR CSV 內** → 取自 `universe.Stock`（TWSE OpenAPI `STOCK_DAY_ALL`）。**TPEx**：取自 brokerBS 回應的 summary 表（`收盤價`），缺漏時退回 `universe.Stock`。 |
| 當日「有成交」股票清單 | **TWSE**：OpenAPI `exchangeReport/STOCK_DAY_ALL`。**TPEx**：OpenAPI `tpex_mainboard_daily_close_quotes`。（只對清單內股票查分點，避免對停牌 / 無量股空打。） |
| `company` 參考表（`stock_id, name, market`） | TWSE OpenAPI `opendata/t187ap03_L` ＋ TPEx OpenAPI `tpex_mainboard_daily_close_quotes`。 |
| `broker_branch` 參考表（`branch_id, branch_name, parent_broker_id, parent_broker_name`） | TWSE OpenAPI `opendata/OpenData_BRK02`（分公司）＋ `brokerService/brokerList`（總公司）。`parent_id` 規則：`code[:3] + "0"`。<br>*註：設計文件所載 `www.twse.com.tw/rwd/zh/brokerService/brokerList?response=json` 已失效，上述為可用替代端點。* |

`net_shares = buy_shares - sell_shares`（本專案計算）。明細列不含日期，日期＝查詢日（與來源檔頭「交易日期」比對校驗）。

---

## 反機器人處理

### TWSE — 5 碼圖形驗證碼
- 由 `ingest/captcha.py` 以 **`ddddocr`**（內建 ONNX 權重）在本機直接辨識，首次命中率約 75–100%。
- `twse_client.fetch_stock()` 內建重試迴圈（每次換新 `guid`，至多 30 次），辨識失敗不阻塞當日其他股票。
- TWSE leg 以 `httpx` 純 HTTP 並發 8 worker，每檔為獨立 session。約 1,000 檔，實測 ~10–15 分／日。

### TPEx — Cloudflare managed Turnstile
- **一般 Playwright 會被偵測並封鎖**（回 `Error: 600010`，`cf-turnstile-response` 永遠拿不到 token）。
  根因是 Turnstile runtime 偵測 Playwright 的 CDP 指紋，與 IP 無關；真實 Chrome channel ＋ stealth flag 同樣被擋。
- 改用 **`patchright`**（stealth Playwright 的 drop-in 取代）：開一個 headed Chromium，
  每檔股票用一次 page reload 換一枚全新的 Turnstile token，序列查詢（`workers=1`）。
- 住宅 IP 實測：多檔測試 7/8、5/5 通過；約 7 秒／檔 → 全 OTC（約 875 檔）約 1.5–2 小時／日。
- **未驗證**：`patchright` 從 GitHub-Actions 資料中心 IP 是否也能通過 Turnstile，尚無實測資料，
  由 Task 14 的第一次 CI 執行決定。`ingest/tpex_client.py` 的請求機制（body 需帶 `cf-turnstile-response`、token 單次有效）維持為參考實作，只有 browser 啟動 / keepalive 層可能需要換掉。

---

## 免費額度與 Task 14 的 A/B 決策

| 資源 | 免費額度 | 本專案用量 | 風險 / 對策 |
|---|---|---|---|
| GitHub Actions（private repo） | 2,000 分鐘／月 | TWSE leg ~10–15 分／日（約 4 小時／月） | 充裕。 |
| GitHub Actions（**TPEx leg**） | 同上 | ~1.5–2 小時／日 ≈ **~40 小時／月** | **若跑在 GitHub-hosted runner 會超出免費上限。** 見下方 A/B。 |
| GitHub Releases 儲存 | 「合理使用」 | Parquet ~5.5 GB／年 ＋ raw 近 30–60 天，合計數 GB | 低。13 個月滾動刪除維持有界。 |
| GitHub API（Streamlit 下載 asset） | 5,000 req/hr（PAT） | 個人查詢極少 | 低。`st.cache_data` 快取。 |
| Streamlit Community Cloud（Plan 2 網頁） | 1 GB RAM、閒置休眠、免費 1 個私有 app | DuckDB 查數個 ~22 MB Parquet | 冷啟動 ~30 秒可接受。 |
| 資料來源條款 | — | — | 「不得散布」→ repo private、資料在私有 Releases、網頁只呈現加工視圖、不提供原始整批下載。 |

**TPEx leg 的月分鐘數超標，Task 14 需二選一（尚未定案）：**

- **方案 A — 程式碼公開 repo ＋ 資料改存 Google Drive。**
  公開 repo 的 Actions 分鐘數無上限；原始分點資料不進公開 repo，改推 Google Drive（維持「不對外散布」）。
- **方案 B — private repo ＋ 混合 runner。**
  TPEx leg 跑在 self-hosted runner（需求方本機、住宅 IP，`patchright` 已確認可用），
  本機執行不計 GitHub 分鐘；TWSE leg 與其餘流程仍跑雲端 GitHub-hosted runner。
  workflow 以 `TPEX_ENABLED` 環境變數（`"0"` 等同 `--skip-tpex`）切換雲端那半是否含 TPEx。

---

## 保留策略

- 每日資料：按月 Release `data-YYYY-MM`（`gh release upload ... --clobber`）。
- 每日 job 執行 `prune_old_releases()`：刪除約 **13 個月前**的 `data-YYYY-MM`（含 tag，`--cleanup-tag`），
  維持約 12–13 個月滾動窗口。
- 原始回應（CSV / JSON，gzip）留在 `raw-YYYY-MM`，只保留 **2 個月分包（約 30–60 天）**，僅供近期抽查；
  Parquet 為正式紀錄。
- `manifest.json`（提交進 git）為可用日期索引：每日新增當日 entry，並移除 13 個月前的 entry。

---

## Repo 佈局

```
broker-branch-tracker/
├── ingest/
│   ├── __init__.py        # Windows DLL 順序守門：先 import onnxruntime 再讓 pyarrow 載入
│   ├── schema.py          # BranchFlow、PARQUET_SCHEMA、flows_to_table
│   ├── aggregate.py       # 券商×價位列 → 分點×日彙整、併收盤價、算 net
│   ├── twse_parse.py      # BSR CSV 解析 → BsrPage
│   ├── tpex_parse.py      # brokerBS JSON 解析 → BrokerBsPage
│   ├── captcha.py         # ddddocr 驗證碼辨識、looks_valid()
│   ├── twse_client.py     # BSR 流程：GET 表單 → 辨識驗證碼 → POST → GET CSV（httpx，重試）
│   ├── tpex_client.py     # brokerBS 請求機制（token 單次有效）＋ TpexBrowser（patchright headed）
│   ├── universe.py        # twse_traded()、tpex_traded()、resolved_trading_date()（非交易日偵測）
│   ├── storage.py         # write_parquet()、upload_release()、prune_old_releases()、manifest 合併
│   ├── reference.py       # 每週更新 company / broker_branch → reference.db
│   └── run.py             # orchestrator 進入點：非交易日偵測 → 兩 leg → 寫檔 → 上傳 → 清理 → manifest
├── tests/
│   ├── fixtures/          # 擷取自真站台的回應樣本（BSR CSV、brokerBS JSON、驗證碼 PNG 等）
│   └── test_*.py          # 單元測試（純函式、mock transport）；標 @pytest.mark.integration 者打真站台
├── docs/specs/            # 設計文件（2026-09-03-broker-branch-tracker-design.md）
├── .github/workflows/     # daily_ingest.yml — cron（台灣 18:00，週一~五）＋ workflow_dispatch（Task 14 產出）
├── reference.db           # 參考表 SQLite（company、broker_branch），提交進 git，每週更新
├── manifest.json          # 可用日期索引，提交進 git，每次執行更新（首次執行時生成）
├── conftest.py / pytest.ini
└── requirements.txt
```

---

## 執行

### 環境設定（Windows，Python 3.13）

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
# TPEx leg 需要 patchright 的 Chromium（沿用 Playwright 的下載機制）
.venv/Scripts/python -m patchright install chromium
```

本機跑 TPEx leg（`patchright` / Playwright）時，需指向系統 Node（bundled node v22 在部分 Windows 機器 segfault）：

```bash
export PLAYWRIGHT_NODEJS_PATH="C:/Program Files/nodejs/node.exe"
```

> Ubuntu CI runner 上以上皆不需要：無 DLL 順序問題、`python -m playwright install --with-deps chromium` 即可。

### 單日抓取

```bash
# 本機測試：抓當日、寫 out/，不上傳（無 GH_REPO 時 prune / upload 略過）
.venv/Scripts/python -m ingest.run --date 2026-09-03

# 只跑 TWSE leg（跳過 TPEx；等同環境變數 TPEX_ENABLED=0）
.venv/Scripts/python -m ingest.run --date 2026-09-03 --skip-tpex

# 指定 repo → 上傳當月 Release、更新 manifest、清理逾期 Release
GH_REPO=jjerry0519/broker-branch-tracker .venv/Scripts/python -m ingest.run --date 2026-09-03
```

非交易日（`resolved_trading_date()` 回傳的站台日期 ≠ `--date`）：寫一筆 `skipped` log 後正常結束（exit 0）。

### 測試

```bash
.venv/Scripts/python -m pytest                  # 單元測試（CI 必跑，預設排除 integration）
.venv/Scripts/python -m pytest -m integration   # 打真實 TWSE / TPEx / GitHub，手動執行
```

Secrets：GitHub Actions 用內建 `GITHUB_TOKEN`（`contents: write`）管理 Release 與提交 manifest；
Plan 2 的 Streamlit 需另存 `GH_PAT`（`repo` scope，讀私有 Release asset）與 `GH_REPO`。
**README 與 repo 內不放任何 token。**

---

## FIFO 平均成本 — 免責聲明

Plan 2 網頁的「個股 × 分點成本」與「分點總覽」頁面，會在每次呈現 FIFO 估算結果時，逐字顯示以下聲明
（規格見設計文件 §6）：

> 此為簡化估算：以每日買賣超 × 當日收盤價作為交易紀錄，並以 FIFO（先進先出）配對計算。券商分點買賣超為該分點多個帳戶淨額相抵後的結果，無法反映真實單筆成交價格，也看不到本工具上線前既有的部位。此數字僅供相對比較與趨勢觀察，**非真實成交均價**。

補充：本工具上線日起累積、不回補歷史，故前 3–6 個月 FIFO 數字偏差較大（期初部位視為 0，上線前已持有的分點會頻繁觸發「超賣捨去」）。UI 會標示「資料起始日」與 `had_oversell` 旗標。

# 台股分點籌碼追蹤 — 設計文件

- 日期：2026-09-03（v1）／2026-09-10（v2 架構變更）
- 狀態：v2 已與需求方確認並實作中
- 目標 repo：`jjerry0519/broker-branch-tracker`（public，程式碼）＋ `jjerry0519/broker-branch-tracker-data`（private，資料）

---

## v2 — 架構變更（2026-09-10）

**為什麼變**：v1 的官方來源（TWSE BSR、TPEx brokerBS）在 GitHub-Actions runner IP 上都跑不動 —— BSR 每個 IP 約 20 檔就被限速，TPEx 的 Cloudflare 邊緣直接封鎖整個 GitHub ASN。HF Docker Space 改成要付費方案。所有「免費＋不綁卡＋不開電腦」的方案逐一失敗。

**新來源**：SysJust `.djhtm` 聚合器（MoneyDJ／富邦 e 財網等數十個券商白牌鏡像），純 Microsoft-IIS、無 Cloudflare、無登入、無驗證碼，`httpx` 直接抓，且**從 GitHub runner IP 實測可通**（富邦鏡像 8/8）。

| 端點 | 回傳 | 用途 |
|---|---|---|
| `zco0.djhtm?A=<股>&BHID=<分點>&b=<分點>&C=1&D=<起>&E=<迄>` | 該（股×分點）**逐日** 買/賣/買賣超（張），一次請求涵蓋任意日期範圍、可回溯 ~15 個月，含 `期間累計` 檢查碼 | 權威每日資料、歷史回補、功能 2/3 |
| `zco.djhtm?a=<股>` | 最新一日 前 15 買 + 前 15 賣（分點中文名、無代號）＋ 站方自算均價成本 | 每日榜單掃描、功能 1 排行 |

**Architecture A（需求方選定）**：私有庫落地**完整** 全市場 × 全分點 × 逐日矩陣，不做 Top-N 裁切。因矩陣達 ~167 萬（股×分點）格、實測幾乎無「死組合」，改用 **20 天滾動分片**：每日只重抓 1/20 的股票（各 ~880 分點），但因 `zco0` 是區間查詢，一次補滿距上次以來每一個交易日、零缺口；最冷門格子的「最新一天」最多延遲 20 天，熱門部分由每日 `zco` 榜單掃描即時補上，且網頁層在使用者查詢時對過期格子即時補抓（1–2 秒）。

**資料主權**：抓進私有 `broker-branch-tracker-data`（Parquet／Releases），網頁只讀我們的庫。任一鏡像若封鎖，保留全部已累積歷史、切換其他鏡像續抓新日。

**單位**：`.djhtm` 以「張」計；落地時 × 1000 存為股數，`schema.BranchFlow` 欄位不變。`close_price` 僅當日 leg 有值，歷史列為 0.0（FIFO 頁面查詢時另接收盤價來源補齊）。

**模組**：`ingest/djhtm_parse.py`、`ingest/djhtm_client.py`（多鏡像 failover）、`ingest/schedule.py`（滾動分片純函式）、`ingest/run.py`（`--mode daily|backfill`）。v1 的 BSR/Turnstile 實作移至 `fallback/`，保留為鏡像全滅時的備援路徑。

以下 §2 起為 v1 內容，來源調查與 `fallback/` 實作仍以其為準。

---

## 1. 目標與範圍

### 要做的
- 每交易日盤後，自動抓取**全部上市（TWSE）+ 全部上櫃（TPEx）**個股的**券商分點**買賣明細，長期累積成資料庫。
- 提供繁體中文網頁：
  1. **個股籌碼**：搜股號/名 → 近 N 日各分點買賣超、前幾大買方/賣方、選定分點的累計買賣超走勢。
  2. **個股 × 分點成本**：選股 + 選分點 + 選區間 → 以 FIFO 估算該分點在區間內的平均持股成本。
  3. **分點總覽**：選分點 + 選區間 → 該分點在區間內**所有**有進出的個股，各列出買賣超、FIFO 成本、估計庫存。

### 不做的（本版）
- 歷史回補。上線日起累積，不往前補（見 §11）。保留匯入接口供日後手動灌歷史。
- 興櫃、權證、ETF 以外的商品分點。
- 即時／盤中資料。純盤後 EOD。
- 投資建議、選股訊號。只呈現資料與衍生計算。

### 硬限制
- **全程免費**：資料來源、儲存、部署、排程皆不得需要付費方案。免費額度上限需在文件與 UI 標明。
- **只保留近 1 年**：超過約 13 個月的資料自動刪除。
- **遵守來源條款**：TWSE / TPEx 皆載明「此資料不得逕自散布或販售」。因此：資料庫與網頁**設為私有／個人自用**，網頁只呈現**加工後**視圖（買賣超排名、FIFO 成本），頁面標注來源與免責，不提供原始分點表格整批下載。

---

## 2. 資料來源調查結果

### 2.1 TWSE — 券商買賣證券日報表查詢系統
- 進入點：`https://bsr.twse.com.tw/bshtm/bsMenu.aspx`（ASP.NET WebForms）。
- **逐檔查詢**，一次一個證券代號。
- 防機器人：**5 碼文數字圖形驗證碼**（`CaptchaImage.aspx?guid=<GUID>`，200×60 px）。可用預訓練 CNN/ONNX 模型辨識（開源專案如 `coldnew/twse-captcha-solver-dl4j`；深度學習版正確率 >90%），失敗重試即可。
- **只提供最近一個交易日**資料，無任何歷史。
- 流程：
  1. `GET bsMenu.aspx` → 解析隱藏欄位 `__VIEWSTATE`、`__VIEWSTATEGENERATOR`、`__EVENTVALIDATION`，及驗證碼圖 `guid`。
  2. `GET CaptchaImage.aspx?guid=<GUID>` → 模型辨識 5 碼。
  3. `POST bsMenu.aspx`（同一 session cookie），欄位：隱藏欄位原值 + `__EVENTTARGET=""` + `__EVENTARGUMENT=""` + `RadioButton_Normal=RadioButton_Normal` + `TextBox_Stkno=<代號>` + `CaptchaControl1=<辨識結果>` + `btnOK=查詢`。
  4. 回應含下載連結 `#HyperLink_DownloadCSV` → `GET bsContent.aspx`（同 session）→ CSV。
- CSV 內容：
  - 檔頭數列：證券代號、交易日期、成交筆數/金額/股數、開/高/低/**收盤價**。
  - 明細：欄位 `序號, 券商, 價格, 買進股數, 賣出股數`，**左右兩組併排**（兩組都要解析）。
  - `券商` 欄格式：`<4碼代號> <名稱>`，例：`1020 合庫`。
  - 每列 = 某分點在某成交價的買/賣股數（**非**分點彙整值）→ 需自行 `group by 分點` 加總。
  - 明細列**不含日期**（日期＝查詢日）。

### 2.2 TPEx — 券商買賣證券日報表查詢系統
- 進入點：`https://www.tpex.org.tw/zh-tw/mainboard/trading/info/brokerBS.html`。
- **逐檔查詢**。
- 防機器人：**Cloudflare Turnstile**（`cf-turnstile-response`）。2026-09-03 實測為 **managed / 非互動式** — 真瀏覽器載入後約 6 秒背景自動通關，無圖框點擊。
- **只提供上櫃證券當日交易資料**，每交易日 16:00 更新。
- 資料端點：`POST https://www.tpex.org.tw/www/zh-tw/afterTrading/brokerBS` → 回 JSON，內含檔頭（含**收盤價**）與明細（`序號 / 券商(代號+名稱) / 價格 / 買進股數 / 賣出股數`），結構與 TWSE CSV 同型。亦有「下載 CSV (BIG5/UTF-8)」端點。
- 實作方式：Playwright + 真 Chromium 載入頁面通過 Turnstile → 取得 `cf_clearance` cookie → 之後以純 HTTP（`requests`/`httpx`）帶該 cookie + 相符 User-Agent 連續查詢多檔。
  - **待驗證（建置里程碑 1 的驗收條件）**：`cf_clearance` 是否可在 ~30 分鐘窗口內重用於多檔查詢。
    - 可 → TPEx 全市場約 3–6 分鐘。
    - 不可（最壞）→ 每檔需重新通關 Turnstile；此時 TPEx leg 改用**需求方本機 self-hosted runner**（住宅 IP，Windows 工作排程器觸發），其餘架構不變。

### 2.3 為何不用第三方
- FinMind `TaiwanStockTradingDailyReport`、FinLab `broker_transactions` 皆為**付費**（FinMind Sponsor NT$999/月起；FinLab VIP 綁 12 個月）。免費層不含分點或只含前 15 大。
- 玩股網 / HiStock / Goodinfo 等皆為再加工 BSR 同一份原始資料，有反爬與條款限制。
- 官方 BSR / brokerBS 已涵蓋全部分點，無需第三方。

### 2.4 欄位來源對照（寫入 `daily_flow` 的每一欄）
| 欄位 | 來源 |
|---|---|
| `date` | 查詢當日（比對來源檔頭「交易日期」確認） |
| `market` | 由抓取來源決定（`twse` / `tpex`） |
| `stock_id` | 查詢輸入；來源檔頭「證券代號」回填校驗 |
| `branch_id`, `branch_name` | 來源明細「券商」欄拆解（代號 + 名稱） |
| `buy_shares`, `sell_shares` | 來源明細「買進股數 / 賣出股數」，依 `branch_id` 跨所有價位加總 |
| `net_shares` | `buy_shares - sell_shares`（本專案計算） |
| `close_price` | TWSE：BSR CSV 檔頭「收盤價」；缺漏時退回 TWSE OpenAPI `STOCK_DAY_ALL`。TPEx：brokerBS 回應檔頭「收盤價」 |

參考表來源：`company` ← TWSE OpenAPI / TPEx 上櫃公司清冊；`broker_branch` ← TWSE / TPEx 券商分公司清冊。

---

## 3. 架構總覽

```
┌─ GitHub Actions（cron 台灣 18:00，週一~週五）──────────────┐
│  非交易日偵測 → TWSE leg（requests + ONNX 驗證碼，並發 6~8） │
│                 TPEx leg（Playwright 取 cookie + 純 HTTP）   │
│      ↓ 解析券商×價位列 → 依分點加總 → 併收盤價 → 算買賣超     │
│  產出 2 個 Parquet（twse / tpex）+ 更新 manifest.json        │
│      ↓ gh release upload 到當月 release「data-YYYY-MM」      │
│      ↓ 刪除 13 個月前的 release（1 年保留）                  │
└────────────────────────────────────────────────────────────┘
                          │
        Parquet assets（GitHub Releases，私有）+ manifest.json（git 內）
                          │
┌─ Streamlit Community Cloud（免費）─────────────────────────┐
│  讀 manifest.json → 用 PAT 下載所需日期的 Parquet 到 /tmp   │
│  DuckDB 查詢 → 3 個頁面（個股籌碼 / 個股×分點成本 / 分點總覽）│
└────────────────────────────────────────────────────────────┘
```

git 歷史只含：程式碼、`manifest.json`、參考表（`company`、`broker_branch`）。Parquet 資料一律走 Releases assets，不進 git 歷史 → repo 本體恆為數 MB，**零手動維護**。

---

## 4. 抓取管線

### 4.1 排程與非交易日
- Workflow cron：`0 10 * * 1-5`（UTC 10:00 = 台灣 18:00）。另開 `workflow_dispatch` 供手動觸發與補跑（可帶 `date` 參數）。
- 非交易日偵測：抓 TWSE BSR 首頁 / brokerBS 頁面的「資料日期」，若 ≠ 目標日期（颱風假、國定假日、補班日等）→ 記 `ingest_log` 為 `skipped` 並正常結束（exit 0），不報錯。

### 4.2 股票清單
- 每日先取「當日有成交」的股票清單：
  - TWSE：OpenAPI `https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL`（含代號與收盤價，一次全市場）。
  - TPEx：對應之當日個股行情端點。
- 僅對清單內股票查詢分點，避免對停牌/無量股空打。

### 4.3 TWSE leg
- 並發 6–8 個 worker（每檔查詢為獨立 session，互不影響）；小幅 jitter，不加固定長延遲。
- 每檔：GET 表單 → 辨識驗證碼 → POST → GET CSV。驗證碼辨識失敗重試至多 5 次（每次換新 `guid`）。
- 解析左右兩組明細 → 依 `branch_id` 加總 `buy_shares`、`sell_shares`。
- 預估 ~1,000 檔，~3–8 分鐘。

### 4.4 TPEx leg
- 啟動 1 個 Playwright + Chromium，載入 brokerBS 頁面，等 `cf-turnstile-response` 填入 → 匯出 `cf_clearance` cookie 與 User-Agent。
- 以純 HTTP（帶 cookie + UA）並發 6–8 查詢 `POST .../afterTrading/brokerBS`。cookie 逾時則重啟 Playwright 重新取得。
- 解析 JSON 明細 → 依 `branch_id` 加總。
- 預估 ~800 檔，~3–6 分鐘（cookie 可重用前提下）。

### 4.5 禮儀與合規
- 先讀並遵守兩站 `robots.txt`。
- 並發上限 8；帶可辨識 User-Agent；遇 `429` / `Retry-After` 退避。
- 只用官方查詢端點，不硬解前端 HTML 結構以外的東西。
- 失敗股票記入 `ingest_log.note`，隔日重試不阻塞當日其他股票。

### 4.6 產出
- 每日兩檔 Parquet：`YYYY-MM-DD_twse.parquet`、`YYYY-MM-DD_tpex.parquet`。
- Schema（每列一分點一股一日）：
  ```
  date: DATE
  market: STRING ('twse' | 'tpex')
  stock_id: STRING
  branch_id: STRING
  branch_name: STRING
  buy_shares: INT64
  sell_shares: INT64
  net_shares: INT64        # buy - sell
  close_price: DOUBLE
  ```
- 壓縮：Snappy。實測量級 ~350 萬列/日、~22 MB/日（雙市場合計）、~5.5 GB/年。
- 原始回應（CSV/JSON）gzip 後留存**最近 30 天**於 release `raw-YYYY-MM`（僅供近期抽查；Parquet 為正式紀錄）。逾 30 天的 raw 自動刪。

### 4.7 上傳與保留
- 當月 release `data-YYYY-MM` 不存在則建立；`gh release upload data-YYYY-MM *.parquet --clobber`。
- 更新 `manifest.json`（提交進 git）：
  ```json
  { "updated": "2026-09-03T10:20:00Z",
    "days": {
      "2026-09-03": { "twse": "<asset download url>", "tpex": "<asset download url>",
                       "rows": 3480000, "close_source": {"twse":"bsr_csv","tpex":"brokerBS"} }
    } }
  ```
- 保留：刪除 release `data-<13 個月前的 YYYY-MM>` 及其 tag、`raw-<32 天前的月>`。以「整月」為刪除單位，維持約 12–13 個月滾動。

---

## 5. 資料模型

### 5.1 事實資料 — Parquet（見 §4.6 schema）
- 主鍵語意：`(date, market, stock_id, branch_id)` 唯一。
- 只在該分點當日對該股有進出時才有列（無進出＝無列，屬正確）。

### 5.2 參考表 — repo 內 SQLite 檔 `reference.db`（每週更新，提交進 git，數百 KB）
```
company(stock_id TEXT PK, name TEXT, market TEXT)          -- market: 'twse' | 'tpex'
broker_branch(branch_id TEXT PK, branch_name TEXT,
              parent_broker_id TEXT, parent_broker_name TEXT)
```

### 5.3 稽核 — `ingest_log`（存為 Parquet 或 append 到 release，一列一次執行）
```
date, market, status ('ok'|'partial'|'skipped'|'failed'),
stock_count, row_count, started_at, finished_at, note
```

---

## 6. FIFO 平均成本 — 寫死規格

**輸入**：`stock_id` + `branch_id` + `[start_date, end_date]`（皆含端點）。

**演算法**：
1. 取該 `(stock_id, branch_id)` 在區間內的 `daily_flow` 列，依 `date` 升冪。
2. 逐日視為一筆交易：數量 `q = net_shares`，價格 `p = close_price`。
   - `q > 0`：買入。將 `(q, p)` 推入 FIFO 佇列尾端。
   - `q < 0`：賣出 `|q|` 股。從佇列**前端（最舊）**開始消耗；耗盡一個 lot 就移除，部分消耗則減少其數量。
     - 若 `|q|` 超過佇列現有總量（因不回補、看不到上線前的期初部位）→ **超出部分捨去**，將該日 `oversold_flag = true`。
   - `q == 0`：略過。
3. 區間處理完畢：
   - `est_inventory_shares` = 佇列剩餘總量。
   - `avg_cost` = Σ(lotᵢ.q × lotᵢ.p) / Σ(lotᵢ.q)；若佇列為空則 `avg_cost = null`。
4. **輸出欄位**：`avg_cost`、`est_inventory_shares`、`period_buy_shares`（區間 net>0 日之和）、`period_sell_shares`（區間 net<0 日之絕對值和）、`period_net_shares`、`had_oversell`（任一日 `oversold_flag`）。

**頁面固定顯示（逐字）**：
> 此為簡化估算：以每日買賣超 × 當日收盤價作為交易紀錄，並以 FIFO（先進先出）配對計算。券商分點買賣超為該分點多個帳戶淨額相抵後的結果，無法反映真實單筆成交價格，也看不到本工具上線前既有的部位。此數字僅供相對比較與趨勢觀察，**非真實成交均價**。

---

## 7. 網頁

- 框架：**Streamlit**。部署：**Streamlit Community Cloud**（免費）。
- 資料存取：啟動時讀 `manifest.json`；依查詢所需日期範圍，用 GitHub PAT（`repo` scope，存 Streamlit secrets）透過 Releases API 取得 asset，下載到 `/tmp`，以 **DuckDB** 查詢本地 Parquet。`st.cache_data`（TTL 例如 6 小時）快取已下載檔與查詢結果。
- UI 語言：全繁體中文。

### 7.1 頁1 — 個股籌碼
- 輸入：股號或股名（模糊比對 `company`）。
- 參數：回看天數 N（5 / 10 / 20 / 60 交易日，預設 20）。
- 呈現：
  - 該股近 N 交易日，各分點 `Σ net_shares` 排名 → 前 15 大買超分點表、前 15 大賣超分點表（分點名稱、買超/賣超張數、佔該股區間總量比重）。
  - 選一個分點 → 畫該分點對該股的**每日累計買賣超**折線（近 N 日或更長）。

### 7.2 頁2 — 個股 × 分點成本
- 輸入：股（同上）+ 分點（模糊比對 `broker_branch`）+ 區間（日期起迄，預設近 60 交易日）。
- 呈現：
  - FIFO 輸出（§6）：平均成本、估計庫存張數、區間總買/總賣/淨、`had_oversell` 警示。
  - 每日交易明細表：日期、net 張數、當日收盤價、當日累計庫存、當日累計 FIFO 成本。
  - 庫存曲線圖。
  - 免責聲明（§6 逐字）。

### 7.3 頁3 — 分點總覽
- 輸入：分點 + 區間（預設近 20 交易日）。
- 呈現：該分點在區間內所有 `Σ net_shares ≠ 0` 的個股，一列一股：
  - 股號/名、區間買超/賣超/淨張數、FIFO 平均成本、估計庫存張數、`had_oversell`。
  - 可依任一欄排序。
  - 效能：DuckDB 先做分組彙整取出候選股清單，FIFO 逐股在 Python 計算；候選股過多時預設只算 |淨張數| 前 200 檔並提示。
  - 免責聲明。

---

## 8. Repo 佈局與維運

```
broker-branch-tracker/
├── .github/workflows/daily_ingest.yml     # cron + workflow_dispatch
├── ingest/
│   ├── __init__.py
│   ├── run.py                 # 進入點：非交易日偵測 → 兩 leg → 產出 → 上傳 → 保留
│   ├── twse.py                # BSR 流程 + CSV 解析
│   ├── tpex.py                # Playwright cookie + brokerBS 查詢 + JSON 解析
│   ├── captcha/               # ONNX 模型權重 + 前處理 + 推論
│   ├── aggregate.py           # 券商×價位 → 分點×日；併收盤價；算 net
│   ├── storage.py             # Parquet 寫出、gh release 上傳、manifest 更新、保留清理
│   └── reference.py           # 每週更新 company / broker_branch
├── app/
│   ├── streamlit_app.py
│   ├── data.py                # manifest 讀取、asset 下載、DuckDB 查詢
│   ├── fifo.py                # §6 演算法（ingest 與 app 共用）
│   └── pages/                 # 三頁
├── reference.db               # 參考表（git 內）
├── manifest.json              # 可用日期索引（git 內，job 更新）
├── tests/
│   ├── test_fifo.py           # 含超賣、空佇列、單日、跨區間案例
│   ├── test_twse_parse.py     # 用真實 CSV 樣本
│   └── test_tpex_parse.py     # 用真實 JSON 樣本
├── requirements.txt
└── README.md
```

- Secrets（GitHub Actions）：`GH_PAT`（上傳 release 用，或用內建 `GITHUB_TOKEN`）。
- Secrets（Streamlit）：`GH_PAT`（`repo` scope，讀 private release asset）、`GH_REPO`。
- 技術棧：Python 3.13、`requests`/`httpx`、`beautifulsoup4`、`lxml`、`playwright`、`onnxruntime`、`pillow`、`numpy`、`pandas`、`pyarrow`、`duckdb`、`streamlit`、`plotly`。
- 慣例比照 `ipo-tracker`：state/log 檔提交回 repo；本機測試文件；TLS 逃生門環境變數（若 TPEx 憑證在本機有問題，沿用 `TPEX_API_INSECURE_SSL`）。

---

## 9. 免費額度與風險（需在 README 與 UI 標明）

| 項目 | 免費額度 | 本專案用量 | 風險 / 對策 |
|---|---|---|---|
| GitHub Actions（private repo） | 2,000 分鐘/月 | 估 ~15 分/日 × 21 ≈ 315 分/月 | 充裕。若 TPEx 退回逐檔 Turnstile，改本機 runner（本機執行不計 GitHub 分鐘） |
| GitHub Releases 儲存 | 「合理使用」（數 GB 無虞） | Parquet ~5.5 GB + raw 近 30 天 ~1 GB | 低。整月刪除維持滾動 |
| GitHub API 流量（Streamlit 下載 asset） | 5,000 req/hr（PAT） | 個人查詢極少 | 低。`st.cache_data` 快取 |
| Streamlit Community Cloud | 1 GB RAM、閒置休眠（~30 秒喚醒）、免費 1 個私有 app | DuckDB 查數個 22 MB Parquet，記憶體充裕 | 冷啟動延遲可接受 |
| 資料來源條款 | — | — | 「不得散布」→ app 私有、只呈現加工視圖、加免責、不提供原始整批下載 |

**其他風險**
- **TPEx `cf_clearance` 可重用性**：建置里程碑 1 驗收；不可重用則 TPEx leg 走本機 runner。
- **FIFO 前 3–6 個月數字偏差**：不回補 → 期初部位視為 0 → 上線前已持有的分點會頻繁觸發超賣捨去。功能可用，數字品質隨累積時間成熟。UI 對「資料起始日」與 `had_oversell` 明確標示。
- **驗證碼模型正確率下滑**：TWSE 若更換驗證碼樣式 → 重試迴圈先頂著，必要時重訓模型（沿用 `ipo-tracker` 已有的 Tesseract/OCR 經驗）。
- **分點併點/改名**：`branch_id` 用官方清冊穩定代碼；交易所重編時舊資料留舊碼，跨長區間分點查詢可能不連續。1 年窗口影響小。
- **來源網站改版**：TWSE/TPEx 皆可能改版（TPEx 近期才改網址）。解析層寫成獨立模組 + 真實樣本回歸測試，改版時定位快。

---

## 10. 建置里程碑（對應需求方要求的開發階段）

1. **里程碑 1 — 單股原型 + TPEx 可行性驗收**
   - TWSE：對單一股票完整跑通 GET→驗證碼→POST→CSV→解析→彙整，人工核對數字。
   - TPEx：Playwright 取 `cf_clearance` → **驗證能否重用於多檔查詢**（決定 TPEx leg 走 GitHub 或本機 runner）。
   - 產出：`twse.py`、`tpex.py`、`aggregate.py`、解析回歸測試。
2. **里程碑 2 — 全市場每日抓取 + 排程**
   - 股票清單、並發、非交易日偵測、`ingest_log`、失敗重試。
   - `daily_ingest.yml`（cron + 手動 + `date` 補跑）。
3. **里程碑 3 — 儲存落地**
   - Parquet 產出、`gh release` 上傳、`manifest.json`、13 個月保留清理、raw 30 天保留。
   - 參考表每週更新。
4. **里程碑 4 — 網頁：搜尋 + 近期籌碼**
   - Streamlit 骨架、`data.py`（manifest→asset→DuckDB）、頁1。
5. **里程碑 5 — FIFO 成本計算與呈現**
   - `fifo.py` + 測試、頁2、頁3。
   - 部署到 Streamlit Community Cloud。

---

## 11. 已決策事項

- **歷史回補**：不做。上線日起累積。保留「歷史匯入接口」：`daily_flow` 的 Parquet schema 即為匯入格式，日後若取得外部歷史（TEJ 匯出、FinMind 單月訂閱等）可直接轉檔灌入同一 release 結構。
- **官方是否涵蓋全分點**：是。TWSE BSR / TPEx brokerBS 已含全部分點，不需第三方。
- **免費額度不夠時的取捨**：目前估算充裕。若 Actions 分鐘不足 → TPEx leg 移本機 runner；若 Releases 儲存吃緊 → 縮短保留月數或改 Cloudflare R2（需綁卡但免費額度內不扣款）。**不**縮減分點涵蓋範圍（需求方明確要求全量）。

## 12. 未來（非本版）
- v2：TPEx 若長期被 Cloudflare 加嚴，改混合式（本機 runner 跑 TPEx，GitHub 跑 TWSE 與網頁部署）。
- v2：歷史匯入工具（吃 TEJ / FinMind 匯出檔）。
- v2：分點「期間損益」與勝率統計、關鍵分點追蹤清單與變動提醒（可併入 `industry-digest` 寄信）。

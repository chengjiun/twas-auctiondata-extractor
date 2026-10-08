# 台灣證券商同業公會（TWSA）歷年競拍開標紀錄下載與 PDF 數據解析

本專案使用 **Poetry** 進行虛擬環境隔離與相依套件管理，並使用 **Playwright** 自動化操作「台灣證券商同業公會」（TWSA）承銷公告系統，精準下載**開標紀錄 PDF**（跳過招標說明書），並使用 **pdfplumber** 嚴格萃取 **PDF 第一頁** 中的各項關鍵指標（公司名稱、最低/最高/加權平均得標價等），最終匯出為結構化 CSV (`auction_records.csv`)。

---

## 一、專案建置與環境隔離 (Poetry)

### 1. 專案依賴安裝指令

若從全新環境開始，可依以下指令進行初始化與依賴安裝：

```bash
# 1. 綁定 Python (推薦使用 Homebrew 或系統 python3)
poetry env use /opt/homebrew/bin/python3.12   # 或 poetry env use $(which python3)

# 2. 加入所需套件
poetry add playwright pdfplumber pypdf pandas openpyxl requests beautifulsoup4

# 3. 安裝 Playwright 瀏覽器核心 (Chromium)
poetry run playwright install chromium
```

> **已配置本機環境隔離**：專案中的 [`poetry.toml`](file:///Volumes/XPG1TP-MAC/homeX/chengjiun/workspace/aiworks/twsa-data-download/poetry.toml) 已啟用 `virtualenvs.in-project = true`，虛擬環境完全儲存於專案根目錄 `.venv` 下，絕不污染全域環境。

---

## 二、專案架構

```text
twsa-data-download/
├── pyproject.toml                     # Poetry 專案設定與套件清單 (PEP 621)
├── poetry.toml                        # Poetry 隔離設定 (強制 .venv 建立於本機目錄)
├── .gitignore                         # Git 忽略清單 (排除 .venv, 下載的 PDF, CSV 等)
├── run_pipeline.py                    # 端到端一鍵自動化入口腳本
├── twsa_data_download/                # 核心模組套件
│   ├── __init__.py
│   ├── crawler_playwright.py          # Playwright 網頁導航、分頁、開標 PDF 下載
│   ├── extractor.py                   # PDF 第一頁嚴格數據萃取模組
│   └── crawler.py                     # Requests 備援爬蟲模組
└── README.md
```

---

## 三、自動化核心機制說明

### 1. 網頁導航與搜尋條件
- 前往公會承銷公告網址：`https://web.twsa.org.tw/edoc2/default.aspx`。
- 自動切換查詢類型為「**競拍公告/開標統計表**」(`value="Auction"`)。
- 支援切換查詢年度（`--year 2024`）以及設定申報日期區間（`--start-date` / `--end-date`）。
- 自動等待 ASP.NET WebForms 之 `__doPostBack` 異步更新與表格載入。

### 2. 精準下載「開標紀錄」PDF（非招標說明書）
- 在 TWSA 網頁表單中，每家公司通常有兩個 PDF 連結（第一個為招標說明書，第二個為開標紀錄）。
- 腳本鎖定表格行 (Row) 的**最後一欄 (Last Column)** 或該列的**第二個下載按鈕**，確保觸發的是開標紀錄下載。
- 使用 Playwright 下載攔截器 (`page.expect_download()`) 完整儲存至 `./downloaded_pdfs` 目錄。

### 3. PDF 第一頁內容萃取 (嚴格限制僅解析 Page 1)
- 針對下載之 PDF 檔，強制僅讀取 `pdf.pages[0]`。
- 透過正規表示式 (Regex) 與表格辨識，精準抓取：
  - **標案名稱與股票代號**（例：大東電 1623）
  - **得標價格**：最高得標價、最低得標價、加權平均得標價
  - **核心競拍數據**：最低承銷價格(底價)、公開承銷價格、合格投標筆數/數量、得標筆數/數量、得標總金額等。

### 4. 防呆與錯誤處理
- 當遇到個別 PDF 連結失效、下載超時、或內容格式非標準時，程式會輸出 `[Warning]` 標示該公司名稱，並以 `try...except` 繼續處理下一筆，確保大型批次工作不中斷。

---

## 四、執行方式

### 1. 一鍵執行完整流程 (推薦)

```bash
# 抓取 2024 年競拍開標紀錄並輸出 CSV
poetry run python run_pipeline.py

# 亦可使用 Poetry CLI 捷徑指令
poetry run twsa-pipeline
```

### 2. 自訂參數範例

```bash
# 指定查詢 2023 年，限制只抓取前 2 頁，並儲存至自訂 CSV
poetry run python run_pipeline.py --year 2023 --max-pages 2 --output-csv 2023_records.csv

# 指定特定日期範圍 (例如 2024 年上半年)
poetry run python run_pipeline.py --year 2024 --start-date 2024/01/01 --end-date 2024/06/30

# 除錯模式 (開啟瀏覽器視窗觀察運作)
poetry run python run_pipeline.py --headful
```

### 3. 單獨測試 PDF 第一頁數據萃取

```bash
poetry run python twsa_data_download/extractor.py downloaded_pdfs/sample.pdf
```

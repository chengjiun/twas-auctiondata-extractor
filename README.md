# 台灣證券商同業公會（TWSA）歷年競拍開標紀錄下載與 PDF 數據解析

本專案使用 **Poetry** 進行相依套件與虛擬環境管理，確保與本機系統環境完全隔離（虛擬環境建立於專案根目錄 `.venv` 下）。

專案目標為自動化下載「台灣證券商同業公會」（TWSA）歷年台股競價拍賣開標公告，並從公告 PDF 前兩頁中精準萃取**個人（自然人）與法人**之投標與得標統計數據。

---

## 專案結構

```text
twsa-data-download/
├── pyproject.toml              # Poetry 專案設定與依賴定義 (PEP 621)
├── poetry.toml                 # Poetry 設定 (強制 .venv 建立於專案內部)
├── .gitignore                  # Git 忽略檔案設定 (排除 .venv, 下載檔案等)
├── requirements.txt            # pip 相容依賴列表
├── twsa_data_download/         # 主套件模組
│   ├── __init__.py
│   ├── extractor.py            # PDF 數據萃取核心模組 (第 1~2 頁個人/法人統計)
│   └── crawler.py              # TWSA 網站歷年公告自動化下載爬蟲
├── auction_extractor.py        # 頂層快捷執行腳本
├── twsa_crawler.py             # 頂層快捷執行腳本
└── README.md                   # 專案說明文件
```

---

## 環境安裝與隔離（Poetry）

本專案已在 `poetry.toml` 啟用 `virtualenvs.in-project = true`，所有相依套件皆會安裝在專案目錄下的 `.venv/` 中，絕不污染本機全域 Python 環境。

### 1. 安裝 Poetry 依賴

在專案根目錄執行：

```bash
poetry install
```

這會自動建立 `.venv/` 虛擬環境並安裝所有依賴（`pdfplumber`, `requests`, `beautifulsoup4`, `pandas` 等）。

---

## 使用說明

### 方式 A：透過 Poetry 指令直接執行 (推薦)

#### 1. 萃取競拍 PDF 開標數據
```bash
poetry run twsa-extractor your_auction.pdf

# 或將結果輸出為 JSON 檔
poetry run twsa-extractor your_auction.pdf -o result.json
```

#### 2. 自動化爬取公會公告並下載 PDF
```bash
# 預設抓取 2024 年競拍公告
poetry run twsa-crawler

# 指定年份 (例如 2023) 並限制下載數量
poetry run twsa-crawler --year 2023 --limit 5
```

---

### 方式 B：進入虛擬環境 Shell

```bash
poetry shell

# 進入環境後可直接執行 Python 腳本
python auction_extractor.py your_auction.pdf
python twsa_crawler.py --year 2024
```

---

## 數據萃取指標對照說明

依據標準競拍開標統計表格式：
- **第 1 頁（總計）**：合格投標筆數、合格投標數量(仟股)、得標筆數、得標數量(仟股)、得標總金額(仟元)、標案價格資訊（底價、最低/最高/加權平均得標價）。
- **第 2 頁（法人）**：法人合格投標筆數、數量、投標數量比率%、法人得標筆數、數量、得標數量比率%。
- **個人（自然人）**：由 `總計 － 法人` 精準推導產出。

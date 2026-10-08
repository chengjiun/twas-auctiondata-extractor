#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_pipeline.py
---------------
端到端一鍵執行腳本：
1. 啟動 Playwright 自動化爬蟲瀏覽 TWSA 公告系統。
2. 篩選年度與競拍公告，處理 ASP.NET 動態分頁。
3. 下載每筆標案最後一欄的「開標紀錄」PDF。
4. 嚴格萃取每個 PDF 的第 1 頁核心指標（公司名稱、最低/最高/加權平均得標價等）。
5. 自動彙整並輸出乾淨的 CSV 報表 (auction_records.csv)。
"""

import sys
from twsa_data_download.crawler_playwright import main

if __name__ == "__main__":
    main()

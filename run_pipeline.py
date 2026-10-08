#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_pipeline.py
---------------
端到端一鍵執行腳本：
1. 查詢 TWSA 證券商同業公會競拍公告系統。
2. 下載每筆標案之「開標統計表」與「招標說明書」雙重 PDF。
3. 嚴格萃取第 1 頁開標核心統計與招標說明書之發券日、競標張數、公開申購與員工認購配售張數。
4. 計算量化投資指標（交割持有天數、投標倍數、溢價率等）。
5. 自動將結構化 CSV 報表匯出至專屬目錄 (預設: ./output/auction_records.csv)。
"""

import sys
from twsa_data_download.pipeline import main

if __name__ == "__main__":
    main()

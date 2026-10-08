#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
twsa_crawler.py
頂層便捷執行腳本（調用 twsa_data_download.crawler）
"""
import sys
from twsa_data_download.crawler import TwsaAuctionCrawler, main

if __name__ == "__main__":
    main()

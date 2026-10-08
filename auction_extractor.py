#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auction_extractor.py
頂層便捷執行腳本（調用 twsa_data_download.extractor）
"""
import sys
from twsa_data_download.extractor import AuctionPdfExtractor, main

if __name__ == "__main__":
    main()

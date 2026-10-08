#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
twsa_data_download.extractor
----------------------------
台股競價拍賣（TWSA / 券商公會）開標結果 PDF 解析器
規格要求：
1. 嚴格限制：只需要解析每個 PDF 檔的第一頁 (Page 1)。
2. 精準萃取：公司名稱、股票代號、開標日期、最低得標價、最高得標價、得標加權平均價格、
   最低承銷價格、公開承銷價格、合格投標筆數、合格投標數量、得標筆數、得標數量、得標總金額。
3. 輸出結構化字典，利於匯出成 CSV。
"""

import os
import re
import json
import argparse
from typing import Dict, Any, Optional, Union


class AuctionPdfExtractor:
    """台股競拍開標統計表資料萃取器"""

    def __init__(self, pdf_source: Optional[str] = None):
        self.pdf_source = pdf_source

    @staticmethod
    def _clean_num(val: Any) -> Optional[Union[int, float]]:
        """清理千分位與空白並轉為數值"""
        if val is None:
            return None
        s = str(val).strip()
        cleaned = re.sub(r"[,\s%張股元筆]", "", s)
        if not cleaned:
            return None
        try:
            return float(cleaned) if "." in cleaned else int(cleaned)
        except ValueError:
            return None

    def extract_page_one(self, pdf_path: Optional[str] = None) -> Dict[str, Any]:
        """
        【規格重點】：嚴格限制只讀取每個 PDF 檔的第一頁。
        精準萃取開標核心數據並扁平化輸出。
        """
        path = pdf_path or self.pdf_source
        if not path or not os.path.exists(path):
            raise FileNotFoundError(f"找不到 PDF 檔案: {path}")

        try:
            import pdfplumber
        except ImportError:
            raise ImportError("請先安裝 pdfplumber：執行 poetry run pip install pdfplumber 或 poetry install")

        with pdfplumber.open(path) as pdf:
            if not pdf.pages:
                raise ValueError(f"PDF 檔案無任何頁面: {path}")

            # 嚴格限制：僅讀取第 1 頁 (index 0)
            page1 = pdf.pages[0]
            text = page1.extract_text() or ""
            tables = page1.extract_tables() or []

        return self.parse_page_one_data(text, tables, file_name=os.path.basename(path))

    def parse_page_one_data(
        self, text: str, tables: Optional[list] = None, file_name: str = ""
    ) -> Dict[str, Any]:
        """
        針對第 1 頁文字與表格進行正規表示式解析與數據結構化。
        """
        # 0. 正規化 Unicode (將康熙部首如「最⾼」、「開標⽇期」等自動轉為標準繁體字)
        import unicodedata
        text = unicodedata.normalize("NFKC", text)

        data: Dict[str, Any] = {
            "company_name": "",          # 公司名稱 (例: 大東電)
            "security_code": "",         # 股票代號 (例: 1623)
            "issue_type": "",            # 發行性質 (例: 初上市)
            "auction_method": "",        # 競拍方式 (例: 美國標)
            "underwriter": "",           # 主辦承銷商 (例: 國泰)
            "auction_date": "",          # 開標日期 (例: 2026/01/13)
            "print_time": "",            # 印表時間
            "floor_price": None,         # 最低承銷價格 / 底價
            "public_price": None,        # 公開承銷價格
            "min_awarded_price": None,   # 最低得標價格
            "max_awarded_price": None,   # 最高得標價格
            "weighted_avg_price": None,  # 得標加權平均價格
            "valid_bids_count": None,    # 合格投標筆數
            "valid_bids_shares_k": None, # 合格投標數量(仟股)
            "awarded_bids_count": None,  # 得標筆數
            "awarded_shares_k": None,    # 得標數量(仟股)
            "awarded_total_amount_k": None,  # 得標總金額(仟元)
            "source_file": file_name,    # 來源檔案
        }

        # 1. 萃取公司名稱、股票代號、發行性質 (例如:「大東電 (1623) 初上市」或「尖點三 (80213) 無擔保可轉換公司債」)
        all_matches = re.findall(r"([^\s\(\)]+)\s*\(([0-9A-Za-z]+)\)\s*([^\n\r]*)", text)
        for name_cand, code_cand, type_cand in all_matches:
            name_cand = name_cand.strip()
            code_cand = code_cand.strip()
            if not any(k in name_cand for k in ["報表", "統計表", "系統", "T004"]) and code_cand != "T004":
                data["company_name"] = name_cand
                data["security_code"] = code_cand
                data["issue_type"] = type_cand.strip()
                break

        if not data["company_name"]:
            # 備援比對無括號格式，例如：「浩宇生醫 初上櫃」或「光焱科技 初上櫃」
            alt_title = re.search(r"^([^\s\(\)]+)\s+(初次?上[櫃市]|創新板[^\n\r]*|現增[^\n\r]*)", text, re.MULTILINE)
            if alt_title:
                data["company_name"] = alt_title.group(1).strip()
                data["issue_type"] = alt_title.group(2).strip()

        if not data["company_name"]:
            # 備援比對格式：有價證券名稱： 1623 大東電
            alt_match = re.search(r"(?:有價證券名稱|標案名稱)[：:\s]+([0-9A-Za-z]+)?\s*([^\n\r]+)", text)
            if alt_match:
                if alt_match.group(1):
                    data["security_code"] = alt_match.group(1).strip()
                data["company_name"] = alt_match.group(2).strip()

        # 2. 萃取基本標案資訊
        field_patterns = [
            ("auction_method", r"競拍方式[：:\s]+([^\s\n\r]+)"),
            ("underwriter", r"主辦承銷商[：:\s]+([^\s\n\r]+)"),
            ("auction_date", r"開標日期[：:\s]+([0-9/.\-]+)"),
            ("print_time", r"印表時間[：:\s]+([0-9/.\-]+\s+[0-9:]+)"),
        ]
        for key, pat in field_patterns:
            m = re.search(pat, text)
            if m:
                data[key] = m.group(1).strip()

        # 3. 萃取各項得標與承銷價格
        price_patterns = [
            ("floor_price", r"最低承銷價格[：:\s]+([0-9.,]+)"),
            ("public_price", r"公開承銷價格[：:\s]+([0-9.,]+)"),
            ("min_awarded_price", r"最低得標價格[：:\s]+([0-9.,]+)"),
            ("max_awarded_price", r"最高得標價格[：:\s]+([0-9.,]+)"),
            ("weighted_avg_price", r"(?:得標加權平均|加權平均得標|平均得標)價格[：:\s]+([0-9.,]+)"),
        ]
        for key, pat in price_patterns:
            pm = re.search(pat, text)
            if pm:
                data[key] = self._clean_num(pm.group(1))

        # 4. 萃取統計數據表格 (合格投標筆數, 數量, 得標筆數, 數量, 總金額)
        extracted_table = False
        if tables:
            for tbl in tables:
                for row in tbl:
                    nums = [self._clean_num(c) for c in row if self._clean_num(c) is not None]
                    if len(nums) == 5:
                        data["valid_bids_count"] = nums[0]
                        data["valid_bids_shares_k"] = nums[1]
                        data["awarded_bids_count"] = nums[2]
                        data["awarded_shares_k"] = nums[3]
                        data["awarded_total_amount_k"] = nums[4]
                        extracted_table = True
                        break
                if extracted_table:
                    break

        # 5. 若無提取到表格，啟用 Regex 文字比對備援機制
        if not extracted_table:
            # 尋找 5 個欄位數值連續排列 (例如: 2,984 16,158 606 4,320 993,720.10)
            m = re.search(
                r"合格投標筆數[\s\S]*?得標總金額[^\n]*\n\s*([0-9,]+)\s+([0-9,]+)\s+([0-9,]+)\s+([0-9,]+)\s+([0-9,.]+)",
                text,
            )
            if m:
                data["valid_bids_count"] = self._clean_num(m.group(1))
                data["valid_bids_shares_k"] = self._clean_num(m.group(2))
                data["awarded_bids_count"] = self._clean_num(m.group(3))
                data["awarded_shares_k"] = self._clean_num(m.group(4))
                data["awarded_total_amount_k"] = self._clean_num(m.group(5))
            else:
                nums_found = re.findall(r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?)", text)
                if len(nums_found) >= 5:
                    for i in range(len(nums_found) - 4):
                        c1, c2, c3, c4, c5 = [self._clean_num(nums_found[j]) for j in range(i, i + 5)]
                        if c1 and c2 and c3 and c4 and c5 and (c3 <= c1) and (c4 <= c2):
                            data["valid_bids_count"] = c1
                            data["valid_bids_shares_k"] = c2
                            data["awarded_bids_count"] = c3
                            data["awarded_shares_k"] = c4
                            data["awarded_total_amount_k"] = c5
                            break

        # 檢驗關鍵得標欄位是否存在：若無加權均價、最低得標價或得標筆數，判定為非開標紀錄（如招標說明書）
        if data["weighted_avg_price"] is None and data["min_awarded_price"] is None and data["awarded_bids_count"] is None:
            raise ValueError("此檔案非開標統計表（未包含得標價或得標筆數，疑似為招標說明書）")

        return data


def main():
    parser = argparse.ArgumentParser(description="TWSA 台股競拍開標結果 PDF 第一頁萃取工具")
    parser.add_argument("pdf_path", help="PDF 檔案路徑")
    parser.add_argument("-o", "--output", help="輸出 JSON 檔案路徑 (可選)")
    args = parser.parse_args()

    extractor = AuctionPdfExtractor()
    data = extractor.extract_page_one(args.pdf_path)

    formatted_json = json.dumps(data, ensure_ascii=False, indent=2)
    print(formatted_json)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(formatted_json)
        print(f"\n已將結果儲存至: {args.output}")


if __name__ == "__main__":
    main()

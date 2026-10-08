#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
twsa_data_download.extractor
----------------------------
台股競價拍賣（TWSA / 券商公會）開標結果 PDF 解析器
支援：
1. 第 1 頁：全體（總計）投標、得標統計表及案件基本/價格資訊。
2. 第 2 頁：法人投標、得標統計表。
3. 自動計算「個人（自然人）」統計數據（總計 - 法人）。
"""

import os
import re
import sys
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

    def extract_from_pdf(self, pdf_path: Optional[str] = None) -> Dict[str, Any]:
        """從 PDF 檔案中提取第 1 頁與第 2 頁之數據"""
        path = pdf_path or self.pdf_source
        if not path or not os.path.exists(path):
            raise FileNotFoundError(f"找不到 PDF 檔案: {path}")

        try:
            import pdfplumber
        except ImportError:
            raise ImportError(
                "請先安裝 pdfplumber：使用 poetry run pip install 或 poetry install"
            )

        pages_text = []
        pages_tables = []

        with pdfplumber.open(path) as pdf:
            total_pages = len(pdf.pages)
            for i in range(min(2, total_pages)):
                p = pdf.pages[i]
                txt = p.extract_text() or ""
                tbls = p.extract_tables() or []
                pages_text.append(txt)
                pages_tables.append(tbls)

        p1_text = pages_text[0] if len(pages_text) > 0 else ""
        p2_text = pages_text[1] if len(pages_text) > 1 else ""
        p1_tables = pages_tables[0] if len(pages_tables) > 0 else []
        p2_tables = pages_tables[1] if len(pages_tables) > 1 else []

        result = self.parse_pages_data(p1_text, p2_text, p1_tables, p2_tables)
        result["file_name"] = os.path.basename(path)
        return result

    def parse_pages_data(
        self,
        page1_text: str,
        page2_text: str,
        page1_tables: Optional[list] = None,
        page2_tables: Optional[list] = None,
    ) -> Dict[str, Any]:
        """
        核心解析邏輯：結合第 1 頁（總計）與第 2 頁（法人），並計算個人（自然人）
        """
        result: Dict[str, Any] = {
            "basic_info": {},
            "price_info": {},
            "summary_statistics": {
                "total": {},         # 第 1 頁：總計
                "institutional": {}, # 第 2 頁：法人
                "individual": {},    # 計算衍生：個人 (自然人)
            },
        }

        full_head_text = page1_text + "\n" + page2_text
        self._extract_header_metadata(full_head_text, result["basic_info"], result["price_info"])

        total_stats = self._extract_page1_total(page1_text, page1_tables)
        result["summary_statistics"]["total"] = total_stats

        inst_stats = self._extract_page2_institutional(page2_text, page2_tables)
        result["summary_statistics"]["institutional"] = inst_stats

        indiv_stats = self._calculate_individual_stats(total_stats, inst_stats)
        result["summary_statistics"]["individual"] = indiv_stats

        return result

    def _extract_header_metadata(self, text: str, basic: Dict[str, Any], prices: Dict[str, Any]):
        title_match = re.search(r"([^\s\(\)]+)\s*\(([0-9A-Za-z]+)\)\s*([^\n\r]*)", text)
        if title_match:
            basic["company_name"] = title_match.group(1).strip()
            basic["security_code"] = title_match.group(2).strip()
            issue_type = title_match.group(3).strip()
            if issue_type:
                basic["issue_type"] = issue_type

        for pat, key in [
            (r"競拍方式[：:\s]+([^\s\n\r]+)", "auction_method"),
            (r"主辦承銷商[：:\s]+([^\s\n\r]+)", "underwriter"),
            (r"開標日期[：:\s]+([0-9/.\-]+)", "auction_date"),
            (r"印表時間[：:\s]+([0-9/.\-]+\s+[0-9:]+)", "print_time"),
        ]:
            m = re.search(pat, text)
            if m:
                basic[key] = m.group(1)

        price_patterns = [
            ("floor_price", r"最低承銷價格[：:\s]+([0-9.,]+)"),
            ("public_price", r"公開承銷價格[：:\s]+([0-9.,]+)"),
            ("min_awarded_price", r"最低得標價格[：:\s]+([0-9.,]+)"),
            ("max_awarded_price", r"最高得標價格[：:\s]+([0-9.,]+)"),
            ("weighted_avg_price", r"得標加權平均價格[：:\s]+([0-9.,]+)"),
        ]
        for key, pat in price_patterns:
            pm = re.search(pat, text)
            if pm:
                prices[key] = self._clean_num(pm.group(1))

    def _extract_page1_total(self, text: str, tables: Optional[list]) -> Dict[str, Any]:
        stats: Dict[str, Any] = {
            "valid_bids_count": None,       # 合格投標筆數
            "valid_bids_shares_k": None,    # 合格投標數量(仟股)
            "awarded_bids_count": None,     # 得標筆數
            "awarded_shares_k": None,       # 得標數量(仟股)
            "awarded_total_amount_k": None, # 得標總金額(仟元)
        }

        extracted = False
        if tables:
            for tbl in tables:
                for row in tbl:
                    nums = [self._clean_num(c) for c in row if self._clean_num(c) is not None]
                    if len(nums) == 5:
                        stats["valid_bids_count"] = nums[0]
                        stats["valid_bids_shares_k"] = nums[1]
                        stats["awarded_bids_count"] = nums[2]
                        stats["awarded_shares_k"] = nums[3]
                        stats["awarded_total_amount_k"] = nums[4]
                        extracted = True
                        break
                if extracted:
                    break

        if not extracted:
            m = re.search(
                r"合格投標筆數[\s\S]*?合格投標數量[\s\S]*?得標總金額[^\n]*\n\s*([0-9,]+)\s+([0-9,]+)\s+([0-9,]+)\s+([0-9,]+)\s+([0-9,.]+)",
                text,
            )
            if m:
                stats["valid_bids_count"] = self._clean_num(m.group(1))
                stats["valid_bids_shares_k"] = self._clean_num(m.group(2))
                stats["awarded_bids_count"] = self._clean_num(m.group(3))
                stats["awarded_shares_k"] = self._clean_num(m.group(4))
                stats["awarded_total_amount_k"] = self._clean_num(m.group(5))
            else:
                m_alt = re.findall(r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?)", text)
                if len(m_alt) >= 5:
                    for i in range(len(m_alt) - 4):
                        c1, c2, c3, c4, c5 = [self._clean_num(m_alt[j]) for j in range(i, i + 5)]
                        if c1 and c2 and c3 and c4 and c5 and (c3 <= c1) and (c4 <= c2):
                            stats["valid_bids_count"] = c1
                            stats["valid_bids_shares_k"] = c2
                            stats["awarded_bids_count"] = c3
                            stats["awarded_shares_k"] = c4
                            stats["awarded_total_amount_k"] = c5
                            break

        return stats

    def _extract_page2_institutional(self, text: str, tables: Optional[list]) -> Dict[str, Any]:
        stats: Dict[str, Any] = {
            "valid_bids_count": None,       # 法人合格投標筆數
            "valid_bids_shares_k": None,    # 法人合格投標數量(仟股)
            "bids_shares_ratio_pct": None,  # 投標數量比率%
            "awarded_bids_count": None,     # 法人得標筆數
            "awarded_shares_k": None,       # 法人得標數量(仟股)
            "awarded_shares_ratio_pct": None,# 得標數量比率%
        }

        extracted = False
        if tables:
            for tbl in tables:
                for row in tbl:
                    nums = [self._clean_num(c) for c in row if self._clean_num(c) is not None]
                    if len(nums) == 6:
                        stats["valid_bids_count"] = nums[0]
                        stats["valid_bids_shares_k"] = nums[1]
                        stats["bids_shares_ratio_pct"] = nums[2]
                        stats["awarded_bids_count"] = nums[3]
                        stats["awarded_shares_k"] = nums[4]
                        stats["awarded_shares_ratio_pct"] = nums[5]
                        extracted = True
                        break
                if extracted:
                    break

        if not extracted:
            lines = text.split("\n")
            for idx, line in enumerate(lines):
                if any(k in line for k in ["得標數量比率", "投標數量比", "合格投標筆"]):
                    for next_line in lines[idx + 1: idx + 6]:
                        nums = re.findall(r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?)", next_line)
                        if len(nums) == 6:
                            stats["valid_bids_count"] = self._clean_num(nums[0])
                            stats["valid_bids_shares_k"] = self._clean_num(nums[1])
                            stats["bids_shares_ratio_pct"] = self._clean_num(nums[2])
                            stats["awarded_bids_count"] = self._clean_num(nums[3])
                            stats["awarded_shares_k"] = self._clean_num(nums[4])
                            stats["awarded_shares_ratio_pct"] = self._clean_num(nums[5])
                            extracted = True
                            break
                if extracted:
                    break

        return stats

    def _calculate_individual_stats(
        self, total: Dict[str, Any], inst: Dict[str, Any]
    ) -> Dict[str, Any]:
        indiv: Dict[str, Any] = {
            "valid_bids_count": None,
            "valid_bids_shares_k": None,
            "bids_shares_ratio_pct": None,
            "awarded_bids_count": None,
            "awarded_shares_k": None,
            "awarded_shares_ratio_pct": None,
        }

        tot_bid_c = total.get("valid_bids_count")
        tot_bid_s = total.get("valid_bids_shares_k")
        tot_won_c = total.get("awarded_bids_count")
        tot_won_s = total.get("awarded_shares_k")

        inst_bid_c = inst.get("valid_bids_count")
        inst_bid_s = inst.get("valid_bids_shares_k")
        inst_won_c = inst.get("awarded_bids_count")
        inst_won_s = inst.get("awarded_shares_k")

        if tot_bid_c is not None and inst_bid_c is not None:
            indiv["valid_bids_count"] = tot_bid_c - inst_bid_c
        if tot_bid_s is not None and inst_bid_s is not None:
            indiv["valid_bids_shares_k"] = tot_bid_s - inst_bid_s
            if tot_bid_s > 0:
                indiv["bids_shares_ratio_pct"] = round(
                    (indiv["valid_bids_shares_k"] / tot_bid_s) * 100, 2
                )

        if tot_won_c is not None and inst_won_c is not None:
            indiv["awarded_bids_count"] = tot_won_c - inst_won_c
        if tot_won_s is not None and inst_won_s is not None:
            indiv["awarded_shares_k"] = tot_won_s - inst_won_s
            if tot_won_s > 0:
                indiv["awarded_shares_ratio_pct"] = round(
                    (indiv["awarded_shares_k"] / tot_won_s) * 100, 2
                )

        return indiv


def main():
    parser = argparse.ArgumentParser(description="TWSA 台股競拍開標結果 PDF 數據萃取工具")
    parser.add_argument("pdf_path", help="PDF 檔案路徑")
    parser.add_argument("-o", "--output", help="輸出 JSON 檔案路徑 (可選)")
    args = parser.parse_args()

    extractor = AuctionPdfExtractor()
    data = extractor.extract_from_pdf(args.pdf_path)

    formatted_json = json.dumps(data, ensure_ascii=False, indent=2)
    print(formatted_json)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(formatted_json)
        print(f"\n已將結果儲存至: {args.output}")


if __name__ == "__main__":
    main()

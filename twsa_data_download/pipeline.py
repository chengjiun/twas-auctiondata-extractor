#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
twsa_data_download.pipeline
---------------------------
端到端競拍數據下載與量化萃取管線：
1. 精準下載每一列最後一欄的「開標統計表」PDF，嚴格解析第一頁，萃取開標核心價格與量能（總得標張數、投標數量、底價、加權均價）。
2. 下載每一列第一欄的「招標說明書」PDF，萃取【發券日期】、【競標張數】、【公開申購張數】、【員工內部申購張數】與【券商保留認購張數】。
3. 自動計算量化交易風險指標：交割持有天數 (Days to Delivery)、投標倍數 (Bid-to-Cover Ratio)、競拍/公開/員工申購佔比 (%)、競拍加權溢價率 (%)。
4. 防呆錯誤處理：個別檔案下載或解析異常時印出 Warning 並繼續處理下一筆，不中斷。
5. 匯出標準 CSV (auction_records.csv)。
"""

import os
import re
import sys
import time
import argparse
import logging
from typing import List, Dict, Any, Optional, Union
import pandas as pd
from bs4 import BeautifulSoup

from twsa_data_download.extractor import AuctionPdfExtractor
from twsa_data_download.prospectus_extractor import ProspectusExtractor
from twsa_data_download.crawler import TwsaAuctionCrawler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("TwsaPipeline")


class AuctionPipeline:
    """歷年台股競拍開標紀錄端到端自動化管線"""

    def __init__(
        self,
        download_dir: str = "./downloaded_pdfs",
        output_csv: str = "auction_records.csv",
    ):
        self.download_dir = download_dir
        self.output_csv = output_csv
        self.extractor = AuctionPdfExtractor()
        self.prospectus_extractor = ProspectusExtractor()
        os.makedirs(self.download_dir, exist_ok=True)

    def run_requests_engine(
        self,
        years: Union[int, List[int]] = 2026,
        latest_n: int = 50,
        report_type: str = "Auction",
    ) -> List[Dict[str, Any]]:
        """
        使用 Requests 引擎進行雙重 PDF 下載與全維度量化數據萃取
        :param years: 西元年份 (單一年度如 2026，或年份列表如 [2026, 2025])
        :param latest_n: 下載最近 N 筆有效開標紀錄 (0 表示不限制)
        :param report_type: "Auction"
        """
        if isinstance(years, int):
            year_list = [years]
        else:
            year_list = list(years)

        crawler = TwsaAuctionCrawler(download_dir=self.download_dir)
        records: List[Dict[str, Any]] = []

        for yr in year_list:
            if latest_n > 0 and len(records) >= latest_n:
                break

            logger.info(f"正在連線至證券商公會公告系統 (查詢年度: {yr})...")
            try:
                crawler.init_session()
                html = crawler.query_auctions(year=yr, report_type=report_type)
            except Exception as e:
                logger.error(f"查詢 {yr} 年度公告失敗: {e}")
                continue

            soup = BeautifulSoup(html, "html.parser")
            table = soup.find("table", {"id": "ctl00_cphMain_gvResult"})
            if not table:
                logger.warning(f"年度 {yr} 未找到查詢結果表格！")
                continue

            rows = table.find_all("tr")[1:]  # 排除表頭
            logger.info(f"{yr} 年度共找到 {len(rows)} 筆標案項目。")

            # 篩選具備開標統計表（按鈕）的項目
            valid_rows = []
            for r in rows:
                btns = r.find_all("input", {"type": "image"})
                if len(btns) >= 2:
                    valid_rows.append(r)
                elif len(btns) == 1 and "ReportFileName" in btns[0].get("name", ""):
                    valid_rows.append(r)

            logger.info(f"其中已公告【開標統計表】的項目共 {len(valid_rows)} 筆。")

            # 由最新到最舊排程逐一尋找
            search_rows = list(reversed(valid_rows))

            for idx, row in enumerate(search_rows, 1):
                if latest_n > 0 and len(records) >= latest_n:
                    logger.info(f"已成功收集滿 {latest_n} 筆開標紀錄，停止後續處理。")
                    break

                cols = [td.get_text(strip=True) for td in row.find_all("td")]
                if len(cols) < 5:
                    continue

                seq_no = cols[0]
                company_name_web = cols[1]  # 發行公司
                underwriter_web = cols[2]   # 主辦承銷商
                issue_type_web = cols[3]    # 發行性質
                bidding_period = cols[6] if len(cols) > 6 else ""

                btns = row.find_all("input", {"type": "image"})
                report_btn = btns[-1]
                prospectus_btn = btns[0] if len(btns) >= 2 else None

                item = {
                    "seq_no": seq_no,
                    "company_name": company_name_web,
                    "case_name": f"{company_name_web}_{issue_type_web}",
                    "button_name": report_btn["name"],
                }

                logger.info(f"[{len(records) + 1}/{latest_n if latest_n > 0 else '全部'}] 處理標案: 序號 {seq_no} ({yr}) | {company_name_web} ({issue_type_web})")

                # 1. 下載【開標統計表】PDF (防呆處理)
                try:
                    pdf_path = crawler.download_pdf(item, year=yr)
                    if not pdf_path or not os.path.exists(pdf_path):
                        logger.warning(f"  [Warning] 公司: {company_name_web} (序號: {seq_no}) - 下載回傳非有效 PDF，跳過。")
                        continue
                except Exception as e:
                    logger.warning(f"  [Warning] 公司: {company_name_web} (序號: {seq_no}) - 下載開標表失敗: {e}")
                    continue

                # 2. 嚴格限制只讀取開標紀錄 PDF 第一頁進行數據萃取 (防呆處理)
                try:
                    data = self.extractor.extract_page_one(pdf_path)

                    data["seq_no"] = seq_no
                    data["company_full_name"] = company_name_web
                    data["bidding_period"] = bidding_period
                    if not data.get("underwriter"):
                        data["underwriter"] = underwriter_web

                except Exception as e:
                    logger.warning(f"  [Warning] 公司: {company_name_web} (序號: {seq_no}) - 開標表解析異常: {e}")
                    continue

                # 3. 下載並解析【招標說明書】PDF 萃取【發券日期】、【競標張數】、【公開申購】與【員工內部申購】張數
                if prospectus_btn:
                    p_item = {
                        "seq_no": seq_no,
                        "company_name": company_name_web,
                        "case_name": f"{company_name_web}_{issue_type_web}_說明書",
                        "button_name": prospectus_btn["name"],
                    }
                    try:
                        p_pdf_path = crawler.download_pdf(p_item, year=yr)
                        if p_pdf_path and os.path.exists(p_pdf_path):
                            p_data = self.prospectus_extractor.extract_prospectus(p_pdf_path)
                            data["delivery_date"] = p_data.get("delivery_date")
                            data["auction_shares_k"] = p_data.get("auction_shares_k")
                            data["public_subscription_shares_k"] = p_data.get("public_subscription_shares_k")
                            data["employee_subscription_shares_k"] = p_data.get("employee_subscription_shares_k")
                            data["underwriter_retention_shares_k"] = p_data.get("underwriter_retention_shares_k")
                            data["total_underwrite_shares_k"] = p_data.get("total_underwrite_shares_k")
                            data["overallotment_shares_k"] = p_data.get("overallotment_shares_k")
                            data["subscription_type"] = p_data.get("subscription_type")

                            # 4. 計算專業量化投資避險核心指標
                            q_metrics = self.prospectus_extractor.calculate_quant_metrics(
                                auction_date=data.get("auction_date"),
                                delivery_date=data.get("delivery_date"),
                                auction_shares=data.get("auction_shares_k"),
                                valid_bids_shares=data.get("valid_bids_shares_k"),
                                awarded_shares=data.get("awarded_shares_k"),
                                public_subscription_shares=data.get("public_subscription_shares_k"),
                                employee_subscription_shares=data.get("employee_subscription_shares_k"),
                                total_underwrite_shares=data.get("total_underwrite_shares_k"),
                                weighted_avg_price=data.get("weighted_avg_price"),
                                floor_price=data.get("floor_price"),
                            )
                            data.update(q_metrics)

                            # 補全代號（若開標表未包含，取自說明書）
                            if not data.get("security_code") and p_data.get("security_code"):
                                data["security_code"] = p_data.get("security_code")

                    except Exception as pe:
                        logger.warning(f"  [Notice] 公司: {company_name_web} - 招標說明書解析略過: {pe}")

                if not data.get("issue_type") or len(data.get("issue_type", "")) <= 2:
                    data["issue_type"] = issue_type_web
                if not data.get("company_name"):
                    data["company_name"] = company_name_web

                records.append(data)
                logger.info(
                    f"  -> 彙整成功: 公司={data.get('company_name')} | 代號={data.get('security_code')} | "
                    f"競標張數={data.get('auction_shares_k')} | 總得標張數={data.get('awarded_shares_k')} | "
                    f"公開申購={data.get('public_subscription_shares_k')} | 員工認購={data.get('employee_subscription_shares_k')}"
                )

                time.sleep(0.2)

        # 5. 匯出 CSV
        self.export_csv(records)
        return records

    def export_csv(self, records: List[Dict[str, Any]]):
        """匯出結構化量化分析 CSV 檔案"""
        if not records:
            logger.warning("無有效紀錄可供匯出。")
            return

        df = pd.DataFrame(records)

        column_mapping = {
            "seq_no": "序號",
            "company_full_name": "發行公司全稱",
            "company_name": "標案簡稱",
            "security_code": "股票/債券代號",
            "issue_type": "發行性質",
            "underwriter": "主辦承銷商",
            "bidding_period": "投標期間",
            "auction_date": "開標日期",
            "delivery_date": "發券日期(預計撥券日)",
            "holding_days": "交割持有天數(天)",
            "floor_price": "最低承銷價格(底價)",
            "public_price": "公開承銷價格",
            "min_awarded_price": "最低得標價格",
            "max_awarded_price": "最高得標價格",
            "weighted_avg_price": "得標加權平均價格",
            "auction_premium_pct": "競拍加權溢價率(%)",
            "auction_shares_k": "競標張數(仟股/張)",
            "valid_bids_count": "合格投標筆數",
            "valid_bids_shares_k": "合格投標數量(仟股/張)",
            "awarded_bids_count": "得標筆數",
            "awarded_shares_k": "總得標張數(仟股/張)",
            "awarded_total_amount_k": "得標總金額(仟元)",
            "bid_cover_ratio": "投標倍數(超額認購倍數)",
            "public_subscription_shares_k": "公開申購張數(仟股/張)",
            "employee_subscription_shares_k": "員工內部申購張數(仟股/張)",
            "underwriter_retention_shares_k": "券商保留認購張數(仟股/張)",
            "total_underwrite_shares_k": "承銷總張數(仟股/張)",
            "overallotment_shares_k": "過額配售張數(仟股/張)",
            "auction_ratio_pct": "競拍配售佔比(%)",
            "public_sub_ratio_pct": "公開申購配售佔比(%)",
            "employee_sub_ratio_pct": "員工內部申購佔比(%)",
            "subscription_type": "申購類型",
            "source_file": "來源檔案",
        }

        existing_cols = [c for c in column_mapping.keys() if c in df.columns]
        df_export = df[existing_cols].rename(columns=column_mapping)

        df_export.to_csv(self.output_csv, index=False, encoding="utf-8-sig")
        logger.info(f"==================================================")
        logger.info(f"【量化報表匯出成功】共 {len(df_export)} 筆競拍開標全維度紀錄儲存至: {self.output_csv}")
        logger.info(f"==================================================")


def main():
    parser = argparse.ArgumentParser(description="TWSA 台股競拍開標與量化指標全自動萃取管線")
    parser.add_argument("--year", default="2025", help="查詢年份 (預設: 2025，可為單一年份或逗號分隔列表如 2026,2025)")
    parser.add_argument("--limit", type=int, default=60, help="下載最近 N 筆開標紀錄 (預設: 60)")
    parser.add_argument("--output-csv", default="auction_records.csv", help="輸出 CSV 檔名")
    parser.add_argument("--download-dir", default="./downloaded_pdfs", help="PDF 儲存目錄")
    args = parser.parse_args()

    # 解析年份參數
    if "," in str(args.year):
        years = [int(y.strip()) for y in str(args.year).split(",") if y.strip()]
    else:
        years = [int(args.year)]

    pipeline = AuctionPipeline(
        download_dir=args.download_dir,
        output_csv=args.output_csv,
    )
    pipeline.run_requests_engine(
        years=years,
        latest_n=args.limit,
    )


if __name__ == "__main__":
    main()

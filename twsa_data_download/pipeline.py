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
        output_csv: str = "./output/auction_records.csv",
    ):
        self.download_dir = download_dir
        self.output_csv = output_csv
        self.extractor = AuctionPdfExtractor()
        self.prospectus_extractor = ProspectusExtractor()
        os.makedirs(self.download_dir, exist_ok=True)
        csv_dir = os.path.dirname(self.output_csv)
        if csv_dir:
            os.makedirs(csv_dir, exist_ok=True)

    def _process_single_case(
        self,
        crawler: TwsaAuctionCrawler,
        row: Any,
        yr: int,
    ) -> Optional[Dict[str, Any]]:
        """處理單一標案列：下載開標表與招標說明書、萃取數據並計算量化指標"""
        cols = [td.get_text(strip=True) for td in row.find_all("td")]
        if len(cols) < 5:
            return None

        seq_no = cols[0]
        company_name_web = cols[1]  # 發行公司
        underwriter_web = cols[2]   # 主辦承銷商
        issue_type_web = cols[3]    # 發行性質
        bidding_period = cols[6] if len(cols) > 6 else ""

        btns = row.find_all("input", {"type": "image"})
        report_btn = None
        prospectus_btn = None

        for b in btns:
            b_name = b.get("name", "")
            if "ReportFileName" in b_name:
                report_btn = b
            elif "AuctionFileName" in b_name:
                prospectus_btn = b

        if not report_btn:
            # 無開標結果表按鈕，跳過
            return None

        item = {
            "seq_no": seq_no,
            "company_name": company_name_web,
            "case_name": f"{company_name_web}_{issue_type_web}",
            "button_name": report_btn["name"],
        }

        # 1. 下載【開標統計表】PDF (防呆處理)
        try:
            pdf_path = crawler.download_pdf(item, year=yr)
            if not pdf_path or not os.path.exists(pdf_path):
                logger.warning(f"  [Warning] 公司: {company_name_web} (序號: {seq_no}) - 下載回傳非有效 PDF，跳過。")
                return None
        except Exception as e:
            logger.warning(f"  [Warning] 公司: {company_name_web} (序號: {seq_no}) - 下載開標表失敗: {e}")
            return None

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
            return None

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

                    # 4. 計算專業量化投資核心指標
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

        return data

    def process_year(
        self,
        crawler: TwsaAuctionCrawler,
        year: int,
        limit: int = 0,
        report_type: str = "Auction",
    ) -> List[Dict[str, Any]]:
        """處理單一年份之所有競拍案件"""
        logger.info(f"正在查詢 {year} (民國 {year - 1911} 年) 競拍開標公告...")
        try:
            crawler.init_session()
            html = crawler.query_auctions(year=year, report_type=report_type)
        except Exception as e:
            logger.error(f"查詢 {year} 年度公告失敗: {e}")
            return []

        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table", {"id": "ctl00_cphMain_gvResult"})
        if not table:
            logger.info(f"年度 {year} 無競拍公告資料。")
            return []

        rows = table.find_all("tr")[1:]  # 排除表頭
        if not rows:
            logger.info(f"年度 {year} 查無任何標案紀錄。")
            return []

        logger.info(f"{year} 年度共找到 {len(rows)} 筆標案項目。")

        # 篩選具備開標統計表按鈕的項目
        valid_rows = []
        for r in rows:
            btns = r.find_all("input", {"type": "image"})
            has_report = any("ReportFileName" in b.get("name", "") for b in btns)
            if has_report:
                valid_rows.append(r)

        logger.info(f"{year} 年度具備【開標統計表】之項目共 {len(valid_rows)} 筆。")
        if not valid_rows:
            return []

        # 決定處理順序與筆數
        if limit > 0:
            target_rows = list(reversed(valid_rows))[:limit]
        else:
            target_rows = valid_rows

        year_records: List[Dict[str, Any]] = []
        for idx, row in enumerate(target_rows, 1):
            cols = [td.get_text(strip=True) for td in row.find_all("td")]
            comp_name = cols[1] if len(cols) > 1 else "未知"
            seq_no = cols[0] if len(cols) > 0 else str(idx)

            logger.info(f"[{idx}/{len(target_rows)}] {year} 處理: 序號 {seq_no} | {comp_name}")
            rec = self._process_single_case(crawler, row, yr=year)
            if rec:
                year_records.append(rec)
                logger.info(
                    f"  -> 成功: 公司={rec.get('company_name')} | 代號={rec.get('security_code')} | "
                    f"底價={rec.get('floor_price')} | 均價={rec.get('weighted_avg_price')} | "
                    f"競標張數={rec.get('auction_shares_k')} | 得標張數={rec.get('awarded_shares_k')} | "
                    f"公開申購={rec.get('public_subscription_shares_k')} | 員工認購={rec.get('employee_subscription_shares_k')}"
                )
            time.sleep(0.15)

        return year_records

    def run_yearly_batch(
        self,
        start_year: int = 2009,
        end_year: int = 2026,
        output_dir: str = "./output",
        report_type: str = "Auction",
    ) -> Dict[int, List[Dict[str, Any]]]:
        """
        批次擷取自 start_year 至 end_year 歷年所有資料，
        每年獨立匯出一個 CSV 檔案，並最後彙整出一份完整歷年總表。
        """
        os.makedirs(output_dir, exist_ok=True)
        crawler = TwsaAuctionCrawler(download_dir=self.download_dir)
        all_results: Dict[int, List[Dict[str, Any]]] = {}
        all_records_combined: List[Dict[str, Any]] = []

        logger.info(f"==================================================")
        logger.info(f"啟動歷年台股競拍開標大數據批次擷取管線: {start_year} ~ {end_year}")
        logger.info(f"每年將獨立匯出專屬 CSV 於目錄: {output_dir}")
        logger.info(f"==================================================")

        for yr in range(start_year, end_year + 1):
            logger.info(f"\n>>> 開始處理 {yr} 年度 (民國 {yr - 1911} 年) <<<")
            yr_records = self.process_year(crawler, year=yr, limit=0, report_type=report_type)
            all_results[yr] = yr_records
            all_records_combined.extend(yr_records)

            # 每年獨立輸出一個 CSV
            yr_csv_path = os.path.join(output_dir, f"auction_records_{yr}.csv")
            self.export_csv(yr_records, output_path=yr_csv_path)

        # 輸出完整歷史合併總表
        combined_csv_path = os.path.join(output_dir, f"auction_records_{start_year}_{end_year}_all.csv")
        self.export_csv(all_records_combined, output_path=combined_csv_path)

        # 同步更新預設總表
        default_csv_path = os.path.join(output_dir, "auction_records.csv")
        self.export_csv(all_records_combined, output_path=default_csv_path)

        # 輸出彙總統計報告
        logger.info(f"\n================ 歷年數據擷取彙總報告 ================")
        total_count = 0
        for yr in range(start_year, end_year + 1):
            cnt = len(all_results[yr])
            total_count += cnt
            logger.info(f"  - {yr} 年 (民國 {yr - 1911:3d} 年): {cnt:3d} 筆紀錄 -> output/auction_records_{yr}.csv")
        logger.info(f"----------------------------------------------------")
        logger.info(f"總計有效開標紀錄: {total_count} 筆")
        logger.info(f"歷史總合併檔: {combined_csv_path}")
        logger.info(f"====================================================")

        return all_results

    def run_requests_engine(
        self,
        years: Union[int, List[int]] = 2026,
        latest_n: int = 50,
        report_type: str = "Auction",
    ) -> List[Dict[str, Any]]:
        """單一或複數年度執行方法 (相容舊呼叫介面)"""
        if isinstance(years, int):
            year_list = [years]
        else:
            year_list = list(years)

        crawler = TwsaAuctionCrawler(download_dir=self.download_dir)
        records: List[Dict[str, Any]] = []

        for yr in year_list:
            if latest_n > 0 and len(records) >= latest_n:
                break
            remaining = latest_n - len(records) if latest_n > 0 else 0
            yr_records = self.process_year(crawler, year=yr, limit=remaining, report_type=report_type)
            records.extend(yr_records)

        self.export_csv(records, output_path=self.output_csv)
        return records

    def export_csv(
        self,
        records: List[Dict[str, Any]],
        output_path: Optional[str] = None,
    ):
        """匯出結構化量化分析 CSV 檔案 (若無資料則匯出標準空表頭檔)"""
        out_file = output_path or self.output_csv
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

        csv_dir = os.path.dirname(out_file)
        if csv_dir:
            os.makedirs(csv_dir, exist_ok=True)

        if not records:
            df_empty = pd.DataFrame(columns=list(column_mapping.values()))
            df_empty.to_csv(out_file, index=False, encoding="utf-8-sig")
            logger.info(f"此年度無有效開標紀錄，已建立標準空資料表: {out_file}")
            return

        df = pd.DataFrame(records)

        # 排序：優先依序號或開標日期排序
        if "seq_no" in df.columns:
            try:
                df["_sort_key"] = pd.to_numeric(df["seq_no"], errors="coerce")
                df = df.sort_values(by="_sort_key").drop(columns=["_sort_key"])
            except Exception:
                pass

        existing_cols = [c for c in column_mapping.keys() if c in df.columns]
        df_export = df[existing_cols].rename(columns=column_mapping)

        df_export.to_csv(out_file, index=False, encoding="utf-8-sig")
        logger.info(f"【量化報表匯出成功】共 {len(df_export)} 筆競拍開標紀錄儲存至: {out_file}")


def main():
    parser = argparse.ArgumentParser(description="TWSA 台股競拍開標與量化指標全自動萃取管線")
    parser.add_argument("--all-years", action="store_true", help="擷取 2009 至 2026 年歷年所有資料並每年獨立匯出 CSV")
    parser.add_argument("--start-year", type=int, default=2009, help="批次起始年份 (預設: 2009)")
    parser.add_argument("--end-year", type=int, default=2026, help="批次結束年份 (預設: 2026)")
    parser.add_argument("--yearly", action="store_true", help="依年份個別輸出 CSV 檔案")
    parser.add_argument("--year", default=None, help="查詢特定年份 (單一年份或逗號分隔如 2025,2024)")
    parser.add_argument("--limit", type=int, default=0, help="下載最近 N 筆開標紀錄 (0 表示全部)")
    parser.add_argument("--output-dir", default="./output", help="輸出目錄 (預設: ./output)")
    parser.add_argument("--output-csv", default="./output/auction_records.csv", help="輸出單一 CSV 檔名")
    parser.add_argument("--download-dir", default="./downloaded_pdfs", help="PDF 暫存目錄")
    args = parser.parse_args()

    pipeline = AuctionPipeline(
        download_dir=args.download_dir,
        output_csv=args.output_csv,
    )

    if args.all_years or args.yearly:
        pipeline.run_yearly_batch(
            start_year=args.start_year,
            end_year=args.end_year,
            output_dir=args.output_dir,
        )
    elif args.year:
        if "," in str(args.year):
            years = [int(y.strip()) for y in str(args.year).split(",") if y.strip()]
        else:
            years = [int(args.year)]
        pipeline.run_requests_engine(
            years=years,
            latest_n=args.limit,
        )
    else:
        # 預設執行歷年全量批次產製 (2009-2026)
        pipeline.run_yearly_batch(
            start_year=2009,
            end_year=2026,
            output_dir=args.output_dir,
        )


if __name__ == "__main__":
    main()


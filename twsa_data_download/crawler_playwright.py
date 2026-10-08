#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
twsa_data_download.crawler_playwright
-------------------------------------
使用 Playwright 自動化擷取台灣證券商同業公會（TWSA）歷年台股競拍開標紀錄
網站：https://web.twsa.org.tw/edoc2/default.aspx

功能特點：
1. 自動導航並選取報表類型（競拍公告/開標統計表）與年份/日期範圍。
2. 處理 ASP.NET WebForms 動態 PostBack 與分頁 (Pagination)。
3. 正確識別每一行中的【開標紀錄】PDF（定位最後一欄/第二個下載連結，避免下載到招標說明書）。
4. 使用 Playwright 下載攔截器存檔至暫存資料夾。
5. 即時呼叫 extractor.extract_page_one() 嚴格解析第一頁關鍵數據。
6. 防呆機制：下載失敗或格式異常時發出 Warning 並繼續處理下一筆。
7. 自動匯出至結構化 CSV (auction_records.csv)。
"""

import os
import re
import sys
import time
import logging
from typing import List, Dict, Any, Optional
import pandas as pd

from twsa_data_download.extractor import AuctionPdfExtractor

# 設定日誌格式
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("TwsaPlaywrightCrawler")


class TwsaPlaywrightCrawler:
    TARGET_URL = "https://web.twsa.org.tw/edoc2/default.aspx"

    def __init__(
        self,
        download_dir: str = "./downloaded_pdfs",
        output_csv: str = "auction_records.csv",
        headless: bool = True,
    ):
        self.download_dir = download_dir
        self.output_csv = output_csv
        self.headless = headless
        self.extractor = AuctionPdfExtractor()
        os.makedirs(self.download_dir, exist_ok=True)

    def run(
        self,
        year: int = 2024,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        report_type: str = "Auction",
        max_pages: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        執行端到端自動化爬蟲與萃取流程
        :param year: 西元年份 (例如: 2024, 2023)
        :param start_date: 起始申報日期 (例如: '2024/01/01')，可選
        :param end_date: 結束申報日期 (例如: '2024/12/31')，可選
        :param report_type: "Auction" (競拍公告/開標統計表) 或 "AuctionInquiring"
        :param max_pages: 最大抓取分頁數 (0 代表無限制)
        :return: 萃取成功的紀錄串列
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error("尚未安裝 playwright！請先執行: poetry add playwright && poetry run playwright install chromium")
            raise

        records: List[Dict[str, Any]] = []

        logger.info(f"啟動 Playwright 無頭瀏覽器 (headless={self.headless})...")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(
                accept_downloads=True,
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()

            try:
                # 1. 導航至公會公告系統
                logger.info(f"連線至目標網站: {self.TARGET_URL}")
                page.goto(self.TARGET_URL, wait_until="networkidle", timeout=45000)

                # 2. 設定搜尋條件 (年度與報表類型)
                self._apply_search_filters(page, year=year, report_type=report_type)

                # 3. 處理分頁與每一頁的行資料
                current_page_num = 1
                while True:
                    logger.info(f"=== 正在處理第 {current_page_num} 頁搜尋結果 ===")
                    page_records = self._process_current_page(
                        page=page,
                        year=year,
                        start_date=start_date,
                        end_date=end_date,
                    )
                    records.extend(page_records)

                    if max_pages > 0 and current_page_num >= max_pages:
                        logger.info(f"已達到最大指定分頁數 ({max_pages} 頁)，停止翻頁。")
                        break

                    # 嘗試前往下一頁
                    has_next = self._go_to_next_page(page, current_page_num)
                    if not has_next:
                        logger.info("已處理至最後一頁，結束分頁掃描。")
                        break

                    current_page_num += 1

            finally:
                context.close()
                browser.close()

        # 4. 資料彙整與匯出 CSV
        self._export_to_csv(records)
        return records

    def _apply_search_filters(self, page, year: int, report_type: str):
        """設定查詢年度與查詢類型，觸發 ASP.NET PostBack 動態載入"""
        logger.info(f"設定查詢條件: 年度={year}, 類型={report_type}")

        # 1. 切換查詢年度
        if page.locator("#ctl00_cphMain_ddlYear").count() > 0:
            page.select_option("#ctl00_cphMain_ddlYear", str(year))
            page.wait_for_load_state("networkidle")

        # 2. 切換報表類型 (單選按鈕)
        # Auction: 競拍公告/開標統計表 (通常為 rblReportType_1)
        # AuctionInquiring: 競拍申購公告/開標統計表 (通常為 rblReportType_8)
        radio_selector = (
            "input[value='Auction']" if report_type == "Auction" else "input[value='AuctionInquiring']"
        )

        if page.locator(radio_selector).count() > 0:
            logger.info("觸發查詢類型變更...")
            # 點擊 radio button
            page.locator(radio_selector).click()
            # 等待 ASP.NET 異步重新載入表格
            page.wait_for_load_state("networkidle")
            page.wait_for_selector("#ctl00_cphMain_gvResult", timeout=30000)
            logger.info("查詢結果表格載入完成！")
        else:
            logger.warning(f"未能找到查詢類型單選按鈕: {radio_selector}")

    def _process_current_page(
        self, page, year: int, start_date: Optional[str], end_date: Optional[str]
    ) -> List[Dict[str, Any]]:
        """處理當前頁面的表格行，精準定位第二個 PDF (開標紀錄) 並下載萃取"""
        page_records: List[Dict[str, Any]] = []

        table = page.locator("#ctl00_cphMain_gvResult")
        if table.count() == 0:
            logger.warning("未找到結果表格 #ctl00_cphMain_gvResult")
            return page_records

        # 取得所有列 (排除標題行)
        rows = table.locator("tr").all()
        if not rows:
            return page_records

        # 遍歷資料列 (略過表頭 th 行)
        data_rows = [r for r in rows if r.locator("td").count() >= 5]
        logger.info(f"本頁共發現 {len(data_rows)} 筆標案列資料。")

        for idx, row in enumerate(data_rows, 1):
            tds = row.locator("td").all()
            if len(tds) < 5:
                continue

            # 抓取表格中前幾欄的基本文字資訊
            seq_no = tds[0].inner_text().strip()
            declare_date = tds[1].inner_text().strip()
            underwriter = tds[2].inner_text().strip()
            company_name = tds[3].inner_text().strip()

            # 日期區間篩選 (若有提供)
            if start_date and declare_date < start_date:
                continue
            if end_date and declare_date > end_date:
                continue

            logger.info(f"[{idx}/{len(data_rows)}] 處理中: 序號 {seq_no} | 公司: {company_name} (申報日: {declare_date})")

            # =========================================================================
            # 【關鍵下載定位】：
            # 使用者特別提醒：每一行通常有兩個 PDF 檔（第一個為招標說明書，第二個為開標紀錄）。
            # 依規格：定位到表格的【最後一欄】，點擊最後一個連結觸發「開標紀錄」下載。
            # =========================================================================
            last_td = tds[-1]
            download_elements = last_td.locator("input[type='image'], a, button").all()

            # 若最後一欄找不到按鈕，搜尋整列中的所有下載按鈕，並選取最後一個 (第二個)
            if not download_elements:
                all_buttons = row.locator("input[type='image'], a[href*='download'], a[href*='pdf'], a[id*='FileName'], input[id*='FileName']").all()
                if len(all_buttons) >= 2:
                    # 存在兩個 PDF：第 1 個是招標說明書，第 2 個是開標紀錄 -> 選取最後一個
                    target_btn = all_buttons[-1]
                elif len(all_buttons) == 1:
                    target_btn = all_buttons[0]
                else:
                    logger.warning(f"[Warning] 公司: {company_name} (序號: {seq_no}) - 表格中無可下載的公告按鈕，略過。")
                    continue
            else:
                target_btn = download_elements[-1]

            # 攔截並下載開標紀錄 PDF
            downloaded_pdf_path = None
            try:
                with page.expect_download(timeout=25000) as download_info:
                    target_btn.click()
                download = download_info.value

                safe_company = re.sub(r'[\/:*?"<>|]', "_", company_name)
                filename = f"{year}_{seq_no}_{safe_company}_開標紀錄.pdf"
                downloaded_pdf_path = os.path.join(self.download_dir, filename)
                download.save_as(downloaded_pdf_path)
                logger.info(f"  -> 下載開標紀錄成功: {filename}")

            except Exception as e:
                logger.warning(f"[Warning] 公司: {company_name} - PDF 下載失敗或超時: {e}")
                continue

            # =========================================================================
            # 【PDF 內容擷取】：嚴格限制只解析第一頁
            # =========================================================================
            if downloaded_pdf_path and os.path.exists(downloaded_pdf_path):
                try:
                    record = self.extractor.extract_page_one(downloaded_pdf_path)
                    # 補充網頁表格上的基礎欄位（若 PDF 內未標明）
                    if not record.get("company_name"):
                        record["company_name"] = company_name
                    if not record.get("underwriter"):
                        record["underwriter"] = underwriter
                    record["declare_date"] = declare_date
                    record["seq_no"] = seq_no

                    page_records.append(record)
                    logger.info(
                        f"  -> 成功萃取第一頁數據: 加權均價={record.get('weighted_avg_price')}, "
                        f"得標數量={record.get('awarded_shares_k')} 仟股"
                    )

                except Exception as e:
                    logger.warning(f"[Warning] 公司: {company_name} - 第一頁解析異常或找不到目標數值: {e}")
                    # 防呆處理：繼續執行下一筆，不中斷
                    continue

            # 禮貌性微延遲避免請求過於頻繁
            time.sleep(0.5)

        return page_records

    def _go_to_next_page(self, page, current_page_num: int) -> bool:
        """處理 ASP.NET GridView 分頁邏輯，前往下一頁"""
        try:
            # 尋找分頁列中的下一頁連結 (例如 "2", "3" 或 "Page$2")
            next_page_str = str(current_page_num + 1)
            next_link = page.locator(
                f"#ctl00_cphMain_gvResult tr a:has-text('{next_page_str}'), "
                f"#ctl00_cphMain_gvResult tr a[href*='Page${next_page_str}']"
            )

            if next_link.count() > 0:
                logger.info(f"發現下一頁 ({next_page_str})，正在進行翻頁...")
                next_link.first.click()
                page.wait_for_load_state("networkidle")
                page.wait_for_selector("#ctl00_cphMain_gvResult", timeout=20000)
                time.sleep(1)
                return True

            return False
        except Exception as e:
            logger.info(f"翻頁檢查結束或無下一頁: {e}")
            return False

    def _export_to_csv(self, records: List[Dict[str, Any]]):
        """將萃取結果匯出為乾淨的 CSV 檔案 (auction_records.csv)"""
        if not records:
            logger.warning("本次執行未萃取到任何有效競拍紀錄，未產出 CSV。")
            return

        df = pd.DataFrame(records)

        # 欄位重新排序與中文化呈現，方便使用者於 Excel 直接檢視
        column_mapping = {
            "seq_no": "序號",
            "declare_date": "申報日期",
            "company_name": "公司名稱",
            "security_code": "股票代號",
            "issue_type": "發行性質",
            "auction_method": "競拍方式",
            "underwriter": "主辦承銷商",
            "auction_date": "開標日期",
            "floor_price": "最低承銷價格(底價)",
            "public_price": "公開承銷價格",
            "min_awarded_price": "最低得標價格",
            "max_awarded_price": "最高得標價格",
            "weighted_avg_price": "得標加權平均價格",
            "valid_bids_count": "合格投標筆數",
            "valid_bids_shares_k": "合格投標數量(仟股)",
            "awarded_bids_count": "得標筆數",
            "awarded_shares_k": "得標數量(仟股)",
            "awarded_total_amount_k": "得標總金額(仟元)",
            "source_file": "來源檔案",
        }

        # 僅保留並重命名存在的欄位
        existing_cols = [c for c in column_mapping.keys() if c in df.columns]
        df_export = df[existing_cols].rename(columns=column_mapping)

        # 使用 utf-8-sig 輸出，防止繁體中文在 Excel 中開啟時變成亂碼
        df_export.to_csv(self.output_csv, index=False, encoding="utf-8-sig")
        logger.info(f"==================================================")
        logger.info(f"【成功完成】共匯出 {len(df_export)} 筆競拍開標紀錄至: {self.output_csv}")
        logger.info(f"==================================================")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="TWSA 歷年競拍開標紀錄 Playwright 自動化爬蟲與萃取")
    parser.add_argument("--year", type=int, default=2024, help="查詢年份 (預設: 2024)")
    parser.add_argument("--start-date", help="起始日期 (例如: 2024/01/01)")
    parser.add_argument("--end-date", help="結束日期 (例如: 2024/12/31)")
    parser.add_argument("--report-type", default="Auction", choices=["Auction", "AuctionInquiring"], help="報表類型")
    parser.add_argument("--download-dir", default="./downloaded_pdfs", help="PDF 暫存目錄")
    parser.add_argument("--output-csv", default="auction_records.csv", help="輸出 CSV 檔名")
    parser.add_argument("--max-pages", type=int, default=0, help="最多抓取頁數 (0 代表無限制)")
    parser.add_argument("--headful", action="store_true", help="關閉無頭模式，顯示瀏覽器視窗 (除錯用)")

    args = parser.parse_args()

    crawler = TwsaPlaywrightCrawler(
        download_dir=args.download_dir,
        output_csv=args.output_csv,
        headless=not args.headful,
    )

    crawler.run(
        year=args.year,
        start_date=args.start_date,
        end_date=args.end_date,
        report_type=args.report_type,
        max_pages=args.max_pages,
    )


if __name__ == "__main__":
    main()

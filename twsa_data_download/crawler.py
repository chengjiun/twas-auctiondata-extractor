#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
twsa_data_download.crawler
--------------------------
中華民國證券商業同業公會（TWSA）承銷公告系統 - 歷年競拍開標公告自動化爬蟲
"""

import os
import re
import time
import argparse
from typing import Dict, List, Optional
import requests
from bs4 import BeautifulSoup


class TwsaAuctionCrawler:
    BASE_URL = "https://web.twsa.org.tw/Edoc2/Default.aspx"

    def __init__(self, download_dir: str = "./downloaded_pdfs"):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": self.BASE_URL,
        })
        self.download_dir = download_dir
        os.makedirs(self.download_dir, exist_ok=True)
        self.viewstate = ""
        self.eventvalidation = ""
        self.viewstategenerator = ""

    def _extract_aspnet_fields(self, html: str):
        soup = BeautifulSoup(html, "html.parser")
        vs = soup.find("input", {"id": "__VIEWSTATE"})
        ev = soup.find("input", {"id": "__EVENTVALIDATION"})
        vsg = soup.find("input", {"id": "__VIEWSTATEGENERATOR"})

        self.viewstate = vs["value"] if vs else ""
        self.eventvalidation = ev["value"] if ev else ""
        self.viewstategenerator = vsg["value"] if vsg else ""

    def init_session(self) -> str:
        resp = self.session.get(self.BASE_URL, timeout=30)
        resp.raise_for_status()
        self._extract_aspnet_fields(resp.text)
        return resp.text

    def query_auctions(self, year: int, report_type: str = "Auction") -> str:
        form_data = {
            "__EVENTTARGET": "ctl00$cphMain$rblReportType$1" if report_type == "Auction" else "ctl00$cphMain$rblReportType$8",
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": self.viewstate,
            "__VIEWSTATEGENERATOR": self.viewstategenerator,
            "__EVENTVALIDATION": self.eventvalidation,
            "ctl00$cphMain$ddlYear": str(year),
            "ctl00$cphMain$rblReportType": report_type,
        }

        for attempt in range(3):
            try:
                resp = self.session.post(self.BASE_URL, data=form_data, timeout=30)
                resp.raise_for_status()
                break
            except Exception as e:
                if attempt < 2:
                    time.sleep(1.5)
                else:
                    raise e

        self._extract_aspnet_fields(resp.text)
        return resp.text

    def parse_table_items(self, html: str) -> List[Dict[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table", {"id": "ctl00_cphMain_gvResult"})
        if not table:
            return []

        rows = table.find_all("tr")[1:]
        items = []

        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 5:
                continue

            # 若該列有多個按鈕，選取最後一個（第二個才是「開標紀錄」）
            download_inputs = row.find_all("input", {"type": "image"})
            btn_name = download_inputs[-1].get("name") if download_inputs else None

            item_info = {
                "seq_no": cols[0].get_text(strip=True),
                "declare_date": cols[1].get_text(strip=True),
                "underwriter": cols[2].get_text(strip=True),
                "case_name": cols[3].get_text(strip=True),
                "button_name": btn_name,
            }
            if len(cols) >= 6:
                item_info["issue_type"] = cols[5].get_text(strip=True)

            items.append(item_info)

        return items

    def download_pdf(self, item: Dict[str, str], year: int) -> Optional[str]:
        btn_name = item.get("button_name")
        if not btn_name:
            return None

        safe_name = re.sub(r'[\/:*?"<>|]', "_", item.get("case_name", "auction"))
        file_name = f"{year}_{item.get('seq_no')}_{safe_name}.pdf"
        file_path = os.path.join(self.download_dir, file_name)

        if os.path.exists(file_path) and os.path.getsize(file_path) > 1000:
            return file_path

        post_data = {
            "__VIEWSTATE": self.viewstate,
            "__VIEWSTATEGENERATOR": self.viewstategenerator,
            "__EVENTVALIDATION": self.eventvalidation,
            "ctl00$cphMain$ddlYear": str(year),
            f"{btn_name}.x": "10",
            f"{btn_name}.y": "10",
        }

        for attempt in range(3):
            try:
                resp = self.session.post(self.BASE_URL, data=post_data, timeout=30)
                resp.raise_for_status()
                break
            except Exception as e:
                if attempt < 2:
                    time.sleep(1.5)
                else:
                    raise e

        content_type = resp.headers.get("Content-Type", "")
        if "pdf" in content_type or resp.content[:4] == b"%PDF":
            with open(file_path, "wb") as f:
                f.write(resp.content)
            print(f"[下載成功] {file_path}")
            return file_path
        else:
            print(f"[下載失敗] {item.get('case_name')} (非 PDF 格式)")
            return None


def main():
    parser = argparse.ArgumentParser(description="TWSA 歷年競拍開標公告爬蟲")
    parser.add_argument("--year", type=int, default=2024, help="查詢年份 (西元，預設: 2024)")
    parser.add_argument("--output-dir", default="./downloaded_pdfs", help="PDF 儲存目錄")
    parser.add_argument("--limit", type=int, default=0, help="下載數量限制 (0 表示無限制)")
    args = parser.parse_args()

    crawler = TwsaAuctionCrawler(download_dir=args.output_dir)
    print("連線至證券商公會公告系統...")
    crawler.init_session()

    print(f"正在查詢 {args.year} 年度之「競拍公告/開標統計表」...")
    html = crawler.query_auctions(year=args.year, report_type="Auction")
    records = crawler.parse_table_items(html)
    print(f"查詢完成！共找到 {len(records)} 筆紀錄。")

    download_list = records if args.limit <= 0 else records[:args.limit]
    for idx, rec in enumerate(download_list, 1):
        print(f"[{idx}/{len(download_list)}] 下載: {rec['seq_no']} - {rec['case_name']}")
        crawler.download_pdf(rec, year=args.year)
        time.sleep(1)


if __name__ == "__main__":
    main()

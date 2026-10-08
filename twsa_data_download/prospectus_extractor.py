#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
twsa_data_download.prospectus_extractor
---------------------------------------
台股競拍招標說明書（競價拍賣處理辦法公告）條款萃取器
專門解析：
1. 【發券日期】：有價證券發放日 / 預計撥券入帳日 / 掛牌上市(櫃)日。
2. 【承銷總數量分配】：
   - 競標張數 (提出競價拍賣數量)
   - 公開申購張數 (散戶抽籤配售)
   - 員工內部申購張數 (公司法第267條員工認購)
   - 券商保留自行認購張數 (主辦承銷商包銷保留)
   - 承銷總張數 (全案承銷總股數)
   - 過額配售張數 (綠鞋機制)
3. 【量化衍生風險與供需指標】：
   - 交割持有天數 (Days to Delivery)
   - 投標倍數 (Bid-to-Cover Ratio: 合格投標張數 / 總得標張數)
   - 競拍佔比 (%)、公開申購佔比 (%)、員工認購佔比 (%)
   - 競拍加權溢價率 (%)
"""

import os
import re
import unicodedata
from datetime import datetime
from typing import Dict, Any, Optional, Union
import pdfplumber


class ProspectusExtractor:
    """競價拍賣招標說明書條款與日程萃取器"""

    @staticmethod
    def _clean_num(val: Any) -> Optional[Union[int, float]]:
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

    def extract_prospectus(self, pdf_path: str) -> Dict[str, Any]:
        """
        從招標說明書 PDF 萃取發券日、競標張數、公開申購與員工內部申購張數
        """
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"找不到說明書檔案: {pdf_path}")

        all_text = ""
        with pdfplumber.open(pdf_path) as pdf:
            # 說明書的前 3~4 頁包含發券日程與承銷數量條款
            for page in pdf.pages[:5]:
                txt = page.extract_text() or ""
                all_text += txt + "\n"

        # 正規化 Unicode (處理康熙部首)
        all_text = unicodedata.normalize("NFKC", all_text)

        result: Dict[str, Any] = {
            "security_code": None,                  # 股票/債券代號
            "delivery_date": None,                  # 發券日期 (撥券日/掛牌日)
            "auction_shares_k": None,               # 競標張數 (提出競價拍賣數量 / 仟股/張)
            "public_subscription_shares_k": None,   # 公開申購張數 (仟股/張)
            "employee_subscription_shares_k": None, # 員工內部申購張數 (仟股/張)
            "underwriter_retention_shares_k": None, # 券商先行保留自行認購 (仟股/張)
            "total_underwrite_shares_k": None,      # 承銷總張數 (仟股/張)
            "overallotment_shares_k": None,         # 過額配售張數 (仟股/張)
            "subscription_type": "無公開申購(全數競拍)",
        }

        # 0. 萃取股票/債券代號 (若標案公告提及)
        code_m = re.search(r"(?:股票代號|債券代號|代號)[：:\s]*([0-9A-Za-z]+)", all_text)
        if code_m:
            result["security_code"] = code_m.group(1).strip()

        # 1. 萃取【發券日期 / 預計撥券日期】
        date_patterns = [
            r"通知集保公司於\s*([0-9]{2,3})\s*年\s*([0-9]{1,2})\s*月\s*([0-9]{1,2})\s*日\s*將(?:股票|債券)直接劃撥",
            r"預計(?:於)?\s*([0-9]{2,3})\s*年\s*([0-9]{1,2})\s*月\s*([0-9]{1,2})\s*日\s*將(?:股票|債券)直接劃撥",
            r"於\s*([0-9]{2,3})\s*年\s*([0-9]{1,2})\s*月\s*([0-9]{1,2})\s*日\s*將(?:股票|債券)直接劃撥至(?:得標人|認購人)",
            r"有價證券發放日期[^\n]*?([0-9]{2,3})\s*年\s*([0-9]{1,2})\s*月\s*([0-9]{1,2})\s*日",
            r"預計(?:於)?\s*([0-9]{2,3})\s*年\s*([0-9]{1,2})\s*月\s*([0-9]{1,2})\s*日\s*(?:掛牌|上市|上櫃)",
        ]

        for pat in date_patterns:
            m = re.search(pat, all_text)
            if m:
                roc_y, m_val, d_val = int(m.group(1)), int(m.group(2)), int(m.group(3))
                result["delivery_date"] = f"{roc_y + 1911}/{m_val:02d}/{d_val:02d}"
                break

        # 2. 萃取【競標張數】(提出競價拍賣數量)
        auc_patterns = [
            r"提出競價拍賣(?:之)?數量(?:為)?[：:\s]*([0-9,]+)\s*(?:千股|仟股|張)",
            r"其中\s*([0-9,]+)\s*(?:千股|仟股|張)\s*以\s*競價\s*拍賣",
            r"餘\s*([0-9,]+)\s*(?:千股|仟股|張)\s*委託證券承銷商以\s*競價\s*拍賣",
            r"其餘\s*([0-9,]+)\s*(?:千股|仟股|張)[^\n]*?以\s*競價\s*拍賣",
            r"競價拍賣(?:數量|股數|張數)[：:\s]*([0-9,]+)\s*(?:千股|仟股|張)",
            r"供競價拍賣(?:之)?數量[：:\s]*([0-9,]+)\s*(?:千股|仟股|張)",
            r"全數\s*([0-9,]+)\s*(?:千股|仟股|張)[^\n]*?以\s*競價\s*拍賣",
        ]
        for pat in auc_patterns:
            auc_m = re.search(pat, all_text)
            if auc_m:
                val = self._clean_num(auc_m.group(1))
                if val:
                    result["auction_shares_k"] = val
                    break

        # 3. 萃取【公開申購張數】(散戶抽籤)
        pub_patterns = [
            r"([0-9,]+)\s*(?:千股|仟股|張)\s*以公開申購(?:配售)?辦理",
            r"供公開申購(?:配售)?之數量[：:\s]+([0-9,]+)\s*(?:千股|仟股|張)",
            r"其中[^\n]*?([0-9,]+)\s*(?:千股|仟股|張)\s*以公開申購",
            r"公開申購(?:配售)?(?:數量|股數)[：:\s]+([0-9,]+)\s*(?:千股|仟股|張)",
        ]

        for pat in pub_patterns:
            m = re.search(pat, all_text)
            if m:
                val = self._clean_num(m.group(1))
                if val:
                    result["public_subscription_shares_k"] = val
                    break

        # 4. 萃取【員工內部申購張數】(員工認購)
        emp_patterns = [
            r"(?:依規定|依法|依公司法[^\s，,]*?)?保留\s*([0-9,]+)\s*(?:千股|仟股|張)[^\n]*?(?:員工|員工內部)\s*認購",
            r"保留[^\n]*?([0-9,]+)\s*(?:千股|仟股|張)[^\n]*?由(?:公司)?員工(?:認購|承購)",
            r"供[^\n]*?員工認購\s*([0-9,]+)\s*(?:千股|仟股|張)",
            r"(?:員工|員工內部)(?:認購|承購)(?:數量|股數|張數)?[：:\s]+([0-9,]+)\s*(?:千股|仟股|張)",
            r"提撥\s*([0-9,]+)\s*(?:千股|仟股|張)[^\n]*?(?:員工|員工內部)\s*認購",
            r"由(?:公司)?員工(?:認購|承購)\s*([0-9,]+)\s*(?:千股|仟股|張)",
        ]

        for pat in emp_patterns:
            m = re.search(pat, all_text)
            if m:
                val = self._clean_num(m.group(1))
                if val:
                    result["employee_subscription_shares_k"] = val
                    break

        # 5. 萃取【券商先行保留自行認購張數】
        retention_patterns = [
            r"證券承銷商先行保留自行認購數量[：:\s]+([0-9,]+)\s*(?:千股|仟股|張)",
            r"承銷商先行保留自行認購[：:\s]+([0-9,]+)\s*(?:千股|仟股|張)",
            r"先行保留\s*([0-9,]+)\s*(?:千股|仟股|張)\s*由承銷商自行認購",
        ]

        for pat in retention_patterns:
            m = re.search(pat, all_text)
            if m:
                val = self._clean_num(m.group(1))
                if val:
                    result["underwriter_retention_shares_k"] = val
                    break

        # 6. 萃取承銷總股數
        tot_m = re.search(r"承銷之總股數為\s*([0-9,]+)\s*(?:千股|仟股|張)", all_text)
        if not tot_m:
            tot_m = re.search(r"承銷總數[：:\s]+([0-9,]+)\s*(?:千股|仟股|張)", all_text)
        if tot_m:
            result["total_underwrite_shares_k"] = self._clean_num(tot_m.group(1))

        # 7. 萃取過額配售數量 (綠鞋機制)
        green_m = re.search(r"提供已發行普通股\s*([0-9,]+)\s*(?:千股|仟股|張)[^\n]*?過額配售", all_text)
        if not green_m:
            green_m = re.search(r"預計過額配售(?:股數|數量)為\s*([0-9,]+)\s*(?:千股|仟股|張)", all_text)
        if green_m:
            result["overallotment_shares_k"] = self._clean_num(green_m.group(1))

        # 8. 判定申購類型標籤
        sub_labels = []
        if result["public_subscription_shares_k"]:
            sub_labels.append("公開申購配售")
        if result["employee_subscription_shares_k"]:
            sub_labels.append("員工內部認購")
        if result["underwriter_retention_shares_k"]:
            sub_labels.append("券商保留認購")

        if sub_labels:
            result["subscription_type"] = "+".join(sub_labels)
        else:
            result["subscription_type"] = "無公開申購(全數競拍)"

        return result

    @staticmethod
    def calculate_quant_metrics(
        auction_date: Optional[str],
        delivery_date: Optional[str],
        auction_shares: Optional[Union[int, float]],
        valid_bids_shares: Optional[Union[int, float]],
        awarded_shares: Optional[Union[int, float]],
        public_subscription_shares: Optional[Union[int, float]],
        employee_subscription_shares: Optional[Union[int, float]],
        total_underwrite_shares: Optional[Union[int, float]],
        weighted_avg_price: Optional[float],
        floor_price: Optional[float],
    ) -> Dict[str, Any]:
        """
        計算專業量化投資與避險核心指標：
        - 交割持有天數 (Days to Delivery): 避險期間與資金成本
        - 投標倍數 (Bid-to-Cover Ratio): 合格投標數量 / 總得標張數
        - 競拍配售佔比 (%): 競標張數 / 承銷總張數
        - 公開申購配售佔比 (%): 散戶籌碼分佈與首日拋壓預測
        - 員工內部申購佔比 (%): 員工持股激勵與內部籌碼分佈
        - 競拍加權溢價率 (%): 市場出價相對底價之競價狂熱度
        """
        metrics: Dict[str, Any] = {
            "holding_days": None,           # 交割持有天數 (天)
            "bid_cover_ratio": None,        # 投標倍數 (超額認購倍數)
            "auction_ratio_pct": None,      # 競拍配售佔比 (%)
            "public_sub_ratio_pct": None,   # 公開申購配售佔比 (%)
            "employee_sub_ratio_pct": None, # 員工內部申購佔比 (%)
            "auction_premium_pct": None,    # 競拍加權溢價率 (%)
        }

        # 1. 交割持有天數 (發券日 - 開標日)
        if auction_date and delivery_date:
            try:
                d_auc = datetime.strptime(str(auction_date).strip(), "%Y/%m/%d")
                d_del = datetime.strptime(str(delivery_date).strip(), "%Y/%m/%d")
                metrics["holding_days"] = (d_del - d_auc).days
            except Exception:
                pass

        # 2. 投標倍數 / 超額認購倍數 (合格投標數量 / 總得標張數)
        target_shares = awarded_shares or auction_shares
        if valid_bids_shares and target_shares and float(target_shares) > 0:
            metrics["bid_cover_ratio"] = round(float(valid_bids_shares) / float(target_shares), 2)

        # 3. 競拍配售佔比 (競標張數 / 承銷總張數)
        if auction_shares and total_underwrite_shares and float(total_underwrite_shares) > 0:
            metrics["auction_ratio_pct"] = round(
                (float(auction_shares) / float(total_underwrite_shares)) * 100, 2
            )

        # 4. 公開申購佔比 (公開申購張數 / 承銷總張數)
        if public_subscription_shares and total_underwrite_shares and float(total_underwrite_shares) > 0:
            metrics["public_sub_ratio_pct"] = round(
                (float(public_subscription_shares) / float(total_underwrite_shares)) * 100, 2
            )

        # 5. 員工內部申購佔比 (員工認購張數 / 承銷總張數)
        if employee_subscription_shares and total_underwrite_shares and float(total_underwrite_shares) > 0:
            metrics["employee_sub_ratio_pct"] = round(
                (float(employee_subscription_shares) / float(total_underwrite_shares)) * 100, 2
            )

        # 6. 競拍加權溢價率 ((加權平均價 - 底價) / 底價 * 100%)
        if weighted_avg_price and floor_price and float(floor_price) > 0:
            metrics["auction_premium_pct"] = round(
                ((float(weighted_avg_price) - float(floor_price)) / float(floor_price)) * 100, 2
            )

        return metrics

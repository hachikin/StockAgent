import re
from typing import Dict, Optional, Tuple

import requests


def detect_market(ts_code: str) -> str:
    code = str(ts_code or "").strip().upper()
    if code.endswith(".SH") or code.endswith(".SZ"):
        return "CN"
    if re.fullmatch(r"[A-Z]{1,8}(\.US)?", code):
        return "US"
    return "UNKNOWN"


def _normalize_codes(ts_code: str) -> Optional[Tuple[str, str, str]]:
    code = str(ts_code or "").strip().upper()
    market = detect_market(code)

    if market == "CN":
        if code.endswith(".SH") or code.endswith(".SZ"):
            symbol, suffix = code.split(".", 1)
            exch = suffix.lower()
            provider_code = f"{exch}{symbol}"
            return market, provider_code, provider_code

        if code.isdigit() and len(code) == 6:
            exch = "sh" if code.startswith("6") else "sz"
            provider_code = f"{exch}{code}"
            return market, provider_code, provider_code

    if market == "US":
        ticker = code.replace(".US", "")
        # 新浪美股常用 gb_aapl，腾讯美股常用 usAAPL
        return market, f"gb_{ticker.lower()}", f"us{ticker}"

    return None


def _extract_numbers(text: str):
    nums = re.findall(r"-?\d+(?:\.\d+)?", str(text or ""))
    return [float(x) for x in nums]


def get_price_sina_safe(code: str) -> Optional[Dict]:
    url = f"http://hq.sinajs.cn/list={code}"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}

    try:
        resp = requests.get(url, headers=headers, timeout=5)
        text = resp.text
        data = text.split(",")

        # A股常规格式
        if len(data) > 8:
            price = float(data[3])
            prev_close = float(data[2])
            volume = float(data[8])
            if price > 0 and prev_close > 0:
                return {"price": price, "prev_close": prev_close, "volume": volume, "source": "sina"}

        # 其他市场兜底：从文本抽数字
        nums = _extract_numbers(text)
        if len(nums) >= 4:
            # 尝试按常见顺序取最近价/昨收
            price = nums[3] if len(nums) > 3 else nums[-1]
            prev_close = nums[2] if len(nums) > 2 else nums[0]
            if price > 0 and prev_close > 0:
                volume = nums[8] if len(nums) > 8 else 0.0
                return {"price": price, "prev_close": prev_close, "volume": volume, "source": "sina"}
    except Exception:
        return None

    return None


def get_price_tencent_safe(code: str) -> Optional[Dict]:
    url = f"http://qt.gtimg.cn/q={code}"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com"}

    try:
        resp = requests.get(url, headers=headers, timeout=5)
        text = resp.text
        data = text.split("~")

        if len(data) > 6:
            price = float(data[3])
            prev_close = float(data[4])
            volume = float(data[6])
            if price > 0 and prev_close > 0:
                return {"price": price, "prev_close": prev_close, "volume": volume, "source": "tencent"}

        nums = _extract_numbers(text)
        if len(nums) >= 5:
            price = nums[3]
            prev_close = nums[4] if len(nums) > 4 else nums[2]
            if price > 0 and prev_close > 0:
                volume = nums[6] if len(nums) > 6 else 0.0
                return {"price": price, "prev_close": prev_close, "volume": volume, "source": "tencent"}
    except Exception:
        return None

    return None


def get_realtime_price(ts_code: str) -> Optional[Dict]:
    """新浪优先、腾讯备用，统一输入 ts_code/ticker，返回 price/prev_close/volume/source/market。"""
    normalized = _normalize_codes(ts_code)
    if not normalized:
        return None

    market, sina_code, tencent_code = normalized

    quote = get_price_sina_safe(sina_code)
    if quote:
        quote["market"] = market
        quote["provider_code"] = sina_code
        return quote

    quote = get_price_tencent_safe(tencent_code)
    if quote:
        quote["market"] = market
        quote["provider_code"] = tencent_code
        return quote

    return None

import json
import os
import re

import tushare as ts
import config
from infra.redis_store import get_json, set_json

# 初始化
ts.set_token(config.TUSHARE_TOKEN)
pro = ts.pro_api()

# 本地别名文件，可手工维护名称到代码映射
ALIAS_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "storage", "stock_aliases.json"))
# 代码内置最小兜底映射（支持常见美股中文名）
DEFAULT_STOCK_ALIASES = {
    "山子高科": "000981.SZ",
    "特斯拉": "TSLA",
    "苹果": "AAPL",
    "苹果公司": "AAPL",
    "英伟达": "NVDA",
    "微软": "MSFT",
    "亚马逊": "AMZN",
    "谷歌": "GOOGL",
    "脸书": "META",
    "Meta": "META",
}


def _load_stock_basic():
    cache_key = "stock_basic_map"
    cached = get_json(cache_key)
    if cached:
        return cached

    df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name")

    by_ts = {}
    by_symbol = {}
    by_name = {}

    for _, row in df.iterrows():
        ts_code = str(row.get("ts_code", "")).strip().upper()
        symbol = str(row.get("symbol", "")).strip().upper()
        name = str(row.get("name", "")).strip()

        if ts_code:
            by_ts[ts_code] = {"ts_code": ts_code, "symbol": symbol, "name": name}
        if symbol:
            by_symbol[symbol] = {"ts_code": ts_code, "symbol": symbol, "name": name}
        if name:
            by_name[name] = {"ts_code": ts_code, "symbol": symbol, "name": name}

    result = {"by_ts": by_ts, "by_symbol": by_symbol, "by_name": by_name}
    set_json(cache_key, result, ex=86400)
    return result


def _load_aliases_from_json():
    if not os.path.exists(ALIAS_FILE):
        return {}
    try:
        with open(ALIAS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}

    cleaned = {}
    for k, v in data.items():
        key = str(k).strip()
        val = str(v).strip().upper()
        if key and val:
            cleaned[key] = val
    return cleaned


def _build_alias_map():
    alias_map = dict(DEFAULT_STOCK_ALIASES)
    alias_map.update(getattr(config, "STOCK_ALIASES", {}))
    alias_map.update(_load_aliases_from_json())
    return alias_map


def _is_us_ticker(raw: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]{1,8}", str(raw or "").upper()))


def _fallback_by_alias(raw):
    ts_code = _build_alias_map().get(raw, "")
    if ts_code:
        code = ts_code.upper()
        symbol = code.split(".")[0]
        return {"ts_code": code, "symbol": symbol, "name": raw}
    return {"ts_code": "", "symbol": "", "name": raw}


def resolve_stock(stock_input):
    """将名称/6位代码/ts_code/美股代码统一解析为可查询代码。优先使用Tushare映射。"""
    raw = str(stock_input or "").strip()
    if not raw:
        return {"input": raw, "ts_code": "", "symbol": "", "name": ""}

    fallback = _fallback_by_alias(raw)
    upper_raw = raw.upper()

    mapping = None
    try:
        mapping = _load_stock_basic()
    except Exception:
        mapping = None

    # 1) 优先走Tushare映射（A股最可靠）
    if mapping:
        by_ts = mapping.get("by_ts", {})
        by_symbol = mapping.get("by_symbol", {})
        by_name = mapping.get("by_name", {})

        if upper_raw in by_ts:
            return by_ts[upper_raw]

        if raw.isdigit() and len(raw) == 6 and raw in by_symbol:
            return by_symbol[raw]

        if raw in by_name:
            return by_name[raw]

        for name, info in by_name.items():
            if raw in name or name in raw:
                return info

    # 2) 别名兜底（含常见美股中文名）
    if fallback.get("ts_code"):
        return fallback

    # 3) A股代码规则
    if upper_raw.endswith(".SZ") or upper_raw.endswith(".SH"):
        return {"ts_code": upper_raw, "symbol": upper_raw.split(".")[0], "name": raw}

    if raw.isdigit() and len(raw) == 6:
        ts_code = f"{raw}.SH" if raw.startswith("6") else f"{raw}.SZ"
        return {"ts_code": ts_code, "symbol": raw, "name": raw}

    # 4) 美股代码规则：AAPL / TSLA / MSFT 等
    if upper_raw.endswith(".US") and _is_us_ticker(upper_raw[:-3]):
        ticker = upper_raw[:-3]
        return {"ts_code": ticker, "symbol": ticker, "name": raw}

    if _is_us_ticker(upper_raw):
        return {"ts_code": upper_raw, "symbol": upper_raw, "name": raw}

    return {"ts_code": "", "symbol": "", "name": raw}


def _get_daily_from_yfinance(code):
    import yfinance as yf

    df = yf.download(code, period="1y", interval="1d", progress=False, auto_adjust=False)
    if df is None or df.empty:
        return {}

    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = [c[0] for c in df.columns]

    if "Close" not in df.columns:
        return {}

    df = df.reset_index().rename(columns={"Date": "trade_date"})
    df = df.sort_values("trade_date")
    return df.to_dict()


def get_daily(code):
    cache_key = f"daily:{code}"

    cached = get_json(cache_key)
    if cached:
        return cached

    print("请求行情数据...")

    result = {}
    try:
        df = pro.daily(ts_code=code)
        if df is None or df.empty:
            raise ValueError("empty daily data from tushare")

        df = df.sort_values("trade_date")
        df = df.rename(columns={"close": "Close"})
        if "Close" not in df.columns:
            raise ValueError("missing close column from tushare")

        result = df.to_dict()
    except Exception:
        # Tushare权限不足或非A股代码时回退到yfinance
        result = _get_daily_from_yfinance(code)

    set_json(cache_key, result, ex=3600)
    return result

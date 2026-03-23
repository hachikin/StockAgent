import re
from datetime import datetime

from tools.tushare_tool import get_daily, resolve_stock

SKILL_NAME = "current_price_skill"
SKILL_DESCRIPTION = "查询股票最新可得价格（当前价/现价/最新价）"

PRICE_PATTERN = re.compile(
    r"(当前价|现价|最新价|最新价格|股价|price|quote|last)", re.IGNORECASE
)


def can_handle(text: str) -> bool:
    return bool(PRICE_PATTERN.search(str(text or "")))


def _extract_stock_input(text: str) -> str:
    t = str(text or "").strip()

    # 优先提取规范代码，例如 600519.SH / 000001.SZ / AAPL / TSLA.US
    m_code = re.search(r"([A-Za-z]{1,6}(?:\.US)?|\d{6}(?:\.(?:SH|SZ))?)", t, re.IGNORECASE)
    if m_code:
        return m_code.group(1)

    # 再尝试提取中文名称片段
    t = re.sub(r"(请问|帮我|查询|查一下|看一下|告诉我|一下)", "", t)
    t = re.sub(r"(的)?(当前价|现价|最新价|最新价格|股价)", "", t, flags=re.IGNORECASE)
    t = t.strip(" ：:，,。？?！!\n\t")
    return t


def _safe_float(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except Exception:
        return None


def _normalize_daily_rows(data):
    if not data:
        return []

    # pandas.DataFrame.to_dict() 常见结构：{col: {idx: value}}
    if isinstance(data, dict):
        vals = list(data.values())
        if vals and isinstance(vals[0], dict):
            idxs = set()
            for col_map in vals:
                idxs.update(col_map.keys())
            rows = []
            for idx in sorted(idxs, key=lambda x: str(x)):
                row = {k: v.get(idx) for k, v in data.items() if isinstance(v, dict)}
                rows.append(row)
            return rows
        return [data]

    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]

    return []


def _pick_latest(rows):
    if not rows:
        return {}

    def _date_key(row):
        d = str(row.get("trade_date") or row.get("date") or "")
        d = d.replace("-", "")
        return d

    rows = sorted(rows, key=_date_key)
    return rows[-1] if rows else {}


def run(text: str, context=None):
    stock_input = _extract_stock_input(text)
    if not stock_input:
        return "请告诉我要查询的股票代码或名称。"

    resolved = resolve_stock(stock_input)
    ts_code = str(resolved.get("ts_code", "")).strip()
    stock_name = str(resolved.get("name") or stock_input).strip()

    if not ts_code:
        return f"❌ 未识别到有效股票：{stock_input}，请提供代码（如 600519 或 AAPL）。"

    data = get_daily(ts_code)
    rows = _normalize_daily_rows(data)
    latest = _pick_latest(rows)

    close_v = _safe_float(latest.get("Close") or latest.get("close"))
    pct_v = _safe_float(latest.get("pct_chg") or latest.get("pct_change"))
    trade_date = str(latest.get("trade_date") or latest.get("date") or "")

    if close_v is None:
        return f"⚠️ {stock_name}({ts_code}) 暂无可用价格数据，请稍后重试。"

    date_text = trade_date
    if trade_date.isdigit() and len(trade_date) == 8:
        try:
            date_text = datetime.strptime(trade_date, "%Y%m%d").strftime("%Y-%m-%d")
        except Exception:
            pass

    pct_text = f"，涨跌幅 {pct_v:.2f}%" if pct_v is not None else ""
    return (
        f"{stock_name}({ts_code}) 最新可得价格：{close_v:.2f}"
        f"（交易日 {date_text or '未知'}{pct_text}）。\n"
        "注：该值基于最新可得日线数据，盘中实时价格可能存在偏差。"
    )

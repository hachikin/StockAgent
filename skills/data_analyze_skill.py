import config

from llm import get_llm
from tools.indicator_tool import calc_indicators
from tools.tushare_tool import get_daily, resolve_stock

SKILL_NAME = "data_analyze_skill"
SKILL_DESCRIPTION = "拉取日线并做技术面分析（输入股票代码）"

tech_llm = get_llm(model=config.MODEL, temperature=0, timeout=25, max_retries=1)


def _fallback_technical_text(indicators: dict) -> str:
    indicators = indicators or {}
    signals = indicators.get("signals") or {}
    latest = indicators.get("latest") or {}

    summary = signals.get("summary", "技术面信号不足")
    close = latest.get("close")
    rsi = latest.get("rsi14")
    macd = latest.get("macd")

    return (
        f"技术面摘要：{summary}。"
        f"最新收盘价={close}，RSI14={rsi}，MACD={macd}。"
    )


def _analyze_technical_with_llm(stock_label: str, indicators: dict) -> str:
    prompt = f"""
你是股票技术分析师。请仅基于给定的日线技术指标数据，输出简洁的技术面结论。

股票：{stock_label}
技术指标数据：
{indicators}

请输出：
1) 短线趋势
2) 中线趋势
3) 长期趋势
4) 动量与波动判断（RSI/MACD/均线/布林带）
5) 关键风险点
6) 给出后续需要观察的技术指标和价位
7) 技术面结论（偏多/中性/偏空 + 技术指标依据）
"""
    return str(tech_llm.invoke(prompt).content).strip()


def run(stock_code):
    try:
        resolved = resolve_stock(stock_code)
        ts_code = resolved.get("ts_code", "")
        stock_name = resolved.get("name") or str(stock_code)

        if not ts_code:
            return {
                "data": None,
                "indicators": None,
                "technical_analysis": "未识别到有效股票代码，无法进行技术面分析。",
                "resolved": resolved,
                "frequency": "1d",
            }

        data = get_daily(ts_code)
        if not data:
            return {
                "data": None,
                "indicators": None,
                "technical_analysis": "未获取到有效日线行情数据，无法进行技术面分析。",
                "resolved": resolved,
                "frequency": "1d",
            }

        lookback_days = int(getattr(config, "TECH_LOOKBACK_DAYS", 30))
        indicators = calc_indicators(data, recent_days=lookback_days)

        try:
            technical_analysis = _analyze_technical_with_llm(f"{stock_name} ({ts_code})", indicators)
        except Exception:
            technical_analysis = _fallback_technical_text(indicators)

        return {
            "data": data,
            "indicators": indicators,
            "technical_analysis": technical_analysis,
            "resolved": resolved,
            "frequency": "1d",
        }
    except Exception:
        return {
            "data": None,
            "indicators": None,
            "technical_analysis": "技术面分析暂不可用，请稍后重试。",
            "resolved": {"ts_code": "", "symbol": "", "name": str(stock_code)},
            "frequency": "1d",
        }

import config

from llm import get_llm
from tools.news_tool import get_news
from tools.tushare_tool import resolve_stock

SKILL_NAME = "news_analyze_skill"
SKILL_DESCRIPTION = "输入股票代码，输出消息面分析"

llm = get_llm(model=config.MODEL, temperature=0, timeout=25, max_retries=1)


def run(stock_code):
    resolved = resolve_stock(stock_code)
    ts_code = resolved.get("ts_code", "")
    stock_name = resolved.get("name") or str(stock_code)

    if not ts_code:
        return "新闻情绪：中性\n原因：未识别到有效股票代码，无法执行消息面分析。"

    payload = get_news(stock_name, ts_code)
    news_list = [str(x).strip() for x in (payload.get("news") or []) if str(x).strip()]
    if not news_list:
        return "新闻情绪：中性\n原因：未检索到高相关度新闻，建议结合公告与盘面再判断。"

    text = "\n".join(f"- {n}" for n in news_list[:8])
    prompt = f"""
你是A股研究员。请基于以下与 {stock_name} 相关的新闻，输出消息面结论：

1) 趋势：利好/利空/中性（三选一）
2) 关键驱动：最多5条
3) 风险提示：最多5条
4) 利好催化：最多5条
5) 一段话结论：不超过150字

新闻列表：
{text}
"""

    try:
        return str(llm.invoke(prompt).content).strip()
    except Exception as e:
        return f"新闻情绪：中性\n原因：新闻解析失败（{e.__class__.__name__}），已降级为技术面主导。"

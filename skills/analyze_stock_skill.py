import re
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait

import config
from llm import get_llm

from skills.data_analyze_skill import run as data_run
from skills.news_analyze_skill import run as news_run
from skills.watchlist_skill import handle_action as watchlist_handle_action
from tools.tushare_tool import resolve_stock

SKILL_NAME = "analyze_stock_skill"
SKILL_DESCRIPTION = "股票分析能力：单股分析、批量分析监控列表"

BATCH_STOCK_TIMEOUT = int(getattr(config, "BATCH_STOCK_TIMEOUT", 60))
BATCH_CHUNK_MAX_CHARS = int(getattr(config, "BATCH_CHUNK_MAX_CHARS", 1400))
STAGE_TASK_TIMEOUT = int(getattr(config, "STAGE_TASK_TIMEOUT", 180))

final_llm = get_llm(model=config.MODEL, temperature=0, timeout=45, max_retries=1)


def can_handle(text: str) -> bool:
    t = str(text or "")
    return bool(re.search(r"(分析|诊断|analyze|监控列表|自选|关注列表)", t, re.IGNORECASE))


def _build_news_analysis(ts_code: str):
    return news_run(ts_code)


def _fallback_final_text(stock_label: str, technical_analysis: str, news_analysis: str) -> str:
    return (
        f"{stock_label} 综合分析：\n"
        f"技术面要点：{technical_analysis}\n"
        f"消息面要点：{news_analysis}\n"
        "结论：当前采用技术面与消息面并行评估，请结合仓位与风险承受能力决策。"
    )


def _compose_final_analysis(stock_label: str, technical_analysis: str, news_analysis: str) -> str:
    prompt = f"""
你是一名股票交易分析师，请基于以下信息输出最终结论。

股票：{stock_label}

技术面分析：
{technical_analysis}

消息面分析：
{news_analysis}

请输出：
- 趋势结论（结合技术面+消息面）
- 风险（至少2条）
- 建议（买入/观望/卖出三选一，后续需要注意的情况）
- 结论依据
"""
    try:
        return str(final_llm.invoke(prompt).content).strip()
    except Exception:
        return _fallback_final_text(stock_label, technical_analysis, news_analysis)


def _analyze_one(stock_input: str) -> str:
    resolved = resolve_stock(stock_input)
    ts_code = resolved.get("ts_code", "")
    stock_name = resolved.get("name") or str(stock_input)

    if not ts_code:
        return f"❌ 未找到股票：{stock_input}，请检查名称或代码"

    with ThreadPoolExecutor(max_workers=2) as executor:
        start = time.monotonic()
        future_data = executor.submit(data_run, ts_code)
        future_news = executor.submit(_build_news_analysis, ts_code)

        data_res = {}
        news_analysis = "新闻情绪：中性\n原因：新闻链路超时，已降级处理。"
        done, not_done = wait([future_data, future_news], timeout=STAGE_TASK_TIMEOUT)
        if future_data in done:
            try:
                data_res = future_data.result()
            except Exception as e:
                print(f"[analysis_perf] data_stage fallback: {e.__class__.__name__}", flush=True)
                data_res = {}
        else:
            print("[analysis_perf] data_stage fallback: TimeoutError", flush=True)

        if future_news in done:
            try:
                news_analysis = future_news.result()
            except Exception as e:
                print(f"[analysis_perf] news_stage fallback: {e.__class__.__name__}", flush=True)
                news_analysis = f"新闻情绪：中性\n原因：新闻链路失败（{e.__class__.__name__}），已降级处理。"
        else:
            print("[analysis_perf] news_stage fallback: TimeoutError", flush=True)

        for fut in not_done:
            fut.cancel()

        stage_ms = int((time.monotonic() - start) * 1000)
        print(
            f"[analysis_perf] parallel_stage stock={stock_name}({ts_code}) ms={stage_ms} "
            f"data_done={future_data in done} news_done={future_news in done}",
            flush=True,
        )

    technical_analysis = data_res.get("technical_analysis") or "技术面分析暂无有效结论"
    result = _compose_final_analysis(f"{stock_name} ({ts_code})", technical_analysis, news_analysis)
    return f"{stock_name}({ts_code}) 分析结果:\n{result}"


def _analyze_one_with_timeout(stock_input: str, timeout_sec: int) -> str:
    if timeout_sec <= 0:
        return _analyze_one(stock_input)

    if threading.current_thread() is not threading.main_thread():
        return _analyze_one(stock_input)

    def _timeout_handler(signum, frame):
        raise TimeoutError("single stock analyze timeout")

    old_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout_sec)
    try:
        return _analyze_one(stock_input)
    except TimeoutError:
        return f"⏱️ {stock_input} 分析超时（>{timeout_sec}s），已跳过该股票"
    except Exception as e:
        return f"❌ {stock_input} 分析失败：{e.__class__.__name__}"
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def _chunk_batch_results(parts, max_chars=BATCH_CHUNK_MAX_CHARS):
    chunks = []
    current = ""

    for p in parts:
        p = str(p or "").strip()
        if not p:
            continue

        block = p if not current else f"\n\n{p}"
        if len(current) + len(block) <= max_chars:
            current += block
        else:
            if current:
                chunks.append(current)
            current = p

    if current:
        chunks.append(current)

    return chunks


def _analyze_watchlist():
    list_text = watchlist_handle_action("list", "")
    if "当前没有监控股票" in list_text or "当前监控列表为空" in list_text:
        return ["当前监控列表为空，先添加股票再分析"]

    items = [x.strip() for x in str(list_text).splitlines()[1:] if x.strip()]
    if not items:
        return ["当前监控列表为空，先添加股票再分析"]

    outputs = []
    total = len(items)
    for idx, item in enumerate(items, start=1):
        header = f"[{idx}/{total}]"
        content = _analyze_one_with_timeout(item, BATCH_STOCK_TIMEOUT)
        outputs.append(f"{header} {content}")

    return _chunk_batch_results(outputs)


def _is_explicit_batch_watchlist_intent(text: str) -> bool:
    t = str(text or "").strip()
    return bool(re.search(r"(批量|逐个|挨个).*(分析|诊断).*(监控列表|自选|关注列表)", t))


def _is_watchlist_analyze_non_batch(text: str) -> bool:
    t = str(text or "").strip()
    return bool(
        re.search(r"(分析|诊断)", t)
        and re.search(r"(监控列表|自选|关注列表)", t)
        and not _is_explicit_batch_watchlist_intent(t)
    )


def run(text: str, context=None):
    t = str(text or "").strip()

    if _is_explicit_batch_watchlist_intent(t):
        return _analyze_watchlist()

    if _is_watchlist_analyze_non_batch(t):
        return "如需批量分析，请说：批量分析监控列表中的股票。若只分析一只，请直接说：分析 + 股票名/代码。"

    m = re.search(r"(分析|诊断)\s*(一下|一下子|下)?\s*(.+)", t)
    if m:
        stock = m.group(3).strip()
        stock = re.sub(r"^(帮我|请|麻烦|给我)", "", stock).strip()
        return _analyze_one(stock)

    if re.fullmatch(r"[A-Za-z0-9.\-\u4e00-\u9fa5]{2,20}", t):
        return _analyze_one(t)

    return "请告诉我要分析的股票代码或名称"

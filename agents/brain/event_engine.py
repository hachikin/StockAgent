"""Brain 事件层。

职责：
1) 对 news/market 事件做动作决策。
2) 执行推送策略（含按情绪聚合，避免刷屏）。
3) 暴露 handle_event 作为事件入口。
"""

import threading

import config

from infra.redis_store import exists, set_text
from llm import get_llm
from notifier.feishu import send

from .utils import extract_json
from .memory import record_chat_turn

_EVENT_LLM = get_llm(model=config.MODEL, temperature=0, timeout=20, max_retries=1)
_EVENT_PUSH_TTL = int(getattr(config, "NEWS_PUSH_TTL_SECONDS", 900))


def _event_decide(event: dict):
    """让 LLM 判断事件是推送、忽略，还是先做进一步技能分析。"""
    prompt = f"""
当前检测到事件：
类型：{event.get('type', '')}
股票：{event.get('stocks', [])}
情绪：{event.get('sentiment', '')}
原因：{event.get('reason', '')}
置信度：{event.get('confidence', 0)}

请判断：
1）是否值得推送
2）是否需要进一步分析（例如调用技术面）
3）最终建议

只输出 JSON：
{{
  "action": "push" 或 "ignore" 或 "call_skill",
  "reason": "...",
  "suggestion": "..."
}}
"""
    try:
        out = _EVENT_LLM.invoke(prompt).content
        data = extract_json(out)
        if isinstance(data, dict):
            action = str(data.get("action", "ignore")).strip().lower()
            if action not in {"push", "ignore", "call_skill"}:
                action = "ignore"
            return {
                "action": action,
                "reason": str(data.get("reason", "")).strip(),
                "suggestion": str(data.get("suggestion", "")).strip(),
            }
    except Exception:
        pass

    return {"action": "ignore", "reason": "decision_fallback", "suggestion": ""}


def _event_policy_allow(event: dict) -> bool:
    """策略兜底：低置信度且非事件时直接拦截。"""
    try:
        conf = float(event.get("confidence", 0) or 0)
    except Exception:
        conf = 0.0
    return conf > 0.6 or bool(event.get("is_event", False))


def _event_allow_push_stock(stock: str) -> bool:
    """按股票维度做短期限流，防止同一标的重复打扰。"""
    key = f"push:{stock}"
    if exists(key):
        return False
    return set_text(key, "1", ex=_EVENT_PUSH_TTL)


def _event_run_analyze_skill(stocks):
    """需要时触发分析 skill，补充解释依据。"""
    try:
        from skills.analyze_stock_skill import run as analyze_run
    except Exception as e:
        return f"技能调用失败：{e.__class__.__name__}"

    outputs = []
    for code in list(stocks or [])[:2]:
        try:
            outputs.append(str(analyze_run(f"分析 {code}")).strip())
        except Exception as e:
            outputs.append(f"{code} 技能失败：{e.__class__.__name__}")
    return "\n\n".join(outputs).strip()


_EVENT_GROUP_WINDOW_SECONDS = int(getattr(config, "EVENT_GROUP_WINDOW_SECONDS", 8))
_EVENT_GROUP_LOCK = threading.Lock()
_EVENT_GROUP_TIMER = None
_EVENT_GROUP_BUCKETS = {
    "bullish": {"stocks": set(), "reasons": []},
    "bearish": {"stocks": set(), "reasons": []},
}


def _event_flush_grouped_messages():
    """定时 flush：把窗口期内同情绪事件聚合成一条消息发送。"""
    global _EVENT_GROUP_TIMER, _EVENT_GROUP_BUCKETS

    with _EVENT_GROUP_LOCK:
        buckets = _EVENT_GROUP_BUCKETS
        _EVENT_GROUP_BUCKETS = {
            "bullish": {"stocks": set(), "reasons": []},
            "bearish": {"stocks": set(), "reasons": []},
        }
        _EVENT_GROUP_TIMER = None

    for sentiment in ("bullish", "bearish"):
        stocks = sorted(list(buckets[sentiment]["stocks"]))
        if not stocks:
            continue

        sentiment_cn = "利好" if sentiment == "bullish" else "利空"
        reasons = [str(x).strip() for x in buckets[sentiment]["reasons"] if str(x).strip()]
        reason_text = "；".join(reasons[:3]) if reasons else "无"

        msg = (
            f"🚨 新闻异动（聚合）\n"
            f"方向：{sentiment_cn}\n"
            f"标的：{'、'.join(stocks)}\n"
            f"原因：{reason_text}"
        )
        send(msg)


def _event_collect_grouped_push(event: dict, decision: dict, skill_output: str = "") -> int:
    """把本次事件并入聚合桶，并在首次入桶时启动定时发送。"""
    global _EVENT_GROUP_TIMER

    _ = decision
    _ = skill_output

    sentiment = str(event.get("sentiment", "neutral")).strip().lower()
    if sentiment not in {"bullish", "bearish"}:
        return 0

    stocks = [str(x).strip() for x in (event.get("stocks") or []) if str(x).strip()]
    allowed_stocks = [s for s in stocks if _event_allow_push_stock(s)]
    if not allowed_stocks:
        return 0

    reason = str(event.get("reason", "")).strip()

    with _EVENT_GROUP_LOCK:
        _EVENT_GROUP_BUCKETS[sentiment]["stocks"].update(allowed_stocks)
        if reason:
            _EVENT_GROUP_BUCKETS[sentiment]["reasons"].append(reason)

        if _EVENT_GROUP_TIMER is None:
            t = threading.Timer(_EVENT_GROUP_WINDOW_SECONDS, _event_flush_grouped_messages)
            t.daemon = True
            _EVENT_GROUP_TIMER = t
            _EVENT_GROUP_TIMER.start()

    return len(allowed_stocks)


def handle_event(event: dict, user_id: str = "system"):
    """统一事件入口：决策 -> 策略拦截 -> 可选 skill -> 聚合推送。"""
    if not isinstance(event, dict):
        return {"ok": False, "reason": "invalid_event"}

    event_type = str(event.get("type", "")).strip()
    if event_type not in {"news_signal", "market_signal"}:
        return {"ok": False, "reason": "unsupported_event"}

    stocks = [str(x).strip() for x in (event.get("stocks") or []) if str(x).strip()]
    if not stocks:
        # 兼容旧格式 market event（单 code 字段）。
        code = str(event.get("code", "")).strip()
        if code:
            stocks = [code]
            event["stocks"] = stocks

    if not stocks:
        return {"ok": False, "reason": "empty_stocks"}

    decision = _event_decide(event)
    action = decision.get("action", "ignore")
    if action == "ignore":
        return {"ok": True, "action": "ignore", "reason": decision.get("reason", "")}

    if not _event_policy_allow(event):
        return {"ok": True, "action": "ignore", "reason": "policy_blocked"}

    skill_output = ""
    if action == "call_skill":
        skill_output = _event_run_analyze_skill(stocks)

    queued = _event_collect_grouped_push(event, decision, skill_output=skill_output)
    sent = 0

    record_chat_turn(
        user_id,
        f"[event] {event_type} stocks={stocks}",
        f"[decision] action={action} queued={queued} sent={sent} reason={decision.get('reason', '')}",
    )

    return {
        "ok": True,
        "action": action,
        "queued": queued,
        "sent": sent,
        "stocks": stocks,
        "reason": decision.get("reason", ""),
    }

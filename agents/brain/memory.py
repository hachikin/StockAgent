"""brain 会话记忆与摘要模块。"""

import threading

import config

from infra.redis_store import get_json, set_json
from llm import get_llm

CHAT_WINDOW_SIZE = int(getattr(config, "BRAIN_CHAT_WINDOW", 4))
CHAT_MEM_TTL = int(getattr(config, "BRAIN_CHAT_TTL_SECONDS", 7 * 24 * 3600))
SUMMARY_BATCH_SIZE = int(getattr(config, "BRAIN_SUMMARY_BATCH_SIZE", 12))

_summary_llm = get_llm(model=config.MODEL, temperature=0, timeout=15, max_retries=1)

_SUMMARY_LOCK = threading.Lock()
_SUMMARY_PENDING = {}
_SUMMARY_RUNNING = set()


def _mem_key(user_id: str):
    uid = str(user_id or "default").strip() or "default"
    return f"brain_chat_memory:{uid}"


def load_memory(user_id: str):
    mem = get_json(_mem_key(user_id))
    if isinstance(mem, dict):
        mem.setdefault("summary", "")
        mem.setdefault("messages", [])
        return mem
    return {"summary": "", "messages": []}


def _save_memory(user_id: str, mem: dict):
    set_json(_mem_key(user_id), mem, ex=CHAT_MEM_TTL)


def _fallback_summary(summary: str, messages):
    lines = []
    for m in messages[-8:]:
        role = str(m.get("role", "unknown"))
        content = str(m.get("content", "")).strip()
        if not content:
            continue
        lines.append(f"{role}: {content[:80]}")

    add = " | ".join(lines)
    if not add:
        return summary

    if summary:
        merged = f"{summary} || {add}"
    else:
        merged = add
    return merged[:1200]


def _summarize(summary: str, messages):
    if not messages:
        return summary

    history = "\n".join(
        f"{m.get('role', 'unknown')}: {str(m.get('content', '')).strip()}" for m in messages
    )
    prompt = f"""
你是会话摘要助手。请将新增对话压缩为简洁摘要，保留：
- 用户偏好与关注点
- 已确认的结论和约束
- 后续应保持的一致口径

已有摘要：
{summary}

新增对话：
{history}
"""
    try:
        result = str(_summary_llm.invoke(prompt).content).strip()
        return result or _fallback_summary(summary, messages)
    except Exception:
        return _fallback_summary(summary, messages)


def _summary_worker(user_id: str):
    while True:
        with _SUMMARY_LOCK:
            pending = _SUMMARY_PENDING.get(user_id, [])
            if not pending:
                _SUMMARY_PENDING.pop(user_id, None)
                _SUMMARY_RUNNING.discard(user_id)
                return
            batch = pending[:SUMMARY_BATCH_SIZE]
            del pending[: len(batch)]

        mem = load_memory(user_id)
        old_summary = mem.get("summary", "")
        new_summary = _summarize(old_summary, batch)
        if new_summary != old_summary:
            mem["summary"] = new_summary
            _save_memory(user_id, mem)


def _enqueue_summary(user_id: str, old_messages):
    if not old_messages:
        return

    with _SUMMARY_LOCK:
        queue = _SUMMARY_PENDING.setdefault(user_id, [])
        queue.extend(old_messages)
        if user_id in _SUMMARY_RUNNING:
            return
        _SUMMARY_RUNNING.add(user_id)

    threading.Thread(target=_summary_worker, args=(user_id,), daemon=True).start()


def record_chat_turn(user_id: str, user_text: str, assistant_text: str):
    mem = load_memory(user_id)
    messages = mem.get("messages", [])

    if user_text:
        messages.append({"role": "user", "content": str(user_text).strip()})
    if assistant_text:
        messages.append({"role": "assistant", "content": str(assistant_text).strip()})

    overflow = []
    if len(messages) > CHAT_WINDOW_SIZE:
        overflow = messages[:-CHAT_WINDOW_SIZE]
        messages = messages[-CHAT_WINDOW_SIZE:]

    mem["messages"] = messages
    _save_memory(user_id, mem)
    _enqueue_summary(user_id, overflow)

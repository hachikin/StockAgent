import importlib.util
import json
import re
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request

from bot.market_bot import start_market_watcher
from bot.news_bot import start_news_watcher
from notifier.feishu import reply_message

app = Flask(__name__)

_SEEN_MESSAGE_IDS = {}
_SEEN_LOCK = threading.Lock()
_SEEN_TTL_SECONDS = 10 * 60

_BRAIN_HANDLER_LOCK = threading.Lock()
_BRAIN_HANDLER_MTIME_NS = None
_BRAIN_HANDLER_FN = None


def _load_brain_handler():
    """Hot-reload brain handler when agents/brain_agent.py changes on disk."""
    global _BRAIN_HANDLER_MTIME_NS, _BRAIN_HANDLER_FN

    brain_path = Path(__file__).resolve().parent.parent / "agents" / "brain_agent.py"
    current_mtime = brain_path.stat().st_mtime_ns

    with _BRAIN_HANDLER_LOCK:
        if _BRAIN_HANDLER_FN is not None and _BRAIN_HANDLER_MTIME_NS == current_mtime:
            return _BRAIN_HANDLER_FN

        module_name = f"agents.dynamic_brain_agent_{current_mtime}"
        spec = importlib.util.spec_from_file_location(module_name, str(brain_path))
        if not spec or not spec.loader:
            raise RuntimeError("failed to build brain agent spec")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        fn = getattr(module, "handle_user_message", None)
        if not callable(fn):
            raise RuntimeError("brain agent missing callable handle_user_message")

        _BRAIN_HANDLER_MTIME_NS = current_mtime
        _BRAIN_HANDLER_FN = fn
        return _BRAIN_HANDLER_FN


def _send_reply_segments(message_id, reply):
    if isinstance(reply, list):
        total = len(reply)
        ok = True
        for i, part in enumerate(reply, start=1):
            text = str(part or "").strip()
            if not text:
                continue
            if total > 1:
                text = f"【批量分析 {i}/{total}】\n{text}"
            ok = reply_message(message_id, text) and ok
        return ok

    return reply_message(message_id, str(reply))


def _mark_message_seen(message_id: str) -> bool:
    now = time.time()
    with _SEEN_LOCK:
        expired = [k for k, ts in _SEEN_MESSAGE_IDS.items() if now - ts > _SEEN_TTL_SECONDS]
        for k in expired:
            _SEEN_MESSAGE_IDS.pop(k, None)

        if not message_id:
            return True

        if message_id in _SEEN_MESSAGE_IDS:
            return False

        _SEEN_MESSAGE_IDS[message_id] = now
        return True


def _build_quick_ack(text: str) -> str:
    t = str(text or "").strip()

    if re.search(r"(加入|添加|关注|删除|移除|取消关注)", t, re.IGNORECASE):
        return "收到，马上修改监控列表。"

    if re.search(r"(查看|列表|清单|监控列表|自选|watchlist|list)", t, re.IGNORECASE):
        return "收到，马上查询监控列表。"

    if re.search(r"(分析|诊断|analyze)", t, re.IGNORECASE):
        return "收到，马上分析，请稍候。"

    return ""


def _process_user_message(message_id: str, user_id: str, text: str):
    quick_ack = _build_quick_ack(text)
    if quick_ack:
        reply_message(message_id, quick_ack)

    try:
        handle_user_message = _load_brain_handler()
        reply = handle_user_message(text, user_id=user_id)
    except Exception as e:
        reply = f"服务暂时不可用: {e.__class__.__name__}"

    sent = _send_reply_segments(message_id, reply)
    if not sent:
        print("飞书回发失败：请检查 FEISHU_WEBHOOK 或 FEISHU_APP_ID/FEISHU_APP_SECRET 配置")


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}

    if data.get("type") == "url_verification":
        return jsonify({"challenge": data.get("challenge", "")})

    event = data.get("event", {})
    sender_type = event.get("sender", {}).get("sender_type", "")
    if sender_type == "app":
        return "ok"

    message = event.get("message", {})
    message_id = message.get("message_id", "")

    sender = event.get("sender", {})
    sender_id = sender.get("sender_id", {}) or {}
    user_id = sender_id.get("open_id") or sender_id.get("user_id") or sender_id.get("union_id") or "default"
    try:
        content = message.get("content", "")
        text = json.loads(content).get("text", "").strip()
    except Exception:
        return "ok"

    if not text:
        return "ok"

    print(f"收到消息: {text}")

    if not _mark_message_seen(message_id):
        print(f"忽略重复消息: {message_id}")
        return "ok"

    threading.Thread(target=_process_user_message, args=(message_id, user_id, text), daemon=True).start()
    return "ok"


if __name__ == "__main__":
    start_market_watcher()
    start_news_watcher()
    app.run(host="0.0.0.0", port=8000)

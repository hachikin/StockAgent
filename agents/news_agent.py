import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List

import config

from events.queue import get_news_queue_len, get_news_window, trim_news_processed
from llm import get_llm


class NewsAgent:
    """消费新闻队列并做批量分析，再将事件交给 brain_agent 决策。"""

    def __init__(self, event_handler: Callable[[Dict[str, Any]], Dict[str, Any]]):
        self.event_handler = event_handler
        self.window_size = int(getattr(config, "NEWS_WINDOW_SIZE", 20))
        self.trim_size = int(getattr(config, "NEWS_WINDOW_TRIM_SIZE", 15))
        self.max_batches_per_trigger = int(getattr(config, "NEWS_MAX_BATCHES_PER_TRIGGER", 3))
        self._llm = get_llm(model=config.MODEL, temperature=0, timeout=25, max_retries=1)
        self._process_lock = threading.Lock()

        root = Path(__file__).resolve().parent.parent
        self.log_dir = root / "log"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / "news_agent.log"

    @staticmethod
    def _extract_json(text: str):
        s = str(text or "").strip()
        if not s:
            return None

        s = re.sub(r"^```json\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"```$", "", s).strip()

        try:
            return json.loads(s)
        except Exception:
            pass

        m = re.search(r"\{[\s\S]*\}", s)
        if not m:
            return None

        try:
            return json.loads(m.group(0))
        except Exception:
            return None

    def _write_log(self, payload: Dict[str, Any]):
        row = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **(payload or {}),
        }
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[news_agent] log_write failed: {e.__class__.__name__}", flush=True)

    def _build_prompt(self, rows: List[Dict[str, Any]]) -> str:
        lines = []
        for i, item in enumerate(rows, start=1):
            title = str(item.get("title") or "").strip()
            content = str(item.get("content") or "").strip().replace("\n", " ")
            text = f"{title} {content}".strip()
            lines.append(f"{i}. {text[:180]}")

        return f"""
市场出现以下财经快讯：
{chr(10).join(lines)}

请分析：
1）涉及哪些股票（股票代码数组，如 ["TSLA", "NVDA"]）
2）整体是利好还是利空（bullish / bearish / neutral）
3）是否形成连续事件（is_event: true/false）
4）给出原因
5）给出建议（买入/观望/卖出）

只输出 JSON：
{{
  "stocks": ["TSLA", "NVDA"],
  "sentiment": "bullish",
  "reason": "...",
  "is_event": true,
  "confidence": 0.8,
  "suggestion": "观望"
}}
"""

    def _analyze_batch(self, rows: List[Dict[str, Any]]):
        if not rows:
            return None

        prompt = self._build_prompt(rows)
        try:
            out = self._llm.invoke(prompt).content
            data = self._extract_json(out)
            if not isinstance(data, dict):
                self._write_log(
                    {
                        "stage": "analyze",
                        "ok": False,
                        "reason": "json_parse_failed",
                        "window_size": len(rows),
                        "llm_output_preview": str(out)[:400],
                    }
                )
                return None

            stocks = [str(x).strip() for x in (data.get("stocks") or []) if str(x).strip()]
            sentiment = str(data.get("sentiment", "neutral")).strip().lower()
            if sentiment not in {"bullish", "bearish", "neutral"}:
                sentiment = "neutral"

            try:
                confidence = float(data.get("confidence", 0) or 0)
            except Exception:
                confidence = 0.0

            event = {
                "type": "news_signal",
                "stocks": stocks,
                "sentiment": sentiment,
                "reason": str(data.get("reason", "")).strip(),
                "is_event": bool(data.get("is_event", False)),
                "confidence": max(0.0, min(1.0, confidence)),
                "suggestion": str(data.get("suggestion", "")).strip(),
                "news_count": len(rows),
                "timestamp": int(time.time()),
            }
            return event
        except Exception as e:
            self._write_log(
                {
                    "stage": "analyze",
                    "ok": False,
                    "reason": f"llm_failed:{e.__class__.__name__}",
                    "window_size": len(rows),
                }
            )
            return None

    def _process_one_batch(self, trigger: str = "bot") -> bool:
        qlen = get_news_queue_len()
        if qlen < self.window_size:
            return False

        rows = get_news_window(self.window_size)
        if len(rows) < self.window_size:
            return False

        event = self._analyze_batch(rows)
        trim_news_processed(self.trim_size)

        if not isinstance(event, dict):
            self._write_log(
                {
                    "stage": "process_one_batch",
                    "ok": False,
                    "reason": "event_build_failed",
                    "trigger": trigger,
                    "queue_len": qlen,
                    "window_size": len(rows),
                    "titles": [str(x.get("title") or "")[:80] for x in rows[:5]],
                }
            )
            return True

        handler_result = None
        try:
            handler_result = self.event_handler(event)
            self._write_log(
                {
                    "stage": "process_one_batch",
                    "ok": True,
                    "trigger": trigger,
                    "queue_len": qlen,
                    "window_size": len(rows),
                    "trim_size": self.trim_size,
                    "event": event,
                    "handler_result": handler_result,
                    "titles": [str(x.get("title") or "")[:80] for x in rows[:5]],
                }
            )
            print(
                f"[news_agent] analyzed trigger={trigger} window={len(rows)} stocks={event.get('stocks', [])} sentiment={event.get('sentiment', 'neutral')}",
                flush=True,
            )
        except Exception as e:
            self._write_log(
                {
                    "stage": "process_one_batch",
                    "ok": False,
                    "reason": f"handle_event_failed:{e.__class__.__name__}",
                    "trigger": trigger,
                    "queue_len": qlen,
                    "window_size": len(rows),
                    "event": event,
                }
            )
            print(f"[news_agent] handle_event failed: {e.__class__.__name__}", flush=True)

        return True

    def process_ready_batches(self, trigger: str = "bot") -> int:
        """由 NewsBot 在入队后触发。满足窗口条件时批量处理，默认最多处理若干批避免阻塞。"""
        if not self._process_lock.acquire(blocking=False):
            self._write_log(
                {
                    "stage": "process_ready_batches",
                    "ok": False,
                    "reason": "busy_skip",
                    "trigger": trigger,
                    "queue_len": get_news_queue_len(),
                }
            )
            return 0

        handled = 0
        try:
            for _ in range(max(1, self.max_batches_per_trigger)):
                processed = self._process_one_batch(trigger=trigger)
                if not processed:
                    break
                handled += 1
            return handled
        finally:
            self._process_lock.release()

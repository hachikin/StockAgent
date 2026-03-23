import json
import threading
import time
from datetime import date, datetime, time as dt_time
from pathlib import Path
from typing import Dict, List, Optional

import config

from agents import brain_agent
from agents.news_agent import NewsAgent
from events.queue import get_news_queue_len, push_news
from infra.redis_store import get_redis


class NewsBot:
    """只负责抓取财联社电报并写入新闻队列，不做 LLM 分析。"""

    def __init__(self, on_queue_ready=None):
        self.poll_seconds = int(getattr(config, "NEWS_BOT_POLL_SECONDS", 30))
        self.cursor_key = str(getattr(config, "NEWS_CURSOR_KEY", "news_last_source_time"))
        self.on_queue_ready = on_queue_ready
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="news-bot", daemon=True)

        root = Path(__file__).resolve().parent.parent
        self.log_dir = root / "log"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / "news_bot.log"

    def _write_log(self, payload: Dict[str, object]):
        row = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **(payload or {}),
        }
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[news_bot] log_write failed: {e.__class__.__name__}", flush=True)

    def start(self):
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self):
        self._stop_event.set()

    @staticmethod
    def _to_text(v) -> str:
        if v is None:
            return ""
        return str(v).strip()

    def _build_source_time(self, row) -> str:
        pub_date = row.get("发布日期") if "发布日期" in row else None
        pub_time = row.get("发布时间") if "发布时间" in row else None

        d_text = ""
        t_text = ""

        if isinstance(pub_date, date):
            d_text = pub_date.isoformat()
        else:
            d_text = self._to_text(pub_date)

        if isinstance(pub_time, dt_time):
            t_text = pub_time.strftime("%H:%M:%S")
        else:
            t_text = self._to_text(pub_time)

        if d_text and t_text:
            return f"{d_text} {t_text}"
        if d_text:
            return d_text
        if t_text:
            return f"{datetime.now().strftime('%Y-%m-%d')} {t_text}"

        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _fetch_cls_news(self) -> List[Dict[str, str]]:
        try:
            import akshare as ak
        except Exception:
            return []

        try:
            df = ak.stock_info_global_cls(symbol="全部")
        except Exception:
            return []

        if df is None or getattr(df, "empty", True):
            return []

        def _pick(row, keys):
            for k in keys:
                if k in row and self._to_text(row.get(k)):
                    return self._to_text(row.get(k))
            return ""

        rows: List[Dict[str, str]] = []
        for _, row in df.iterrows():
            title = _pick(row, ["标题", "title", "Title"])
            content = _pick(row, ["内容", "正文", "content", "Content"])
            if not content:
                continue

            source_time = self._build_source_time(row)
            rows.append(
                {
                    "title": title or "财联社电报",
                    "content": content,
                    "source_time": source_time,
                }
            )

        return rows

    def _get_cursor(self) -> str:
        client = get_redis()
        if not client:
            return ""
        try:
            return str(client.get(self.cursor_key) or "").strip()
        except Exception:
            return ""

    def _set_cursor(self, source_time: str):
        client = get_redis()
        if not client:
            return
        try:
            client.set(self.cursor_key, str(source_time or "").strip())
        except Exception:
            pass

    def run_once(self):
        rows = self._fetch_cls_news()
        fetched = len(rows)
        if not rows:
            qlen = get_news_queue_len()
            self._write_log(
                {
                    "stage": "run_once",
                    "ok": True,
                    "fetched": 0,
                    "pushed": 0,
                    "queue_len": qlen,
                    "cursor": self._get_cursor(),
                    "note": "no_rows_fetched",
                }
            )
            print(f"[news_bot] fetched=0 queue={qlen}", flush=True)
            return

        now = int(time.time())
        cursor = self._get_cursor()

        new_rows = []
        for item in rows:
            st = str(item.get("source_time") or "").strip()
            if cursor and st <= cursor:
                continue
            new_rows.append(item)

        if not new_rows:
            qlen = get_news_queue_len()
            self._write_log(
                {
                    "stage": "run_once",
                    "ok": True,
                    "fetched": fetched,
                    "pushed": 0,
                    "queue_len": qlen,
                    "cursor": cursor,
                    "note": "no_new_rows",
                }
            )
            print(f"[news_bot] fetched={fetched} pushed=0 queue={qlen} cursor={cursor}", flush=True)
            return

        new_rows.sort(key=lambda x: str(x.get("source_time") or ""))

        pushed = 0
        max_source_time = cursor
        for item in new_rows:
            st = str(item.get("source_time") or "").strip()
            ok = push_news(
                {
                    "title": str(item.get("title") or "财联社电报").strip(),
                    "content": str(item.get("content") or "").strip(),
                    "source_time": st,
                    "timestamp": now,
                }
            )
            if ok:
                pushed += 1
                if st > max_source_time:
                    max_source_time = st

        if max_source_time and max_source_time != cursor:
            self._set_cursor(max_source_time)

        qlen = get_news_queue_len()
        self._write_log(
            {
                "stage": "run_once",
                "ok": True,
                "fetched": fetched,
                "pushed": pushed,
                "queue_len": qlen,
                "cursor_before": cursor,
                "cursor_after": max_source_time,
                "note": "source_time_cursor_dedup",
            }
        )
        print(f"[news_bot] fetched={fetched} pushed={pushed} queue={qlen} cursor={max_source_time}", flush=True)

        if callable(self.on_queue_ready) and qlen >= int(getattr(config, "NEWS_WINDOW_SIZE", 20)):
            try:
                handled = int(self.on_queue_ready(trigger="bot", queue_len=qlen) or 0)
            except TypeError:
                handled = int(self.on_queue_ready() or 0)
            except Exception as e:
                handled = 0
                self._write_log(
                    {
                        "stage": "trigger_agent",
                        "ok": False,
                        "queue_len": qlen,
                        "reason": f"callback_failed:{e.__class__.__name__}",
                    }
                )
            else:
                self._write_log(
                    {
                        "stage": "trigger_agent",
                        "ok": True,
                        "queue_len": qlen,
                        "handled_batches": handled,
                    }
                )

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception as e:
                self._write_log(
                    {
                        "stage": "loop",
                        "ok": False,
                        "reason": f"run_once_failed:{e.__class__.__name__}",
                        "queue_len": get_news_queue_len(),
                    }
                )
                print(f"[news_bot] run_once failed: {e.__class__.__name__}", flush=True)
            self._stop_event.wait(self.poll_seconds)


_NEWS_LOCK = threading.Lock()
_NEWS_INSTANCE = None


class NewsSystem:
    """
    新闻系统协调器（独立于 market_bot）：
    - NewsBot：财联社抓取 + 基于发布时间游标去重 + 入队
    - NewsAgent：由 NewsBot 触发批量分析 + 交给 brain_agent 决策
    """

    def __init__(self):
        self.news_agent = NewsAgent(event_handler=brain_agent.handle_event)
        self.news_bot = NewsBot(on_queue_ready=self._trigger_news_agent)

    def _trigger_news_agent(self, trigger: str = "bot", queue_len: Optional[int] = None) -> int:
        _ = queue_len
        return self.news_agent.process_ready_batches(trigger=trigger)

    def start(self):
        self.news_bot.start()
        print("[news_system] news bot started", flush=True)
        print("[news_system] news agent trigger-by-bot enabled", flush=True)

    def stop(self):
        self.news_bot.stop()
        print("[news_system] stop requested", flush=True)


def start_news_watcher():
    global _NEWS_INSTANCE
    enabled = bool(getattr(config, "NEWS_ENABLED", True))
    if not enabled:
        print("[news_system] disabled by config", flush=True)
        return None

    with _NEWS_LOCK:
        if _NEWS_INSTANCE is None:
            _NEWS_INSTANCE = NewsSystem()
            _NEWS_INSTANCE.start()
        return _NEWS_INSTANCE

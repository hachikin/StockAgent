import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

import requests

from infra.redis_store import get_json, set_json
import config

API_KEY = config.NEWS_API_KEY


def _clean_text(text):
    text = str(text or "").strip()
    return " ".join(text.split())


def _strip_html(text):
    return re.sub(r"<[^>]+>", "", str(text or ""))


def _dedupe_keep_order(items, limit=12):
    seen = set()
    result = []
    for item in items:
        t = _clean_text(item)
        if not t:
            continue
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        result.append(t)
        if len(result) >= limit:
            break
    return result


def _build_query_terms(keyword, ts_code=""):
    terms = []
    k = _clean_text(keyword)
    if k:
        terms.append(k)

    if ts_code:
        ts_code = ts_code.upper()
        symbol = ts_code.split(".")[0]
        terms.append(ts_code)
        terms.append(symbol)

    return _dedupe_keep_order(terms, limit=5)


def _is_relevant(headline, keyword, ts_code=""):
    h = _clean_text(headline).lower()
    if not h:
        return False

    kw = _clean_text(keyword).lower()
    if kw and kw in h:
        return True

    if ts_code:
        ts_code = ts_code.upper()
        symbol = ts_code.split(".")[0].lower()
        if symbol and symbol in h:
            return True
        if ts_code.lower() in h:
            return True

    return False


def get_cls_news(keyword):
    try:
        resp = requests.get(
            "https://www.cls.cn/nodeapi/telegraphList",
            params={"app": "CailianpressWeb", "os": "web", "sv": "7.7.5"},
            timeout=6,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        data = resp.json()
        news_list = data.get("data", {}).get("roll_data", [])
        return [n.get("content", "") for n in news_list if keyword in n.get("content", "")]
    except Exception:
        return []


def get_sina_news(keyword):
    try:
        resp = requests.get(
            "https://feed.mix.sina.com.cn/api/roll/get",
            params={"pageid": "153", "lid": "2515", "k": keyword, "num": 10},
            timeout=6,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        data = resp.json()
        return [n.get("title", "") for n in data.get("result", {}).get("data", [])]
    except Exception:
        return []


def get_cninfo_news(keyword):
    """巨潮资讯公告检索（关键词）。"""
    try:
        resp = requests.post(
            "http://www.cninfo.com.cn/new/fulltextSearch/full",
            data={
                "searchkey": keyword,
                "sdate": "",
                "edate": "",
                "isfulltext": "false",
                "sortName": "",
                "sortType": "",
                "pageNum": 1,
                "pageSize": 10,
            },
            timeout=8,
            headers={
                "User-Agent": "Mozilla/5.0",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
            },
        )
        data = resp.json()
        anns = data.get("announcements", []) or []
        results = []
        for a in anns:
            title = _strip_html(a.get("announcementTitle", ""))
            sec_name = _clean_text(a.get("secName", ""))
            if sec_name and sec_name not in title:
                title = f"{sec_name} {title}".strip()
            if title:
                results.append(title)
        return results
    except Exception:
        return []


def get_newsapi_news(query):
    if not API_KEY:
        return []
    try:
        resp = requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "q": query,
                "apiKey": API_KEY,
                "language": "zh",
                "sortBy": "publishedAt",
                "pageSize": 8,
            },
            timeout=8,
        )
        data = resp.json()
        return [a.get("title", "") for a in data.get("articles", []) if a.get("title")]
    except Exception:
        return []


def get_yfinance_news(ts_code):
    if not ts_code:
        return []
    try:
        import yfinance as yf

        ticker = yf.Ticker(ts_code)
        items = ticker.news or []
        return [n.get("title", "") for n in items if n.get("title")]
    except Exception:
        return []


def get_news(keyword, ts_code=""):
    """
    多源新闻聚合，返回结构：
    {
      "news": [...],
      "source_hits": {"cls": n, "sina": n, "cninfo": n, "newsapi": n, "yfinance": n, "filtered": n},
      "query_terms": [...]
    }
    """
    terms = _build_query_terms(keyword, ts_code)
    cache_key = f"news_all_v4:{'|'.join(terms)}"

    cached = get_json(cache_key)
    if cached:
        return cached

    print("请求多源新闻...")

    primary = terms[0] if terms else str(keyword or "")
    query = " OR ".join(terms) if terms else str(keyword or "")

    # Multi-source fetch in parallel to reduce response latency.
    tasks = {
        "cls": (get_cls_news, (primary,)),
        "sina": (get_sina_news, (primary,)),
        "cninfo": (get_cninfo_news, (primary,)),
        "newsapi": (get_newsapi_news, (query,)),
        "yfinance": (get_yfinance_news, (ts_code,)),
    }
    results = {name: [] for name in tasks}

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        future_map = {name: pool.submit(func, *args) for name, (func, args) in tasks.items()}
        for name, future in future_map.items():
            try:
                results[name] = future.result(timeout=15) or []
            except FuturesTimeoutError:
                results[name] = []
            except Exception:
                results[name] = []

    cls_news = results["cls"]
    sina_news = results["sina"]
    cninfo_news = results["cninfo"]
    newsapi_news = results["newsapi"]
    yf_news = results["yfinance"]

    merged = _dedupe_keep_order(cls_news + sina_news + cninfo_news + newsapi_news + yf_news, limit=30)
    final_news = _dedupe_keep_order([n for n in merged if _is_relevant(n, keyword, ts_code)], limit=12)

    result = {
        "news": final_news,
        "source_hits": {
            "cls": len(_dedupe_keep_order(cls_news, limit=100)),
            "sina": len(_dedupe_keep_order(sina_news, limit=100)),
            "cninfo": len(_dedupe_keep_order(cninfo_news, limit=100)),
            "newsapi": len(_dedupe_keep_order(newsapi_news, limit=100)),
            "yfinance": len(_dedupe_keep_order(yf_news, limit=100)),
            "filtered": len(final_news),
        },
        "query_terms": terms,
    }

    set_json(cache_key, result, ex=600)
    return result

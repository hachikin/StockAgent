import math
from typing import Dict, List

import pandas as pd
import ta


def _normalize_ohlcv_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {}
    for col in df.columns:
        c = str(col)
        low = c.lower()
        if low == "close":
            rename_map[col] = "Close"
        elif low == "open":
            rename_map[col] = "Open"
        elif low == "high":
            rename_map[col] = "High"
        elif low == "low":
            rename_map[col] = "Low"
        elif low in ("vol", "volume"):
            rename_map[col] = "Volume"
        elif low in ("trade_date", "date", "datetime"):
            rename_map[col] = "trade_date"

    return df.rename(columns=rename_map)


def _safe_float(v):
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except Exception:
        return None


def _signal_text(latest: Dict) -> Dict:
    close = latest.get("close")
    sma20 = latest.get("sma20")
    sma60 = latest.get("sma60")
    rsi = latest.get("rsi14")
    macd_hist = latest.get("macd_hist")

    trend_short = "中性"
    if close is not None and sma20 is not None:
        trend_short = "偏强" if close > sma20 else "偏弱"

    trend_mid = "中性"
    if sma20 is not None and sma60 is not None:
        trend_mid = "上行" if sma20 > sma60 else "下行"

    momentum = "中性"
    if rsi is not None:
        if rsi >= 70:
            momentum = "超买"
        elif rsi <= 30:
            momentum = "超卖"
        elif rsi >= 55:
            momentum = "偏强"
        elif rsi <= 45:
            momentum = "偏弱"

    macd_state = "中性"
    if macd_hist is not None:
        macd_state = "多头" if macd_hist > 0 else "空头"

    summary = f"短线{trend_short}，中线{trend_mid}，动量{momentum}，MACD{macd_state}"
    return {
        "trend_short": trend_short,
        "trend_mid": trend_mid,
        "momentum": momentum,
        "macd_state": macd_state,
        "summary": summary,
    }


def calc_indicators(data, recent_days: int = 30):
    """
    计算日线技术指标，并返回可直接喂给LLM的技术面结构。
    兼容旧字段：rsi/macd/price。
    """
    if not data:
        return {
            "frequency": "1d",
            "samples": 0,
            "latest": {},
            "recent_technical": [],
            "signals": {},
            "rsi": None,
            "macd": None,
            "price": None,
        }

    df = pd.DataFrame(data)
    if df.empty:
        return {
            "frequency": "1d",
            "samples": 0,
            "latest": {},
            "recent_technical": [],
            "signals": {},
            "rsi": None,
            "macd": None,
            "price": None,
        }

    df = _normalize_ohlcv_columns(df)

    if "Close" not in df.columns:
        return {
            "frequency": "1d",
            "samples": len(df),
            "latest": {},
            "recent_technical": [],
            "signals": {"summary": "缺少Close字段，无法计算技术指标"},
            "rsi": None,
            "macd": None,
            "price": None,
        }

    if "trade_date" in df.columns:
        try:
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df = df.sort_values("trade_date")
        except Exception:
            pass

    close = pd.to_numeric(df["Close"], errors="coerce")
    df["Close"] = close

    # 常用技术指标
    df["rsi14"] = ta.momentum.RSIIndicator(df["Close"], window=14).rsi()
    macd_obj = ta.trend.MACD(df["Close"], window_slow=26, window_fast=12, window_sign=9)
    df["macd"] = macd_obj.macd()
    df["macd_signal"] = macd_obj.macd_signal()
    df["macd_hist"] = macd_obj.macd_diff()

    df["sma5"] = ta.trend.SMAIndicator(df["Close"], window=5).sma_indicator()
    df["sma10"] = ta.trend.SMAIndicator(df["Close"], window=10).sma_indicator()
    df["sma20"] = ta.trend.SMAIndicator(df["Close"], window=20).sma_indicator()
    df["sma60"] = ta.trend.SMAIndicator(df["Close"], window=60).sma_indicator()
    df["ema12"] = ta.trend.EMAIndicator(df["Close"], window=12).ema_indicator()
    df["ema26"] = ta.trend.EMAIndicator(df["Close"], window=26).ema_indicator()

    bb_obj = ta.volatility.BollingerBands(df["Close"], window=20, window_dev=2)
    df["bb_high"] = bb_obj.bollinger_hband()
    df["bb_mid"] = bb_obj.bollinger_mavg()
    df["bb_low"] = bb_obj.bollinger_lband()

    if {"High", "Low"}.issubset(set(df.columns)):
        high = pd.to_numeric(df["High"], errors="coerce")
        low = pd.to_numeric(df["Low"], errors="coerce")
        df["atr14"] = ta.volatility.AverageTrueRange(high, low, df["Close"], window=14).average_true_range()
    else:
        df["atr14"] = None

    if "Volume" in df.columns:
        vol = pd.to_numeric(df["Volume"], errors="coerce")
        df["volume_ma5"] = vol.rolling(5).mean()
    else:
        df["volume_ma5"] = None

    df["pct_change"] = df["Close"].pct_change()

    tail_df = df.tail(max(1, int(recent_days))).copy()
    latest_row = df.iloc[-1]

    latest = {
        "date": str(latest_row.get("trade_date", "")),
        "close": _safe_float(latest_row.get("Close")),
        "rsi14": _safe_float(latest_row.get("rsi14")),
        "macd": _safe_float(latest_row.get("macd")),
        "macd_signal": _safe_float(latest_row.get("macd_signal")),
        "macd_hist": _safe_float(latest_row.get("macd_hist")),
        "sma5": _safe_float(latest_row.get("sma5")),
        "sma10": _safe_float(latest_row.get("sma10")),
        "sma20": _safe_float(latest_row.get("sma20")),
        "sma60": _safe_float(latest_row.get("sma60")),
        "ema12": _safe_float(latest_row.get("ema12")),
        "ema26": _safe_float(latest_row.get("ema26")),
        "bb_high": _safe_float(latest_row.get("bb_high")),
        "bb_mid": _safe_float(latest_row.get("bb_mid")),
        "bb_low": _safe_float(latest_row.get("bb_low")),
        "atr14": _safe_float(latest_row.get("atr14")),
        "volume_ma5": _safe_float(latest_row.get("volume_ma5")),
        "pct_change": _safe_float(latest_row.get("pct_change")),
    }

    recent_cols = [
        "trade_date",
        "Close",
        "pct_change",
        "rsi14",
        "macd",
        "macd_signal",
        "macd_hist",
        "sma5",
        "sma20",
        "sma60",
        "bb_high",
        "bb_low",
        "atr14",
    ]
    recent_cols = [c for c in recent_cols if c in tail_df.columns]

    recent_technical: List[Dict] = []
    for _, row in tail_df[recent_cols].iterrows():
        recent_technical.append(
            {
                "date": str(row.get("trade_date", "")),
                "close": _safe_float(row.get("Close")),
                "pct_change": _safe_float(row.get("pct_change")),
                "rsi14": _safe_float(row.get("rsi14")),
                "macd": _safe_float(row.get("macd")),
                "macd_signal": _safe_float(row.get("macd_signal")),
                "macd_hist": _safe_float(row.get("macd_hist")),
                "sma5": _safe_float(row.get("sma5")),
                "sma20": _safe_float(row.get("sma20")),
                "sma60": _safe_float(row.get("sma60")),
                "bb_high": _safe_float(row.get("bb_high")),
                "bb_low": _safe_float(row.get("bb_low")),
                "atr14": _safe_float(row.get("atr14")),
            }
        )

    signals = _signal_text(latest)

    # 兼容旧字段: rsi/macd/price
    return {
        "frequency": "1d",
        "samples": len(df),
        "window_days": int(recent_days),
        "latest": latest,
        "recent_technical": recent_technical,
        "signals": signals,
        "rsi": latest.get("rsi14"),
        "macd": latest.get("macd"),
        "price": latest.get("close"),
    }

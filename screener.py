import time

import requests
import pandas as pd
import numpy as np
import ta
from config import OKX_API_URL, MIN_MARKET_CAP, COINGECKO_API_URL

# ---------------- TF LIST KuCoin ----------------
# granularity dalam menit
INTERVALS = {
    "5min": 5,
    "15min": 15,
    "30min": 30,
    "1hour": 60
}

# bobot untuk scoring
TF_WEIGHT = {
    "5min": 1,
    "15min": 2,
    "30min": 3,
    "1hour": 4
}

# ---------------- HELPER ----------------
def fetch_symbols():
    """Ambil semua simbol USDT Futures di KuCoin"""
    url = "https://api-futures.kucoin.com/api/v1/contracts/active"
    try:
        r = requests.get(url, timeout=5)
        resp = r.json()
        data = resp.get("data", [])
        if not data or not isinstance(data, list):
            return []
        return [d["symbol"] for d in data if d.get("settleCurrency") == "USDT"]
    except Exception as e:
        print(f"Error fetch_symbols: {e}")
        return []


def fetch_klines(symbol, interval="1hour", limit=100):
    """
    Ambil OHLC dari KuCoin Futures
    symbol: XBTUSDTM, ETHUSDTM, dst
    interval: key dari TF_LIST, misal '1hour'
    limit: jumlah candle terakhir
    """
    url = "https://api-futures.kucoin.com/api/v1/kline/query"
    granularity = INTERVALS.get(interval, 60)  # default 60 menit

    params = {
        "symbol": symbol,
        "granularity": granularity,
    }

    try:
        r = requests.get(url, params=params, timeout=5)
        data = r.json().get("data", [])
        if not data:
            return None

        # Response: [timestamp, open, close, high, low, volume, turnover]
        df = pd.DataFrame(data, columns=["timestamp","open","close","high","low","volume","turnover"])
        df = df[["timestamp","open","high","low","close","volume"]].apply(pd.to_numeric, errors='coerce')
        df = df.dropna().reset_index(drop=True)

        if len(df) < 5:
            return None

        # Urutkan dari paling lama → paling baru
        df = df.iloc[::-1].reset_index(drop=True)
        return df

    except Exception as e:
        print(f"Error fetch_klines {symbol}: {e}")
        return None

def fetch_last_price(symbol):
    """Ambil mark price terakhir dari KuCoin Futures"""
    url = f"https://api-futures.kucoin.com/api/v1/mark-price/{symbol}/current"
    try:
        r = requests.get(url, timeout=5)
        resp = r.json()
        data = resp.get("data", {})
        if not data:
            return None
        return float(data.get("value", 0))  # ambil mark price
    except Exception as e:
        print(f"Error fetch_last_price {symbol}: {e}")
        return None


# ---------------- ANALISA ----------------
def score_tf(df):
    df["ema5"] = ta.trend.EMAIndicator(df["close"], 5).ema_indicator()
    df["ema20"] = ta.trend.EMAIndicator(df["close"], 20).ema_indicator()
    df["ema50"] = ta.trend.EMAIndicator(df["close"], 50).ema_indicator()
    df["ema200"] = ta.trend.EMAIndicator(df["close"], 200).ema_indicator()
    df["rsi"] = ta.momentum.RSIIndicator(df["close"], 14).rsi()
    df["stoch_k"] = ta.momentum.StochasticOscillator(df["high"], df["low"], df["close"], 5, 3).stoch()
    df["stoch_d"] = df["stoch_k"].rolling(3).mean()

    last = df.iloc[-1]
    avg_body = np.mean(abs(df["close"] - df["open"]))
    body = abs(last["close"] - last["open"])

    score_long, score_short = 0, 0
    # LONG
    if last["ema5"] > last["ema20"]: score_long += 1
    if last["rsi"] > 45: score_long += 1
    if last["stoch_k"] > last["stoch_d"]: score_long += 1
    if last["close"] > last["open"] and body > 1.2 * avg_body: score_long += 1
    # SHORT
    if last["ema50"] < last["ema200"]: score_short += 1
    if last["rsi"] < 55: score_short += 1
    if last["stoch_k"] < last["stoch_d"]: score_short += 1
    if last["close"] < last["open"] and body > 1.2 * avg_body: score_short += 1

    return score_long, score_short

def analyze_symbol(symbol):
    total_long, total_short = 0, 0
    tf_detail = {}

    for tf, weight in TF_WEIGHT.items():
        df = fetch_klines(symbol, interval=tf)
        if df is None or len(df) < 20:
            continue
        s_long, s_short = score_tf(df)
        tf_detail[tf] = {"LONG": s_long, "SHORT": s_short}
        total_long += s_long * weight
        total_short += s_short * weight

    min_score = 1
    if total_long >= min_score:
        return "LONG", symbol, total_long, tf_detail
    elif total_short >= min_score:
        return "SHORT", symbol, total_short, tf_detail
    else:
        return None

# ---------------- SWING ----------------
def swing_trade_levels(df, lookback=90, atr_len=14):
    df = df[-min(lookback, len(df)):].copy()
    df = df.dropna(subset=['high','low','close'])
    if df.empty: return None
    lowest_low = df['low'].min()
    highest_high = df['high'].max()
    price_range = highest_high - lowest_low
    atr_window = min(atr_len, len(df))
    atr_series = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=atr_window).average_true_range()
    atr = atr_series.iloc[-1]
    cl = lowest_low - 0.25*atr
    entry_bottom = lowest_low
    entry_top = lowest_low + atr*1.0
    tp1 = lowest_low + price_range*0.382
    tp2 = lowest_low + price_range*0.618
    tp3 = highest_high
    return {"CL": cl, "ENTRY_BOTTOM": entry_bottom, "ENTRY_TOP": entry_top, "TP1": tp1, "TP2": tp2, "TP3": tp3}

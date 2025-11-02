import requests
import pandas as pd
import numpy as np
import ta
from config import OKX_API_URL

# Multi-TF dengan bobot
TF_LIST = {
    "5m": 1,    # entry cepat
    "15m": 2,   # konfirmasi minor trend
    "30m": 3,   # trend utama
    "1h": 4     # trend besar / filter utama
}

def fetch_klines(symbol, interval="1H", limit=100):
    url = f"{OKX_API_URL}/api/v5/market/candles"
    params = {"instId": symbol, "bar": interval, "limit": limit}
    r = requests.get(url, params=params)
    data = r.json().get("data", [])
    if not data:
        return None

    df = pd.DataFrame(data, columns=[
        "timestamp", "open", "high", "low", "close", "volume", "confirm", "wap", "count"
    ])
    df = df[["timestamp", "open", "high", "low", "close", "volume"]].astype(float)
    return df[::-1].reset_index(drop=True)

def score_tf(df):
    """Hitung skor LONG / SHORT untuk satu TF"""
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

    score_long = 0
    score_short = 0

    # LONG conditions
    if last["ema5"] > last["ema20"]:
        score_long += 1
    if last["rsi"] > 45:
        score_long += 1
    if last["stoch_k"] > last["stoch_d"]:
        score_long += 1
    if last["close"] > last["open"] and body > 1.2 * avg_body:
        score_long += 1

    # SHORT conditions
    if last["ema50"] < last["ema200"]:
        score_short += 1
    if last["rsi"] < 55:
        score_short += 1
    if last["stoch_k"] < last["stoch_d"]:
        score_short += 1
    if last["close"] < last["open"] and body > 1.2 * avg_body:
        score_short += 1

    return score_long, score_short

def analyze_symbol(symbol):
    total_long_score = 0
    total_short_score = 0
    tf_breakdown = {}

    for tf, weight in TF_LIST.items():
        df = fetch_klines(symbol, interval=tf)
        if df is None or len(df) < 50:
            continue
        s_long, s_short = score_tf(df)
        tf_breakdown[tf] = {"LONG": s_long, "SHORT": s_short}
        total_long_score += s_long * weight
        total_short_score += s_short * weight

    # Threshold multi-TF, total max = sum(weights)*4 = 40
    min_score = 1  # bisa disesuaikan, total max = sum(weight)*4 = 40
    if total_long_score >= min_score:
        return "LONG", symbol, total_long_score, tf_breakdown
    elif total_short_score >= min_score:
        return "SHORT", symbol, total_short_score, tf_breakdown
    else:
        return None

def fetch_last_price(symbol):
    """
    Ambil harga terakhir (last price) untuk symbol di OKX
    """
    url = f"{OKX_API_URL}/api/v5/market/ticker"
    params = {"instId": symbol}
    try:
        r = requests.get(url, params=params, timeout=5)
        data = r.json().get("data", [])
        if data:
            return float(data[0]["last"])
    except Exception as e:
        print(f"Error fetch_last_price {symbol}: {e}")
    return None

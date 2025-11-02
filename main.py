import json

import requests
import time
import schedule
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, OKX_API_URL, MAX_WORKERS
from screener import analyze_symbol, fetch_last_price
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask

POSITIONS_FILE = "positions.json"

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running"

def fetch_symbols():
    """
    Ambil semua simbol futures USDT dari OKX
    """
    url = f"{OKX_API_URL}/api/v5/public/instruments"
    params = {"instType": "SWAP"}  # hanya perpetual futures
    r = requests.get(url, params=params)
    data = r.json().get("data", [])
    return [d["instId"] for d in data if d["instId"].endswith("USDT-SWAP")]

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    res = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    })
    print(res.json())

def load_positions():
    try:
        with open(POSITIONS_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_positions(positions):
    with open(POSITIONS_FILE, "w") as f:
        json.dump(positions, f, indent=2)

# ==================== JOB 30 MENIT ====================
def job_signal():
    symbols = fetch_symbols()
    long_candidates = []
    short_candidates = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(analyze_symbol, s): s for s in symbols}
        for future in as_completed(futures):
            symbol_name = futures[future]
            try:
                result = future.result()
                if result:
                    signal, sym, score, tf_detail = result
                    entry_lines = [f"{sym} (score={score})"]
                    for tf, val in tf_detail.items():
                        entry_lines.append(f"   {tf:<4}: LONG={val['LONG']}, SHORT={val['SHORT']}")
                    entry = "\n".join(entry_lines)

                    # Simpan juga data asli untuk PnL
                    candidate_data = {
                        "symbol": sym,
                        "signal": signal,
                        "score": score,
                        "entry_text": entry
                    }

                    if signal == "LONG":
                        long_candidates.append(candidate_data)
                    elif signal == "SHORT":
                        short_candidates.append(candidate_data)
            except Exception as e:
                print(f"Error processing {symbol_name}: {e}")

    # Sort top 5 berdasarkan score
    long_candidates = sorted(long_candidates, key=lambda x: x["score"], reverse=True)[:5]
    short_candidates = sorted(short_candidates, key=lambda x: x["score"], reverse=True)[:5]

    # Simpan posisi baru untuk tracking PnL
    new_positions = []
    for c in long_candidates + short_candidates:
        current_price = fetch_last_price(c["symbol"])
        new_positions.append({
            "symbol": c["symbol"],
            "signal": c["signal"],
            "entry_price": current_price,
            "pnl": 0
        })

    save_positions(new_positions)

    # Kirim pesan ke Telegram
    if long_candidates:
        long_msg = "🚀 LONG Candidates:\n\n" + "\n\n".join([c["entry_text"] for c in long_candidates])
        send_telegram_message(long_msg)
    else:
        send_telegram_message("✅ Tidak ada peluang LONG saat ini.")

    if short_candidates:
        short_msg = "📉 SHORT Candidates:\n\n" + "\n\n".join([c["entry_text"] for c in short_candidates])
        send_telegram_message(short_msg)
    else:
        send_telegram_message("✅ Tidak ada peluang SHORT saat ini.")

# ==================== JOB 5 MENIT ====================
def job_pnl():
    positions = load_positions()
    if not positions:
        return

    long_msgs = []
    short_msgs = []

    for p in positions:
        current_price = fetch_last_price(p["symbol"])
        signal = p["signal"]

        if signal == "LONG":
            pnl = (current_price - p["entry_price"]) / p["entry_price"] * 100
        else:  # SHORT
            pnl = (p["entry_price"] - current_price) / p["entry_price"] * 100

        # Emoji
        if pnl > 0:
            emoji = "🟢"
        elif pnl < 0:
            emoji = "🔴"
        else:
            emoji = "⚪"

        # Tambahkan entry price dan marked price
        line = (
            f"{emoji} {p['symbol']:<16} | "
            f"Entry: {p['entry_price']:.4f} | "
            f"Mark: {current_price:.4f} | "
            f"PnL: {pnl:>6.2f}%"
        )

        if signal == "LONG":
            long_msgs.append(line)
        else:
            short_msgs.append(line)

    msg = ""
    if long_msgs:
        msg += "🚀 LONG:\n" + "\n".join(long_msgs) + "\n"
    else:
        msg += "🚀 LONG: Tidak ada posisi terbuka.\n"

    if short_msgs:
        msg += "📉 SHORT:\n" + "\n".join(short_msgs)
    else:
        msg += "📉 SHORT: Tidak ada posisi terbuka."

    send_telegram_message(msg)


@app.route("/signal")
def run_signal():
    job_signal()
    return "job_signal executed"


@app.route("/pnl")
def run_pnl():
    job_pnl()
    return "job_pnl executed"


if __name__ == "__main__":
    from threading import Thread

    # Jalankan Flask server di background
    Thread(target=lambda: app.run(host="0.0.0.0", port=3000)).start()

    # Schedule job lokal (backup kalau ping dari luar mati)
    import schedule

    schedule.every(30).minutes.do(job_signal)
    schedule.every(5).minutes.do(job_pnl)
    while True:
        schedule.run_pending()
        time.sleep(10)
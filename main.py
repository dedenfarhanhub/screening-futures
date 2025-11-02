import json

import requests
import time
import schedule
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, OKX_API_URL, MAX_WORKERS
from screener import analyze_symbol, fetch_last_price, swing_trade_levels, fetch_klines
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask

POSITIONS_FILE = "positions.json"
POSITIONS_FILE_SWING = "position_swings.json"

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


def load_position_swings():
    try:
        with open(POSITIONS_FILE_SWING, "r") as f:
            return json.load(f)
    except:
        return []

def save_position_swings(positions):
    with open(POSITIONS_FILE_SWING, "w") as f:
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


# ==================== JOB SWING SIGNAL ====================
def job_swing_signal():
    symbols = fetch_symbols()
    swing_candidates = []
    new_positions = []  # untuk save ke file swing

    def process_symbol(sym):
        for attempt in range(3):  # retry 3x
            try:
                df = fetch_klines(sym, interval="1D")
                if df is None or df.empty or len(df) < 20:
                    time.sleep(0.5)  # delay sebentar sebelum retry
                    continue

                levels = swing_trade_levels(df)
                last_price = df['close'].iloc[-1]
                signal = "LONG" if last_price <= levels["ENTRY_TOP"] else "WAIT"
                if signal != "LONG":
                    return None

                return {
                    "symbol": sym,
                    "signal": signal,
                    "last_price": last_price,
                    "levels": levels,
                    "entry_zone": (levels["ENTRY_BOTTOM"], levels["ENTRY_TOP"])
                }
            except Exception as e:
                print(f"Attempt {attempt+1} error for {sym}: {e}")
                time.sleep(0.5)
        return None

    batch_size = 10
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i+batch_size]
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(process_symbol, s): s for s in batch}
            for future in as_completed(futures):
                res = future.result()
                if res:
                    entry_lines = [
                        f"{res['symbol']}",
                        f"   Entry: {res['entry_zone'][0]:.4f} - {res['entry_zone'][1]:.4f}",
                    ]
                    swing_candidates.append({
                        "symbol": res["symbol"],
                        "signal": res["signal"],
                        "entry_text": "\n".join(entry_lines)
                    })
                    # Save posisi swing
                    new_positions.append({
                        "symbol": res["symbol"],
                        "signal": res["signal"],
                        "entry_price": res["last_price"],
                        "pnl": 0
                    })

    # Simpan ke file swing
    save_position_swings(new_positions)

    if swing_candidates:
        send_telegram_message("📊 SWING Candidates:\n\n" + "\n\n".join([c["entry_text"] for c in swing_candidates]))
    else:
        send_telegram_message("✅ Tidak ada peluang SWING saat ini.")



# ==================== JOB PNL SWING 10 MENIT ====================
def job_swing_pnl():
    positions = load_position_swings()
    if not positions:
        return

    msgs = []
    for p in positions:
        current_price = fetch_last_price(p["symbol"])
        pnl = (current_price - p["entry_price"]) / p["entry_price"] * 100
        emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
        line = f"{emoji} {p['symbol']:<16} | Entry: {p['entry_price']:.4f} | Mark: {current_price:.4f} | PnL: {pnl:>6.2f}%"
        msgs.append(line)
        p["pnl"] = pnl

    save_positions(positions)
    msg_text = "📊 SWING PnL Update:\n" + "\n".join(msgs)
    send_telegram_message(msg_text)

@app.route("/signal")
def run_signal():
    job_signal()
    return "job_signal executed"
@app.route("/pnl")
def run_pnl():
    job_pnl()
    return "job_pnl executed"

@app.route("/signal-swing")
def run_signal_swing():
    job_swing_signal()
    return "job_swing_signal executed"
@app.route("/pnl-swing")
def run_pnl_swing():
    job_swing_pnl()
    return "job_swing_pnl executed"


if __name__ == "__main__":
    from threading import Thread

    # Jalankan Flask server di background
    Thread(target=lambda: app.run(host="0.0.0.0", port=3000)).start()

    # Schedule job lokal (backup kalau ping dari luar mati)
    import schedule

    schedule.every(30).minutes.do(job_signal)
    schedule.every(60).minutes.do(job_swing_signal)
    schedule.every(5).minutes.do(job_pnl)
    schedule.every(10).minutes.do(job_swing_pnl)
    while True:
        schedule.run_pending()
        time.sleep(10)
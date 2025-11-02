import json

import requests
import time
import schedule
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, OKX_API_URL, MAX_WORKERS
from screener import analyze_symbol, fetch_last_price
from concurrent.futures import ThreadPoolExecutor, as_completed

POSITIONS_FILE = "positions.json"

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

def load_positions():
    try:
        with open(POSITIONS_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_positions(positions):
    with open(POSITIONS_FILE, "w") as f:
        json.dump(positions, f, indent=2)

def job():
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

                    if signal == "LONG":
                        long_candidates.append((score, entry))
                    elif signal == "SHORT":
                        short_candidates.append((score, entry))
            except Exception as e:
                print(f"Error processing {symbol_name}: {e}")

    # Sort top 5
    long_candidates = [e[1] for e in sorted(long_candidates, key=lambda x: x[0], reverse=True)[:5]]
    short_candidates = [e[1] for e in sorted(short_candidates, key=lambda x: x[0], reverse=True)[:5]]

    new_positions = []
    for c in long_candidates + short_candidates:
        symbol = c["symbol"]
        signal = c["signal"]
        current_price = fetch_last_price(symbol)
        new_positions.append({
            "symbol": symbol,
            "signal": signal,
            "entry_price": current_price,
            "pnl": 0
        })

    save_positions(new_positions)

    if long_candidates:
        long_msg = "🚀 LONG Candidates:\n" + "\n\n".join(long_candidates)
        send_telegram_message(long_msg)
    else:
        send_telegram_message("✅ Tidak ada peluang LONG saat ini.")

    if short_candidates:
        short_msg = "📉 SHORT Candidates:\n" + "\n\n".join(short_candidates)
        send_telegram_message(short_msg)
    else:
        send_telegram_message("✅ Tidak ada peluang SHORT saat ini.")

def format_pnl_message(positions):
    long_pos = [p for p in positions if p["signal"] == "LONG"]
    short_pos = [p for p in positions if p["signal"] == "SHORT"]

    msg_lines = ["📊 Posisi Terbuka PnL:\n"]

    if long_pos:
        msg_lines.append("🚀 LONG:")
        for p in long_pos:
            msg_lines.append(f"{p['symbol']:<16}: {p['pnl']:>6.2f}%")
        msg_lines.append("")  # spasi

    if short_pos:
        msg_lines.append("📉 SHORT:")
        for p in short_pos:
            msg_lines.append(f"{p['symbol']:<16}: {p['pnl']:>6.2f}%")
        msg_lines.append("")

    return "\n".join(msg_lines)

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

        line = f"{emoji} {p['symbol']:<16}: {pnl:>6.2f}%"

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

if __name__ == '__main__':
    schedule.every(30).minutes.do(job_signal())
    schedule.every(5).minutes.do(job_pnl())
    while True:
        schedule.run_pending()
        time.sleep(10)
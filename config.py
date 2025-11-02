from dotenv import load_dotenv
import os

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", 0))
OKX_API_URL = os.getenv("OKX_API_URL", "https://www.okx.com")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", 10))
import os
from dataclasses import dataclass

@dataclass
class Config:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "8529165360:AAF-L_DTZ3f6S-hppJNzaW9X4pqMtWUJe_c")
    AITUNNEL_KEY: str = os.getenv("AITUNNEL_KEY", "sk-aitunnel-qeuouwWXHwYtJmmartJ6xlCEvSbAEBaG")
    AITUNNEL_URL: str = os.getenv("AITUNNEL_URL", "https://api.aitunnel.ru/v1/chat/completions")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    
    # Задержка тишины буфера (секунды) для сбора пачки фотографий
    DEBOUNCE_DELAY: float = float(os.getenv("DEBOUNCE_DELAY", "2.8"))
    
    # Google Sheets
    GOOGLE_SHEET_ID: str = os.getenv("GOOGLE_SHEET_ID", "")
    GOOGLE_SHEET_NAME: str = os.getenv("GOOGLE_SHEET_NAME", "Винил")
    GOOGLE_SERVICE_ACCOUNT_JSON: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    GOOGLE_SERVICE_ACCOUNT_FILE: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")

config = Config()

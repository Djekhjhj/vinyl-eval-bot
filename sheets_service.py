import datetime
import json
import logging
import os
from typing import Optional, Tuple
import gspread
from google.oauth2.service_account import Credentials

from config import config
from models import VinylRelease

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

COLUMNS = [
    "Дата",
    "Исполнитель",
    "Альбом",
    "Год",
    "Лейбл",
    "Кат. №",
    "Матрица со сбега",
    "Конверт",
    "Винил",
    "Цена (₽)",
    "Коридор (₽)",
    "Ликвидность",
    "Экспертный вердикт",
    "Ссылка на Discogs"
]

class SheetsService:
    def __init__(self):
        self._client: Optional[gspread.Client] = None
        self._spreadsheet = None

    def get_client(self) -> Optional[gspread.Client]:
        if self._client:
            return self._client

        # 1. Проверяем переменную окружения с JSON сервисного аккаунта
        if config.GOOGLE_SERVICE_ACCOUNT_JSON:
            try:
                creds_dict = json.loads(config.GOOGLE_SERVICE_ACCOUNT_JSON)
                creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
                self._client = gspread.authorize(creds)
                logger.info("Успешная авторизация Google Sheets через GOOGLE_SERVICE_ACCOUNT_JSON.")
                return self._client
            except Exception as e:
                logger.error(f"Не удалось авторизоваться через GOOGLE_SERVICE_ACCOUNT_JSON: {e}")

        # 2. Проверяем файл сервисного аккаунта
        if os.path.exists(config.GOOGLE_SERVICE_ACCOUNT_FILE):
            try:
                creds = Credentials.from_service_account_file(config.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES)
                self._client = gspread.authorize(creds)
                logger.info("Успешная авторизация Google Sheets через файл сервисного аккаунта.")
                return self._client
            except Exception as e:
                logger.error(f"Не удалось авторизоваться через файл сервисного аккаунта: {e}")

        # 3. Локальный oauth только если явно запрошен через env
        if os.getenv("GOOGLE_USE_OAUTH", "").lower() in ("true", "1"):
            try:
                self._client = gspread.oauth()
                return self._client
            except Exception:
                pass

        return None

    def append_release(self, release: VinylRelease) -> Tuple[bool, str]:
        """
        Записывает карточку винила в Google Таблицу.
        Возвращает (success: bool, status_message: str).
        """
        row_data = [
            datetime.datetime.now().strftime("%d.%m.%Y %H:%M"),
            release.artist,
            release.album,
            release.year,
            release.label,
            release.catno,
            release.matrix,
            release.condition_sleeve,
            release.condition_vinyl,
            release.price_rub_median,
            f"{release.price_rub_min} - {release.price_rub_max} ₽",
            f"{release.liquidity_stars} {release.liquidity_text}",
            release.verdict,
            release.discogs_url or ""
        ]

        client = self.get_client()
        if not client:
            self._save_local_fallback(row_data)
            return False, "Google Таблица не настроена (строка сохранена в локальный лог)."

        try:
            sh = None
            if config.GOOGLE_SHEET_ID:
                sh = client.open_by_key(config.GOOGLE_SHEET_ID)
            else:
                try:
                    sh = client.open(config.GOOGLE_SHEET_NAME)
                except gspread.SpreadsheetNotFound:
                    # Создаем новую таблицу, если не найдена
                    sh = client.create(config.GOOGLE_SHEET_NAME)
                    logger.info(f"Создана новая Google Таблица '{config.GOOGLE_SHEET_NAME}'")

            worksheet = sh.sheet1
            # Проверяем заголовки
            first_row = worksheet.row_values(1)
            if not first_row:
                worksheet.append_row(COLUMNS)

            worksheet.append_row(row_data)
            return True, f"Записано в таблицу: {sh.title}"
        except Exception as e:
            logger.error(f"Ошибка записи в Google Sheets: {e}", exc_info=True)
            self._save_local_fallback(row_data)
            return False, f"Ошибка сохранения в Google: {str(e)[:50]}"

    def _save_local_fallback(self, row_data: list):
        """Резервное сохранение на диск при отсутствии сети/доступа к Google."""
        try:
            with open("saved_records.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(row_data, ensure_ascii=False) + "\n")
            logger.info("Строка успешно зафиксирована в локальном резервном файле saved_records.jsonl")
        except Exception as e:
            logger.error(f"Не удалось сохранить в локальный лог: {e}")

sheets_service = SheetsService()

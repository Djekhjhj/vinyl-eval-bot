import html
import uuid
import time
from typing import Dict, Tuple
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from models import VinylRelease

class ReleaseStore:
    """Хранилище карточек в памяти с TTL для безопасных callback_data в Telegram (лимит 64 байта)."""
    def __init__(self, ttl_seconds: int = 86400):
        self._store: Dict[str, Tuple[float, VinylRelease]] = {}
        self.ttl = ttl_seconds

    def save(self, release: VinylRelease) -> str:
        self._cleanup()
        rel_id = uuid.uuid4().hex[:12]
        self._store[rel_id] = (time.time(), release)
        return rel_id

    def get(self, rel_id: str) -> VinylRelease | None:
        item = self._store.get(rel_id)
        if item:
            return item[1]
        return None

    def _cleanup(self):
        now = time.time()
        expired = [k for k, (t, _) in self._store.items() if now - t > self.ttl]
        for k in expired:
            del self._store[k]

release_store = ReleaseStore()

def format_price(val: int) -> str:
    """Форматирует число с разделителем тысяч, например 7 500."""
    return f"{val:,}".replace(",", " ")

def format_card_html(release: VinylRelease) -> str:
    """Формирует презентабельное HTML-сообщение карточки для Telegram."""
    artist = html.escape(release.artist, quote=False)
    album = html.escape(release.album, quote=False)
    label = html.escape(release.label, quote=False)
    year = html.escape(str(release.year), quote=False)
    catno = html.escape(release.catno, quote=False)
    matrix = html.escape(release.matrix, quote=False)
    sleeve = html.escape(release.condition_sleeve, quote=False)
    vinyl = html.escape(release.condition_vinyl, quote=False)
    liquidity_text = html.escape(release.liquidity_text, quote=False)
    verdict = html.escape(release.verdict, quote=False)

    median_str = format_price(release.price_rub_median)
    min_str = format_price(release.price_rub_min)
    max_str = format_price(release.price_rub_max)

    discogs_line = ""
    if release.discogs_url:
        safe_url = html.escape(release.discogs_url, quote=True)
        discogs_info = f"#{release.discogs_id}" if release.discogs_id else "Страница поиска"
        price_eur = f" (мин. €{release.discogs_lowest_eur})" if release.discogs_lowest_eur else ""
        discogs_line = f"\n🌐 <b>Discogs:</b> <a href=\"{safe_url}\">{discogs_info}</a>{price_eur}\n"

    text = (
        f"💿 <b>{artist} — {album}</b>\n\n"
        f"🏢 <b>Лейбл:</b> {label} | <b>Год:</b> {year}\n"
        f"🏷️ <b>Каталожный №:</b> <code>{catno}</code>\n"
        f"🔍 <b>Матрица со сбега:</b> <code>{matrix}</code>\n"
        f"📦 <b>Состояние:</b> Конверт: <b>{sleeve}</b> | Винил: <b>{vinyl}</b>\n"
        f"{discogs_line}\n"
        f"💰 <b>Цена в РФ:</b> <b>{median_str} ₽</b> (коридор: {min_str} – {max_str} ₽)\n"
        f"📈 <b>Ликвидность:</b> {release.liquidity_stars} <i>{liquidity_text}</i>\n\n"
        f"⚖️ <b>Вердикт:</b>\n{verdict}"
    )
    return text

def create_card_keyboard(rel_id: str, short_title: str, discogs_url: str | None = None) -> InlineKeyboardMarkup:
    # Ограничиваем длину названия на кнопке, чтобы не обрезалось Telegram UI
    btn_text = f"➕ В таблицу: {short_title}"
    if len(btn_text) > 40:
        btn_text = btn_text[:37] + "..."

    rows = []
    if discogs_url:
        rows.append([
            InlineKeyboardButton(
                text="🔗 Релиз на Discogs",
                url=discogs_url
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text=btn_text,
            callback_data=f"save:{rel_id}"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)

def create_saved_keyboard(short_title: str, discogs_url: str | None = None) -> InlineKeyboardMarkup:
    """Клавиатура после успешного сохранения."""
    btn_text = f"✅ Добавлено: {short_title}"
    if len(btn_text) > 40:
        btn_text = btn_text[:37] + "..."

    rows = []
    if discogs_url:
        rows.append([
            InlineKeyboardButton(
                text="🔗 Релиз на Discogs",
                url=discogs_url
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text=btn_text,
            callback_data="already_saved"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)

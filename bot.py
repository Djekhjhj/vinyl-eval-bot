import asyncio
import io
import logging
import sys
from typing import List, NamedTuple, Optional

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.storage.memory import MemoryStorage

from config import config
from models import VinylRelease, AnalysisResponse
from batch_buffer import PhotoBatchBuffer
from vision_service import analyze_photos_batch
from card_formatter import format_card_html, create_card_keyboard, create_saved_keyboard, release_store
from sheets_service import sheets_service

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("VinylBot")

router = Router()

class PhotoItem(NamedTuple):
    file_id: str
    caption: Optional[str]
    message_id: int

# Глобальный буфер пачек фото
photo_buffer = PhotoBatchBuffer(delay=config.DEBOUNCE_DELAY)

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    welcome_text = (
        "👋 <b>Добро пожаловать в ассистент оценки винила!</b>\n\n"
        "Я работаю 24/7 в облаке и помогаю Елене и Андрею мгновенно оценивать виниловые пластинки.\n\n"
        "📸 <b>Как со мной работать:</b>\n"
        "1. Сфотографируйте пластинки и отправьте мне пачку из <b>1–10 фото</b> (альбомом или подряд).\n"
        "2. Вы можете отправить обе стороны одной пластинки и матрицу со сбега — нейросеть объединит их в одну карточку.\n"
        "3. Если вы отправите фото разных пластинок — я разделю их на отдельные карточки.\n"
        "4. Под каждой карточкой будет кнопка <code>➕ В таблицу</code> для мгновенной фиксации.\n\n"
        "<i>Жду ваши фотографии!</i>"
    )
    await message.answer(welcome_text, parse_mode="HTML")

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "ℹ️ <b>Памятка по оценке:</b>\n\n"
        "• Для точной идентификации старайтесь, чтобы в кадр попадало <b>яблоко (лейбл)</b> и <b>номер матрицы на сбеге (runout groove)</b>.\n"
        "• Цены рассчитываются на основе реального вторичного рынка РФ (Мешок, Авито, виниловые ярмарки).\n"
        "• Все фото из одной пачки обрабатываются нейросетью Gemini 3.8 Flash за один проход."
    )
    await message.answer(help_text, parse_mode="HTML")

@router.message(F.photo)
async def handle_incoming_photo(message: types.Message):
    """
    Принимает фото, извлекает наилучшее разрешение и помещает в буфер тишины.
    """
    largest_photo = message.photo[-1]
    item = PhotoItem(
        file_id=largest_photo.file_id,
        caption=message.caption,
        message_id=message.message_id
    )
    await photo_buffer.add_item(message.chat.id, item)
    logger.info(f"Фото {item.file_id[:12]} добавлено в буфер чата {message.chat.id}")

@router.callback_query(F.data.startswith("save:"))
async def handle_save_to_sheet(query: types.CallbackQuery):
    """
    Обрабатывает клик по кнопке '➕ В таблицу'.
    """
    rel_id = query.data.split(":", 1)[1]
    release = release_store.get(rel_id)

    if not release:
        await query.answer("Срок действия карточки истек. Отправьте фото заново.", show_alert=True)
        return

    success, msg = sheets_service.append_release(release)

    if success:
        await query.answer("✅ Добавлено в Google Таблицу!", show_alert=False)
        try:
            await query.message.edit_reply_markup(
                reply_markup=create_saved_keyboard(release.short_title)
            )
        except Exception:
            pass
    else:
        await query.answer(f"⚠️ {msg}", show_alert=True)

@router.callback_query(F.data == "already_saved")
async def handle_already_saved(query: types.CallbackQuery):
    await query.answer("Эта пластинка уже внесена в таблицу.", show_alert=False)

async def process_photos_batch(chat_id: int, items: List[PhotoItem], bot: Bot):
    """
    Вызывается буфером по истечении окна тишины. Скачивает фото и отправляет в Gemini.
    """
    total_photos = len(items)
    logger.info(f"Начало обработки пачки из {total_photos} фото для чата {chat_id}")

    status_msg = await bot.send_message(
        chat_id,
        f"⏳ Собрана пачка из <b>{total_photos} фото</b>. Анализирую через Gemini 3.8 Flash...",
        parse_mode="HTML"
    )

    try:
        # Скачиваем все фото из Telegram
        images_bytes: List[bytes] = []
        user_captions: List[str] = []

        for item in items:
            if item.caption:
                user_captions.append(item.caption)
            
            file_info = await bot.get_file(item.file_id)
            if not file_info.file_path:
                continue

            bio = io.BytesIO()
            await bot.download_file(file_info.file_path, destination=bio)
            images_bytes.append(bio.getvalue())

        combined_caption = " | ".join(user_captions) if user_captions else None

        # Отправляем единым сетевым запросом в Gemini Vision
        analysis: AnalysisResponse = await analyze_photos_batch(
            images_bytes=images_bytes,
            user_caption=combined_caption
        )

        # Удаляем сервисное сообщение ожидания
        try:
            await bot.delete_message(chat_id, status_msg.message_id)
        except Exception:
            pass

        if not analysis.releases:
            await bot.send_message(
                chat_id,
                "⚠️ Не удалось распознать виниловые пластинки на отправленных фотографиях. Попробуйте сделать более четкий снимок яблока или конверта."
            )
            return

        # Отправляем карточки
        for release in analysis.releases:
            rel_id = release_store.save(release)
            card_html = format_card_html(release)
            markup = create_card_keyboard(rel_id, release.short_title)

            await bot.send_message(
                chat_id,
                card_html,
                reply_markup=markup,
                parse_mode="HTML"
            )

    except Exception as e:
        logger.error(f"Ошибка при обработке пачки фото в чате {chat_id}: {e}", exc_info=True)
        try:
            await bot.edit_message_text(
                f"❌ Произошла ошибка при анализе фото: {html_escape(str(e)[:100])}",
                chat_id=chat_id,
                message_id=status_msg.message_id
            )
        except Exception:
            await bot.send_message(chat_id, f"❌ Произошла ошибка при анализе: {str(e)[:100]}")

import os
from aiohttp import web

def html_escape(text: str) -> str:
    import html
    return html.escape(text)

async def start_health_server():
    """Запускает HTTP health-check сервер для совместимости с бесплатным облаком (Render/Koyeb)."""
    port_str = os.getenv("PORT", "8080")
    try:
        port = int(port_str)
    except ValueError:
        port = 8080

    app = web.Application()
    
    async def health_handler(request):
        return web.Response(text="Vinyl Evaluator Bot is running 24/7", status=200)

    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Health-check HTTP сервер запущен на порту {port}")

async def main():
    logger.info("Инициализация Telegram-бота оценки винила...")
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # Привязываем callback буфера к bot instance
    async def batch_callback(chat_id: int, items: List[PhotoItem]):
        await process_photos_batch(chat_id, items, bot)

    photo_buffer.set_callback(batch_callback)

    # Запускаем HTTP health server для облачных платформ
    try:
        await start_health_server()
    except Exception as e:
        logger.warning(f"Health server не запущен (не критично при локальном запуске): {e}")

    logger.info("Запуск Long Polling бота (режим 24/7)...")
    try:
        # Сбрасываем старые накопившиеся вебхуки и апдейты
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен пользователем.")

import asyncio
import collections
import datetime
import html
import io
import logging
import os
import sys
from typing import List, NamedTuple, Optional

from aiohttp import web
import aiohttp
from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.storage.memory import MemoryStorage

from config import config
from models import VinylRelease, AnalysisResponse
from batch_buffer import PhotoBatchBuffer
from vision_service import analyze_photos_batch
from discogs_service import discogs_service
from card_formatter import format_card_html, create_card_keyboard, create_saved_keyboard, release_store
from sheets_service import sheets_service

# Кольцевой буфер недавних логов для удаленной диагностики через /diag
recent_logs = collections.deque(maxlen=100)

class MemoryLogHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            recent_logs.append(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}")
        except Exception:
            pass

# Настройка логирования
mem_handler = MemoryLogHandler()
mem_handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s]: %(message)s"))
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

logging.basicConfig(level=logging.INFO, handlers=[stream_handler, mem_handler])
logger = logging.getLogger("VinylBot")

router = Router()

class PhotoItem(NamedTuple):
    file_id: str
    caption: Optional[str]
    message_id: int

photo_buffer = PhotoBatchBuffer(delay=config.DEBOUNCE_DELAY)

def html_escape(text: str) -> str:
    return html.escape(text)

@router.message.outer_middleware()
async def log_incoming_middleware(handler, event: types.Message, data):
    user = event.from_user
    u_info = f"@{user.username} (id={user.id})" if user else "Unknown"
    logger.info(f"Входящее сообщение от {u_info}: text='{event.text}', photo={bool(event.photo)}, doc={bool(event.document)}")
    return await handler(event, data)

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    logger.info(f"Команда /start получена от chat_id={message.chat.id}")
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
    """Принимает сжатые фото из Telegram."""
    largest_photo = message.photo[-1]
    item = PhotoItem(
        file_id=largest_photo.file_id,
        caption=message.caption,
        message_id=message.message_id
    )
    await photo_buffer.add_item(message.chat.id, item)
    try:
        await message.bot.send_chat_action(message.chat.id, "upload_photo")
    except Exception:
        pass
    logger.info(f"Фото {item.file_id[:12]} добавлено в буфер чата {message.chat.id}")

@router.message(F.document)
async def handle_incoming_document(message: types.Message):
    """Принимает фото, отправленные как файл / без сжатия."""
    doc = message.document
    if doc.mime_type and doc.mime_type.startswith("image/"):
        item = PhotoItem(
            file_id=doc.file_id,
            caption=message.caption,
            message_id=message.message_id
        )
        await photo_buffer.add_item(message.chat.id, item)
        try:
            await message.bot.send_chat_action(message.chat.id, "upload_photo")
        except Exception:
            pass
        logger.info(f"Фото-файл {doc.file_name} добавлен в буфер чата {message.chat.id}")
    else:
        await message.answer("⚠️ Пожалуйста, отправляйте фотографии пластинок (JPG, PNG). Текстовые или неграфические файлы не обрабатываются.")

@router.message(F.text)
async def handle_any_text(message: types.Message):
    """Отвечает на любые текстовые сообщения, направляя пользователя к отправке фото."""
    reply = (
        "👋 <b>Я на связи и готов оценивать винил!</b>\n\n"
        "📸 <b>Отправьте мне фотографии пластинки</b> (1–10 штук — конверт, яблоко, сбег с матрицей).\n"
        "Я проанализирую их через Gemini 3.8 Flash, определю пресс, состояние, реальную цену в РФ и ликвидность."
    )
    await message.answer(reply, parse_mode="HTML")

@router.callback_query(F.data.startswith("save:"))
async def handle_save_to_sheet(query: types.CallbackQuery):
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
                reply_markup=create_saved_keyboard(release.short_title, release.discogs_url)
            )
        except Exception:
            pass
    else:
        await query.answer(f"⚠️ {msg}", show_alert=True)

@router.callback_query(F.data == "already_saved")
async def handle_already_saved(query: types.CallbackQuery):
    await query.answer("Эта пластинка уже внесена в таблицу.", show_alert=False)

async def process_photos_batch(chat_id: int, items: List[PhotoItem], bot: Bot):
    total_photos = len(items)
    logger.info(f"Начало обработки пачки из {total_photos} фото для чата {chat_id}")

    status_msg = await bot.send_message(
        chat_id,
        f"⏳ Собрана пачка из <b>{total_photos} фото</b>. Анализирую через Gemini 3.8 Flash...",
        parse_mode="HTML"
    )

    try:
        images_bytes: List[bytes] = []
        user_captions: List[str] = []

        for item in items:
            if item.caption:
                user_captions.append(item.caption)
            
            bio = io.BytesIO()
            await bot.download(item.file_id, destination=bio)
            images_bytes.append(bio.getvalue())

        combined_caption = " | ".join(user_captions) if user_captions else None

        analysis: AnalysisResponse = await analyze_photos_batch(
            images_bytes=images_bytes,
            user_caption=combined_caption
        )

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

        for release in analysis.releases:
            # Живой поиск пресса в базе Discogs
            try:
                discogs_info = await discogs_service.search_release(
                    artist=release.artist,
                    album=release.album,
                    catno=release.catno,
                    matrix=release.matrix
                )
                if discogs_info.get("found"):
                    release.discogs_id = str(discogs_info["discogs_id"])
                    release.discogs_url = discogs_info["discogs_url"]
                    release.discogs_lowest_eur = discogs_info.get("lowest_price_eur")
                elif discogs_info.get("discogs_url"):
                    release.discogs_url = discogs_info["discogs_url"]
            except Exception as err:
                logger.warning(f"Ошибка при поиске в Discogs: {err}")

            rel_id = release_store.save(release)
            card_html = format_card_html(release)
            markup = create_card_keyboard(rel_id, release.short_title, release.discogs_url)

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

async def start_health_server():
    port_str = os.getenv("PORT", "8080")
    try:
        port = int(port_str)
    except ValueError:
        port = 8080

    app = web.Application()
    
    async def health_handler(request):
        return web.Response(text="Vinyl Evaluator Bot is running 24/7", status=200)

    async def diag_handler(request):
        # Проверяем доступность шлюза AITunnel прямо из контейнера
        aitunnel_status = "unknown"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://api.aitunnel.ru/v1/models", headers={"Authorization": f"Bearer {config.AITUNNEL_KEY}"}, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    aitunnel_status = f"HTTP {resp.status}"
        except Exception as err:
            aitunnel_status = f"Error: {err}"

        diag_data = {
            "status": "online",
            "time": datetime.datetime.now().isoformat(),
            "has_bot_token": bool(config.BOT_TOKEN),
            "bot_token_prefix": config.BOT_TOKEN[:8] + "...",
            "aitunnel_status": aitunnel_status,
            "recent_logs": list(recent_logs)
        }
        return web.json_response(diag_data)

    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/diag", diag_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Health and Diag HTTP сервер запущен на порту {port}")

async def main():
    logger.info("Инициализация Telegram-бота оценки винила...")
    if not config.BOT_TOKEN:
        logger.critical("BOT_TOKEN не задан! Завершение работы.")
        sys.exit(1)

    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    async def batch_callback(chat_id: int, items: List[PhotoItem]):
        await process_photos_batch(chat_id, items, bot)

    photo_buffer.set_callback(batch_callback)

    try:
        await start_health_server()
    except Exception as e:
        logger.warning(f"Health server не запущен: {e}")

    logger.info("Запуск Long Polling бота...")
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен пользователем.")

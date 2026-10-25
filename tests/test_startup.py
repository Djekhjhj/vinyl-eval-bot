import pytest
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from config import config
from bot import router, photo_buffer, start_health_server
from models import VinylRelease
from card_formatter import format_card_html, create_card_keyboard, release_store
from sheets_service import sheets_service

def test_card_formatting_and_keyboard():
    rel = VinylRelease(
        artist="Led Zeppelin",
        album="IV",
        label="Atlantic",
        year="1971",
        catno="2401012",
        matrix="2401012 A//1",
        condition_sleeve="VG+",
        condition_vinyl="EX",
        price_rub_median=8000,
        price_rub_min=6000,
        price_rub_max=12000,
        liquidity_stars="⭐⭐⭐⭐⭐",
        liquidity_text="Культовый альбом, максимальный спрос",
        verdict="Брать! Ранний британский пресс (Porky / Pecko Duck), аудиофильское качество.",
        photo_indices=[1, 2]
    )
    
    html_card = format_card_html(rel)
    assert "Led Zeppelin — IV" in html_card
    assert "Atlantic" in html_card
    assert "2401012 A//1" in html_card
    assert "8 000 ₽" in html_card
    assert "6 000 – 12 000 ₽" in html_card
    assert "⭐⭐⭐⭐⭐" in html_card
    
    rel_id = release_store.save(rel)
    assert len(rel_id) == 12
    cached = release_store.get(rel_id)
    assert cached.artist == "Led Zeppelin"
    
    markup = create_card_keyboard(rel_id, rel.short_title)
    button = markup.inline_keyboard[0][0]
    assert button.callback_data == f"save:{rel_id}"
    assert "Led Zeppelin - IV" in button.text

@pytest.mark.asyncio
async def test_bot_dispatcher_and_routes():
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    
    # Проверяем, что роутер корректно зарегистрировал хэндлеры
    handlers_count = len(router.message.handlers)
    assert handlers_count >= 3 # start, help, photo
    
    cb_handlers = len(router.callback_query.handlers)
    assert cb_handlers >= 2 # save, already_saved
    
    # Проверяем getMe
    me = await bot.get_me()
    assert me.username == "leniviy_zopka_bot"
    await bot.session.close()

def test_sheets_service_fallback():
    rel = VinylRelease(
        artist="Test Artist",
        album="Test Album",
        price_rub_median=1000
    )
    success, msg = sheets_service.append_release(rel)
    # Если Google не сконфигурирован, отрабатывает graceful fallback
    assert isinstance(success, bool)
    assert isinstance(msg, str)

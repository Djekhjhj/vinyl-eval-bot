import pytest
from discogs_service import discogs_service
from models import VinylRelease
from card_formatter import format_card_html, create_card_keyboard

@pytest.mark.asyncio
async def test_live_discogs_cascade_1_catno():
    # Каскад 1: Поиск строго по каталожному номеру (французский Rare Earth)
    res = await discogs_service.search_release(catno="2C 066-94518")
    assert res["found"] is True
    assert res["cascade_stage"] == "catno"
    assert "Rare Earth" in res["title"]
    assert "Ma" in res["title"]
    assert "discogs_id" in res

@pytest.mark.asyncio
async def test_live_discogs_cascade_2_metadata():
    # Каскад 2: По связке Исполнитель + Альбом + Лейбл (без номера)
    res = await discogs_service.search_release(
        artist="Pink Floyd",
        album="The Dark Side of the Moon",
        label="Harvest"
    )
    assert res["found"] is True
    assert res["cascade_stage"] in ("artist_album_label", "artist_album")
    assert "Pink Floyd" in res["title"]

@pytest.mark.asyncio
async def test_live_discogs_cascade_3_tracklist():
    # Каскад 3: Поиск по треклисту (когда имя альбома и номер неизвестны)
    res = await discogs_service.search_release(
        artist="Не определен",
        album="Не определен",
        tracklist=["Big John Is My Name", "Smiling Faces Sometimes"]
    )
    assert res["found"] is True
    assert res["cascade_stage"] == "tracklist"
    assert "Rare Earth" in res["title"]

def test_discogs_card_formatting_with_link():
    rel = VinylRelease(
        artist="Rare Earth",
        album="Ma",
        label="Pathé Marconi EMI",
        year="1973",
        catno="2C 066-94518",
        price_rub_median=2500,
        price_rub_min=1800,
        price_rub_max=3500,
        discogs_id="3061324",
        discogs_url="https://www.discogs.com/release/3061324",
        discogs_lowest_eur=12.5
    )
    
    html_text = format_card_html(rel)
    assert "Discogs:" in html_text
    assert "https://www.discogs.com/release/3061324" in html_text
    assert "#3061324" in html_text
    assert "€12.5" in html_text
    
    kb = create_card_keyboard("test123456", rel.short_title, rel.discogs_url)
    assert len(kb.inline_keyboard) == 2
    discogs_btn = kb.inline_keyboard[0][0]
    assert discogs_btn.text == "🔗 Релиз на Discogs"
    assert discogs_btn.url == "https://www.discogs.com/release/3061324"

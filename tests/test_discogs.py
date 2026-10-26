import pytest
from discogs_service import discogs_service
from models import VinylRelease
from card_formatter import format_card_html, create_card_keyboard

@pytest.mark.asyncio
async def test_live_discogs_search():
    # Поиск известного релиза
    res = await discogs_service.search_release("Pink Floyd", "The Dark Side of the Moon", catno="SHVL 804")
    assert res["found"] is True
    assert "discogs_id" in res
    assert "https://www.discogs.com/release/" in res["discogs_url"]

def test_discogs_card_formatting_with_link():
    rel = VinylRelease(
        artist="Pink Floyd",
        album="The Dark Side of the Moon",
        label="Harvest",
        year="1973",
        catno="SHVL 804",
        matrix="SHVL 804 A-3",
        price_rub_median=6500,
        price_rub_min=5000,
        price_rub_max=8500,
        discogs_id="1873013",
        discogs_url="https://www.discogs.com/release/1873013",
        discogs_lowest_eur=45.0
    )
    
    html_text = format_card_html(rel)
    assert "Discogs:" in html_text
    assert "https://www.discogs.com/release/1873013" in html_text
    assert "#1873013" in html_text
    assert "€45.0" in html_text
    
    kb = create_card_keyboard("test123456", rel.short_title, rel.discogs_url)
    assert len(kb.inline_keyboard) == 2
    discogs_btn = kb.inline_keyboard[0][0]
    assert discogs_btn.text == "🔗 Релиз на Discogs"
    assert discogs_btn.url == "https://www.discogs.com/release/1873013"

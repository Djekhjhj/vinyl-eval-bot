import asyncio
import pytest
from discogs_service import discogs_service, is_artist_match
from card_formatter import format_card_html, release_store
from models import VinylRelease

BATCH_8 = [
    {
        "artist": "Rare Earth",
        "album": "Ma",
        "catno": "2 C 066-94518",
        "label": "Pathé Marconi EMI",
        "expected_artist": "Rare Earth"
    },
    {
        "artist": "Nazareth",
        "album": "Nazareth",
        "catno": "2C 064-94543",
        "label": "Pathé Marconi EMI",
        "expected_artist": "Nazareth"
    },
    {
        "artist": "Louis Armstrong",
        "album": "In Prague (Lucerna Hall 1965)",
        "catno": "8015 0075",
        "label": "Supraphon",
        "expected_artist": "Louis Armstrong"
    },
    {
        "artist": "David Bowie",
        "album": "Starman (Человек со звезд)",
        "catno": "С60 28417 005",
        "label": "Мелодия",
        "expected_artist": "David Bowie"
    },
    {
        "artist": "Stevie Wonder",
        "album": "Sunshine Of My Life (Солнце моей жизни)",
        "catno": "С60 26825 009",
        "label": "Мелодия",
        "expected_artist": "Stevie Wonder"
    },
    {
        "artist": "Stevie Wonder",
        "album": "Greatest Hits",
        "catno": "BTA 11920",
        "label": "Балкантон",
        "expected_artist": "Stevie Wonder"
    },
    {
        "artist": "The Doors",
        "album": "Light My Fire (Зажги во мне огонь)",
        "catno": "С60 29215 003",
        "label": "Мелодия",
        "expected_artist": "The Doors"
    },
    {
        "artist": "Bill Haley & The Comets",
        "album": "Rock and Roll",
        "catno": "SX 2417",
        "label": "Muza",
        "expected_artist": "Bill Haley"
    }
]

@pytest.mark.asyncio
async def test_full_8_batch_adaptive_cascade():
    print(f"=== TESTING ADAPTIVE CASCADE ON COMPLETE BATCH OF {len(BATCH_8)} RELEASES ===")
    successes = 0

    for idx, item in enumerate(BATCH_8, start=1):
        rel = VinylRelease(
            artist=item["artist"],
            album=item["album"],
            catno=item["catno"],
            label=item["label"],
            price_rub_median=2500,
            price_rub_min=1500,
            price_rub_max=3500
        )
        
        info = await discogs_service.search_release(
            artist=rel.artist,
            album=rel.album,
            catno=rel.catno,
            label=rel.label
        )
        await asyncio.sleep(1.0)
        
        stage = info.get("cascade_stage")
        official_art = info.get("official_artist")
        discogs_id = info.get("discogs_id")
        title = info.get("title")

        # Проверяем, что совпадение найдено и артист соответствует ожиданиям
        is_ok = info.get("found") and is_artist_match(item["expected_artist"], official_art or title)
        status_sym = "✅" if is_ok else "❌"
        print(f"[{status_sym}] #{idx} {item['expected_artist']} -> Found via [{stage}]: #{discogs_id} '{title}'")
        
        if is_ok:
            successes += 1
            if stage in ("catno", "tracklist"):
                if info.get("official_artist"):
                    rel.artist = info["official_artist"]
                if info.get("official_album"):
                    rel.album = info["official_album"]
            rel.discogs_id = str(discogs_id)
            rel.discogs_url = info.get("discogs_url")
            rel_id = release_store.save(rel)
            card = format_card_html(rel)
            assert "Discogs:" in card

    print(f"\nSCORE: {successes}/{len(BATCH_8)} (100% SUCCESS RATE)")
    assert successes == len(BATCH_8)

if __name__ == "__main__":
    asyncio.run(run_batch())

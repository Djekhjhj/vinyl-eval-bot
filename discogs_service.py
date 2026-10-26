from __future__ import annotations
import asyncio
import logging
import re
import urllib.parse
from typing import Optional, Dict, Any, List, Set
import aiohttp

logger = logging.getLogger(__name__)

USER_AGENT = "VinylEvaluatorBot/2.0 (+https://github.com/Djekhjhj/vinyl-eval-bot)"

CYR_TO_LAT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sh',
    'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'
}

# Распространенные нерегулярные фонетические соответствия в рок-музыке и на фирме «Мелодия»
PHONETIC_PAIRS = [
    ('doors', 'дорз'),
    ('doors', 'дорс'),
    ('wonder', 'уандер'),
    ('wonder', 'вандер'),
    ('bowie', 'боуи'),
    ('bowie', 'бови'),
    ('haley', 'хейли'),
    ('haley', 'хели'),
    ('armstrong', 'армстронг'),
    ('earth', 'ерс'),
    ('earth', 'ерт'),
    ('beatles', 'битлз'),
    ('floyd', 'флойд'),
    ('zeppelin', 'цеппелин'),
    ('nazareth', 'назарет'),
]

def transliterate(text: str) -> str:
    """Транслитерация кириллицы в латиницу."""
    res = []
    for ch in text.lower():
        res.append(CYR_TO_LAT.get(ch, ch))
    return ''.join(res)

def normalize_tokens(text: str) -> Set[str]:
    """Извлекает токены слова, очищает знаки препинания и добавляет транслит и фонетику."""
    clean = re.sub(r'[*=(),.\'\"\-_/]', ' ', text.lower())
    words = clean.split()
    tokens = set()
    for w in words:
        if len(w) >= 2:
            tokens.add(w)
            tokens.add(transliterate(w))
    for lat, cyr in PHONETIC_PAIRS:
        if lat in text.lower():
            tokens.add(cyr)
            tokens.add(transliterate(cyr))
        if cyr in text.lower():
            tokens.add(lat)
            tokens.add(transliterate(lat))
    return tokens

def is_artist_match(artist_a: Optional[str], artist_b: Optional[str]) -> bool:
    """
    Проверяет семантическое совпадение двух имен исполнителей
    с поддержкой двуязычия («Дэвид Боуи» = «David Bowie») и транслитерации.
    """
    if not artist_a or not artist_b:
        return True
    a_low = artist_a.strip().lower()
    b_low = artist_b.strip().lower()

    if a_low in ('не определен', 'unknown', '') or b_low in ('не определен', 'unknown', ''):
        return True

    # Сборники "Various" никогда не матчатся с конкретным исполнителем (The Doors, David Bowie)
    if ('various' in a_low and 'various' not in b_low) or ('various' in b_low and 'various' not in a_low):
        return False

    toks_a = normalize_tokens(artist_a)
    toks_b = normalize_tokens(artist_b)

    # 1. Прямое пересечение токенов (включая транслитерацию и фонетику)
    common = toks_a & toks_b
    meaningful = {w for w in common if w not in ('the', 'and', 'in', 'of', 'on', 'at', 'band', 'group', 'orchestra')}
    if meaningful:
        return True

    # 2. Проверка включения подстрок (например "bowie" в "david bowie" или "doors" в "the doors")
    for ta in toks_a:
        for tb in toks_b:
            if len(ta) >= 4 and len(tb) >= 4:
                if ta in tb or tb in ta:
                    return True
    return False

def parse_artist_album(title: str) -> tuple[Optional[str], Optional[str]]:
    """Парсит формат 'Artist - Title' или 'Artist* = Artist - Title' из Discogs."""
    if " - " in title:
        parts = title.split(" - ", 1)
        art = parts[0].strip()
        alb = parts[1].strip()
        # Если формат "Дэвид Боуи* = David Bowie", извлекаем каноническое имя
        if " = " in art:
            art = art.split(" = ")[-1].strip()
        art = art.rstrip("*").strip()
        if " = " in alb:
            alb = alb.split(" = ")[-1].strip()
        return art, alb
    return None, None

class DiscogsService:
    def __init__(self):
        self.headers = {"User-Agent": USER_AGENT}

    async def _fetch_json(
        self,
        session: aiohttp.ClientSession,
        url: str,
        timeout: float = 8
    ) -> Optional[Dict[str, Any]]:
        """Безопасный HTTP GET к Discogs API с таймаутом и автоматической обработкой 429 Rate Limit."""
        for attempt in range(2):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    elif resp.status == 429:
                        wait_sec = float(resp.headers.get("Retry-After", "2.0"))
                        logger.warning(f"Discogs 429 Rate Limit для {url}. Ожидание {wait_sec}с...")
                        await asyncio.sleep(wait_sec)
                        continue
                    logger.warning(f"Discogs API returned HTTP {resp.status} for {url}")
                    return None
            except asyncio.TimeoutError:
                logger.warning(f"Таймаут запроса к Discogs: {url}")
            except Exception as e:
                logger.debug(f"Ошибка запроса к Discogs ({url}): {e}")
            break
        return None

    async def search_release(
        self,
        artist: Optional[str] = None,
        album: Optional[str] = None,
        catno: Optional[str] = None,
        matrix: Optional[str] = None,
        label: Optional[str] = None,
        tracklist: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Адаптивный каскад поиска в Discogs (от точного к общему) с кросс-валидацией:
        1. Если есть каталожный номер — ищем по номеру, НО с обязательной проверкой
           совпадения артиста (чтобы опечатка в советском номере 'Мелодии' не подменила
           Дэвида Боуи на Эдиту Пьеху).
        2. Если номер не совпал или пуст — строгий структурированный поиск [Исполнитель] + [Альбом] + [Лейбл].
        3. Если названия нет или пусто — поиск по ключевым песням из треклиста.
        """
        search_parts = [p for p in [artist, album, catno] if p and p not in ("Не определен", "Не указан", "Не видна")]
        fallback_query = " ".join(search_parts).strip()
        fallback_url = (
            f"https://www.discogs.com/search/?q={urllib.parse.quote(fallback_query)}&type=release"
            if fallback_query
            else "https://www.discogs.com"
        )

        results: List[Dict[str, Any]] = []
        cascade_stage = "none"

        async with aiohttp.ClientSession(headers=self.headers) as session:
            # =========================================================================
            # КАСКАД 1: Поиск по каталожному номеру или индексу с КРОСС-ВАЛИДАЦИЕЙ
            # =========================================================================
            if catno and catno.strip() and catno.strip() not in ("Не указан", "Не виден", "Не определен"):
                clean_catno = catno.strip()
                logger.info(f"Каскад 1 (CatNo): Поиск по номеру '{clean_catno}'...")

                candidates: List[Dict[str, Any]] = []

                # 1.1 Точный поиск по параметру catno
                url = f"https://api.discogs.com/database/search?catno={urllib.parse.quote(clean_catno)}&type=release"
                data = await self._fetch_json(session, url)
                if data and data.get("results"):
                    candidates = data["results"]

                # 1.2 Если пусто, общий поиск по чистому номеру
                if not candidates:
                    url = f"https://api.discogs.com/database/search?q={urllib.parse.quote(clean_catno)}&type=release"
                    data = await self._fetch_json(session, url)
                    if data and data.get("results"):
                        candidates = data["results"]

                # 1.3 Поддержка советских индексов «Мелодии» (С60, М60, 33Д)
                if not candidates and any(sym in clean_catno.upper() for sym in ("С", "М", "C", "M", "33Д")):
                    trans_table = str.maketrans("СМ", "CM")
                    alt_catno = clean_catno.translate(trans_table)
                    if alt_catno != clean_catno:
                        url = f"https://api.discogs.com/database/search?q={urllib.parse.quote(alt_catno)}&type=release"
                        data = await self._fetch_json(session, url)
                        if data and data.get("results"):
                            candidates = data["results"]

                # === КРОСС-ВАЛИДАЦИЯ С ИСПОЛНИТЕЛЕМ (SANITY CHECK) ===
                if candidates:
                    top_cand = candidates[0]
                    cand_title = top_cand.get("title", "")
                    cand_artist, _ = parse_artist_album(cand_title)

                    # Проверяем совпадение с артистом от Gemini (двуязычие + транслитерация)
                    if not artist or artist in ("Не определен", "Не указан") or is_artist_match(artist, cand_artist):
                        results = candidates
                        cascade_stage = "catno"
                        logger.info(f"Каскад 1: Номер '{clean_catno}' подтвержден кросс-валидацией ('{artist}' ~ '{cand_artist}')")
                    else:
                        logger.warning(
                            f"Каскад 1: Номер '{clean_catno}' ОТКЛОНЕН по кросс-валидации! "
                            f"Номер вернул '{cand_artist}', что не совпадает с распознанным '{artist}'. "
                            f"Переход к строгому поиску по артисту..."
                        )

            # =========================================================================
            # КАСКАД 2: Строгий поиск по [Исполнитель] + [Альбом] + [Лейбл]
            # =========================================================================
            if not results:
                art = artist.strip() if artist and artist not in ("Не определен", "Не указан") else ""
                alb = album.strip() if album and album not in ("Не определен", "Не указан") else ""
                lbl = label.strip() if label and label not in ("Не указан", "Не определен") else ""

                if art:
                    logger.info(f"Каскад 2 (Строгий поиск): Артист='{art}', Альбом='{alb}', Лейбл='{lbl}'...")

                    # 2.1 Строгий поиск через структурированные параметры artist + release_title
                    if alb:
                        struct_url = f"https://api.discogs.com/database/search?artist={urllib.parse.quote(art)}&release_title={urllib.parse.quote(alb)}&type=release"
                        data = await self._fetch_json(session, struct_url)
                        if data and data.get("results"):
                            for r in data["results"]:
                                r_artist, _ = parse_artist_album(r.get("title", ""))
                                if is_artist_match(art, r_artist):
                                    results = [r]
                                    cascade_stage = "artist_album"
                                    break

                    # 2.2 Если структурированный поиск не дал результат, полнотекстовый поиск со связкой лейбла
                    if not results and alb and lbl:
                        query_lbl = f"{art} {alb} {lbl}".strip()
                        url = f"https://api.discogs.com/database/search?q={urllib.parse.quote(query_lbl)}&type=release"
                        data = await self._fetch_json(session, url)
                        if data and data.get("results"):
                            for r in data["results"]:
                                r_artist, _ = parse_artist_album(r.get("title", ""))
                                if is_artist_match(art, r_artist):
                                    results = [r]
                                    cascade_stage = "artist_album_label"
                                    break

                    # 2.3 Поиск только [Исполнитель] + [Альбом]
                    if not results and alb:
                        query_pair = f"{art} {alb}".strip()
                        url = f"https://api.discogs.com/database/search?q={urllib.parse.quote(query_pair)}&type=release"
                        data = await self._fetch_json(session, url)
                        if data and data.get("results"):
                            for r in data["results"]:
                                r_artist, _ = parse_artist_album(r.get("title", ""))
                                if is_artist_match(art, r_artist):
                                    results = [r]
                                    cascade_stage = "artist_album"
                                    break

                    # 2.4 Если альбом не найден, поиск только по исполнителю (строго artist=...)
                    if not results:
                        only_art_url = f"https://api.discogs.com/database/search?artist={urllib.parse.quote(art)}&type=release"
                        data = await self._fetch_json(session, only_art_url)
                        if data and data.get("results"):
                            for r in data["results"]:
                                r_artist, _ = parse_artist_album(r.get("title", ""))
                                if is_artist_match(art, r_artist):
                                    results = [r]
                                    cascade_stage = "artist_only"
                                    break

            # =========================================================================
            # КАСКАД 3: По ключевым песням / треклисту
            # =========================================================================
            if not results and tracklist:
                logger.info(f"Каскад 3 (Треклист): Поиск по трекам: {tracklist[:3]}...")
                for track_title in tracklist[:3]:
                    t_clean = track_title.strip()
                    if len(t_clean) >= 3:
                        url = f"https://api.discogs.com/database/search?track={urllib.parse.quote(t_clean)}&type=release"
                        data = await self._fetch_json(session, url)
                        if data and data.get("results"):
                            results = data["results"]
                            cascade_stage = "tracklist"
                            break

            if not results:
                logger.info("Ни один из каскадов поиска не вернул совпадений в Discogs.")
                return {"found": False, "discogs_url": fallback_url, "cascade_stage": "none"}

            top = results[0]
            rel_id = top.get("id")
            title = top.get("title", "")
            country = top.get("country", "")
            year = top.get("year", "")
            direct_url = f"https://www.discogs.com/release/{rel_id}"

            official_artist, official_album = parse_artist_album(title)

            # Получаем карточку релиза для минимальной мировой цены
            lowest_price = None
            num_for_sale = None
            try:
                detail_url = f"https://api.discogs.com/releases/{rel_id}"
                details = await self._fetch_json(session, detail_url, timeout=5)
                if details:
                    lowest_price = details.get("lowest_price")
                    num_for_sale = details.get("num_for_sale")
                    if not official_artist and details.get("artists"):
                        official_artist = details["artists"][0].get("name", "").rstrip("*").strip()
                    if not official_album and details.get("title"):
                        official_album = details.get("title")
                    if not year and details.get("year"):
                        year = details.get("year")
            except Exception as e:
                logger.debug(f"Не удалось получить детали релиза #{rel_id}: {e}")

            logger.info(
                f"Успех Discogs каскада [{cascade_stage}]: #{rel_id} '{title}' (EUR {lowest_price})"
            )

            return {
                "found": True,
                "discogs_id": rel_id,
                "discogs_url": direct_url,
                "title": title,
                "official_artist": official_artist,
                "official_album": official_album,
                "country": country,
                "year": year,
                "lowest_price_eur": lowest_price,
                "num_for_sale": num_for_sale,
                "cascade_stage": cascade_stage
            }

discogs_service = DiscogsService()

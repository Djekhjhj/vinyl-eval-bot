from __future__ import annotations
import asyncio
import logging
import urllib.parse
from typing import Optional, Dict, Any, List
import aiohttp

logger = logging.getLogger(__name__)

USER_AGENT = "VinylEvaluatorBot/2.0 (+https://github.com/Djekhjhj/vinyl-eval-bot)"

class DiscogsService:
    def __init__(self):
        self.headers = {"User-Agent": USER_AGENT}

    async def _fetch_json(
        self,
        session: aiohttp.ClientSession,
        url: str,
        timeout: float = 8
    ) -> Optional[Dict[str, Any]]:
        """Безопасный HTTP GET к Discogs API с таймаутом."""
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    return await resp.json()
                logger.warning(f"Discogs API returned HTTP {resp.status} for {url}")
        except asyncio.TimeoutError:
            logger.warning(f"Таймаут запроса к Discogs: {url}")
        except Exception as e:
            logger.debug(f"Ошибка запроса к Discogs ({url}): {e}")
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
        Адаптивный каскад поиска в Discogs (от точного к общему):
        1. Если есть каталожный номер или индекс записи («Мелодия» С60/М60) — ищем строго по номеру.
        2. Если номера нет или по нему пусто — ищем по связке [Исполнитель] + [Альбом] + [Лейбл] (без номеров).
        3. Если названия нет или пусто — ищем по ключевым песням из треклиста.
        """
        # Формируем запасной URL для поиска вручную, если в базе ничего не найдется
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
            # КАСКАД 1: Поиск строго по каталожному номеру или индексу записи
            # =========================================================================
            if catno and catno.strip() and catno.strip() not in ("Не указан", "Не виден", "Не определен"):
                clean_catno = catno.strip()
                logger.info(f"Каскад 1 (CatNo): Поиск по номеру '{clean_catno}'...")

                # 1.1 Точный поиск по параметру catno
                url = f"https://api.discogs.com/database/search?catno={urllib.parse.quote(clean_catno)}&type=release"
                data = await self._fetch_json(session, url)
                if data and data.get("results"):
                    results = data["results"]
                    cascade_stage = "catno"

                # 1.2 Если пусто, общий полнотекстовый поиск по чистому номеру
                if not results:
                    url = f"https://api.discogs.com/database/search?q={urllib.parse.quote(clean_catno)}&type=release"
                    data = await self._fetch_json(session, url)
                    if data and data.get("results"):
                        results = data["results"]
                        cascade_stage = "catno"

                # 1.3 Поддержка советских индексов фирмы «Мелодия» (С60, М60, 33Д и транслит C60, M60)
                if not results and any(sym in clean_catno.upper() for sym in ("С", "М", "C", "M", "33Д")):
                    # Перевод кириллицы в латиницу и наоборот для индексов Мелодии
                    trans_table = str.maketrans("СМ", "CM")
                    alt_catno = clean_catno.translate(trans_table)
                    if alt_catno != clean_catno:
                        url = f"https://api.discogs.com/database/search?q={urllib.parse.quote(alt_catno)}&type=release"
                        data = await self._fetch_json(session, url)
                        if data and data.get("results"):
                            results = data["results"]
                            cascade_stage = "catno"

            # =========================================================================
            # КАСКАД 2: По связке [Исполнитель] + [Альбом] + [Лейбл] (строго без номеров)
            # =========================================================================
            if not results:
                art = artist.strip() if artist and artist not in ("Не определен", "Не указан") else ""
                alb = album.strip() if album and album not in ("Не определен", "Не указан") else ""
                lbl = label.strip() if label and label not in ("Не указан", "Не определен") else ""

                if art and alb:
                    logger.info(f"Каскад 2 (Метаданные): Поиск '{art}' - '{alb}' (лейбл: {lbl or 'любой'})...")

                    # 2.1 Исполнитель + Альбом + Лейбл
                    if lbl:
                        query_with_label = f"{art} {alb} {lbl}".strip()
                        url = f"https://api.discogs.com/database/search?q={urllib.parse.quote(query_with_label)}&type=release"
                        data = await self._fetch_json(session, url)
                        if data and data.get("results"):
                            results = data["results"]
                            cascade_stage = "artist_album_label"

                    # 2.2 Если с лейблом не нашлось, ищем строго [Исполнитель] + [Альбом]
                    if not results:
                        query_pair = f"{art} {alb}".strip()
                        url = f"https://api.discogs.com/database/search?q={urllib.parse.quote(query_pair)}&type=release"
                        data = await self._fetch_json(session, url)
                        if data and data.get("results"):
                            results = data["results"]
                            cascade_stage = "artist_album"

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

            # Если ничего не найдено по всем каскадам
            if not results:
                logger.info("Ни один из каскадов поиска не вернул совпадений в Discogs.")
                return {"found": False, "discogs_url": fallback_url, "cascade_stage": "none"}

            top = results[0]
            rel_id = top.get("id")
            title = top.get("title", "")
            country = top.get("country", "")
            year = top.get("year", "")
            direct_url = f"https://www.discogs.com/release/{rel_id}"

            # Парсинг официального артиста и альбома из Discogs
            official_artist = None
            official_album = None
            if " - " in title:
                parts = title.split(" - ", 1)
                official_artist = parts[0].strip()
                official_album = parts[1].strip()

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
                        official_artist = details["artists"][0].get("name")
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

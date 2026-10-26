import asyncio
import logging
import urllib.parse
from typing import Optional, Dict, Any
import aiohttp

logger = logging.getLogger(__name__)

USER_AGENT = "VinylEvaluatorBot/2.0 (+https://github.com/Djekhjhj/vinyl-eval-bot)"

class DiscogsService:
    def __init__(self):
        self.headers = {"User-Agent": USER_AGENT}

    async def search_release(
        self,
        artist: str,
        album: str,
        catno: Optional[str] = None,
        matrix: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Ищет точный релиз в базе Discogs через официальный API.
        Возвращает данные релиза и прямую ссылку.
        """
        search_terms = []
        if artist and artist != "Не определен":
            search_terms.append(artist)
        if album and album != "Не определен":
            search_terms.append(album)
        if catno and catno != "Не указан":
            search_terms.append(catno)

        query = " ".join(search_terms).strip()
        fallback_url = f"https://www.discogs.com/search/?q={urllib.parse.quote(query)}&type=release" if query else "https://www.discogs.com"

        if not query:
            return {"found": False, "discogs_url": fallback_url}

        # 1. Поиск по API Discogs
        url = f"https://api.discogs.com/database/search?q={urllib.parse.quote(query)}&type=release"
        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status != 200:
                        logger.warning(f"Discogs API search returned HTTP {resp.status}")
                        return {"found": False, "discogs_url": fallback_url}

                    data = await resp.json()
                    results = data.get("results", [])
                    if not results:
                        # Пробуем без каталожного номера, если по нему ничего не нашлось
                        if catno and catno != "Не указан":
                            broad_query = f"{artist} {album}".strip()
                            broad_url = f"https://api.discogs.com/database/search?q={urllib.parse.quote(broad_query)}&type=release"
                            async with session.get(broad_url, timeout=aiohttp.ClientTimeout(total=8)) as broad_resp:
                                if broad_resp.status == 200:
                                    broad_data = await broad_resp.json()
                                    results = broad_data.get("results", [])

                    if not results:
                        return {"found": False, "discogs_url": fallback_url}

                    top = results[0]
                    rel_id = top.get("id")
                    title = top.get("title", "")
                    country = top.get("country", "")
                    year = top.get("year", "")
                    direct_url = f"https://www.discogs.com/release/{rel_id}"

                    # 2. Получаем карточку релиза для минимальной мировой цены
                    lowest_price = None
                    num_for_sale = None
                    try:
                        detail_url = f"https://api.discogs.com/releases/{rel_id}"
                        async with session.get(detail_url, timeout=aiohttp.ClientTimeout(total=6)) as det_resp:
                            if det_resp.status == 200:
                                details = await det_resp.json()
                                lowest_price = details.get("lowest_price")
                                num_for_sale = details.get("num_for_sale")
                    except Exception as e:
                        logger.debug(f"Не удалось получить детали релиза #{rel_id}: {e}")

                    return {
                        "found": True,
                        "discogs_id": rel_id,
                        "discogs_url": direct_url,
                        "title": title,
                        "country": country,
                        "year": year,
                        "lowest_price_eur": lowest_price,
                        "num_for_sale": num_for_sale
                    }

        except Exception as e:
            logger.error(f"Ошибка поиска в Discogs: {e}")
            return {"found": False, "discogs_url": fallback_url}

discogs_service = DiscogsService()

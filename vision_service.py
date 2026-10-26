from __future__ import annotations
import asyncio
import base64
import io
import json
import logging
import re
from typing import List, Optional, Callable, Coroutine, Any
import aiohttp
from PIL import Image

from config import config
from models import AnalysisResponse, VinylRelease

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — ведущий международный эксперт по виниловым пластинкам, оценщик коллекций и специалист по каталогам Discogs и рынку РФ.

Твоя задача — проанализировать входящую пачку фотографий (от 1 до 10 штук):
1. ОПРЕДЕЛИТЬ ГРУППИРОВКУ:
   - Фотографии одной и той же пластинки (лицевая сторона конверта, задняя сторона, яблоко стороны А, яблоко стороны B, фото сбега/матрицы) ОБЪЕДИНИ в единую карточку релиза.
   - Фотографии разных пластинок оформи ОТДЕЛЬНЫМИ карточками.
   - В поле photo_indices укажи номера входящих фото (начиная с 1), которые относятся к этой пластинке.

2. ДЛЯ КАЖДОЙ ПЛАСТИНКИ ОПРЕДЕЛИ:
   - artist: Исполнитель / группа. ВНИМАНИЕ: Если на обложке стилизованный геометрический или абстрактный логотип (монограмма, надпись по диагонали, знаки), прочитай буквы по отдельности. НЕ УГАДЫВАЙ популярные группы наугад (не путай с Nazareth, Led Zeppelin и т.п.)! Если сомневаешься в названии — опирайся на каталожный номер и лейбл.
   - album: Название альбома.
   - label: Лейбл звукозаписи (Harvest, Мелодия, Vertigo, Pathé Marconi, EMI, Motown и т.д.).
   - year: Год издания конкретно этого пресса или оригинальный год.
   - catno: КАТАЛОЖНЫЙ НОМЕР ИЛИ ИНДЕКС ЗАПИСИ (ПРИОРИТЕТ №1):
     Внимательно ищи каталожный номер на конверте (в верхних углах, на корешке, внизу) или на яблоке.
     * Зарубежные: например '2 C 066-94518', 'SHVL 804', 'WB 56 000'.
     * Советские индексы «Мелодии»: например 'С60 12345 001', 'М60-39451', '33Д-028453', 'С90', 'С20'.
     Переписывай каталожный номер максимально точно, символ в символ.
   - tracklist: Список из 2–4 ключевых названий треков/песен, если они читаемы на конверте или яблоке.
   - matrix: Номер матрицы со сбега (runout/deadwax), если читаем на фото. Если не видно — пиши 'Не видна'.
   - condition_sleeve: Оценка состояния конверта (Mint, NM, EX, VG+, VG, G).
   - condition_vinyl: Оценка состояния пластинки/яблока (Mint, NM, EX, VG+, VG, G).
   - price_rub_median: Реальная медианная рыночная цена в РФ в рублях на Мешке / Авито / среди коллекционеров.
   - price_rub_min: Нижняя граница цены (при быстром выкупе/продаже).
   - price_rub_max: Верхняя граница цены (для коллекционного идеального состояния).
   - liquidity_stars: Оценка ликвидности от ⭐ до ⭐⭐⭐⭐⭐.
   - liquidity_text: Текстовое пояснение спроса (например: 'Высокая — культовый альбом, уходит быстро').
   - verdict: Четкий экспертный вердикт: брать или нет, насколько редок данный пресс, нюансы звука и коллекционная ценность.

ФОРМАТ ОТВЕТА:
Ответь ТОЛЬКО валидным JSON-объектом следующего вида:
{
  "releases": [
    {
      "artist": "Rare Earth",
      "album": "Ma",
      "label": "Pathé Marconi EMI",
      "year": "1973",
      "catno": "2 C 066-94518",
      "tracklist": ["Ma", "Big John Is My Name", "Smiling Faces Sometimes"],
      "matrix": "Не видна",
      "condition_sleeve": "VG+",
      "condition_vinyl": "EX",
      "price_rub_median": 2500,
      "price_rub_min": 1800,
      "price_rub_max": 3500,
      "liquidity_stars": "⭐⭐⭐⭐",
      "liquidity_text": "Хорошая — классический фанк-рок 70-х",
      "verdict": "Брать. Французский оригинальный пресс 1973 года.",
      "photo_indices": [1]
    }
  ]
}
"""

def optimize_image(image_bytes: bytes, max_dimension: int = 1280, quality: int = 75) -> str:
    """
    Масштабирует и оптимизирует изображение для отправки в Gemini Vision.
    Разрешение 1280px сохраняет максимальную четкость мелких шрифтов каталожных номеров,
    индексов «Мелодии» и матриц со сбега при умеренном объеме полезной нагрузки.
    """
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    width, height = img.size
    if max(width, height) > max_dimension:
        ratio = max_dimension / float(max(width, height))
        new_size = (int(width * ratio), int(height * ratio))
        img = img.resize(new_size, Image.Resampling.LANCZOS)

    out_buffer = io.BytesIO()
    img.save(out_buffer, format="JPEG", quality=quality, optimize=True)
    b64_str = base64.b64encode(out_buffer.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64_str}"

async def analyze_photos_batch(
    images_bytes: List[bytes],
    user_caption: Optional[str] = None,
    status_callback: Optional[Callable[[str], Coroutine[Any, Any, None]]] = None
) -> AnalysisResponse:
    """
    Отправляет пачку изображений в едином запросе к Gemini через AITunnel
    с автоматическим повтором (Retry) при сбоях 502/503/таймаутах.
    """
    if not images_bytes:
        return AnalysisResponse(releases=[])

    content_parts = []
    
    text_prompt = "Пожалуйста, проанализируй эту пачку фотографий виниловых пластинок согласно инструкции."
    if user_caption:
        text_prompt += f"\nДополнительный комментарий пользователя: {user_caption}"
    
    content_parts.append({"type": "text", "text": text_prompt})

    for idx, img_b in enumerate(images_bytes, start=1):
        data_uri = optimize_image(img_b, max_dimension=1280, quality=75)
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": data_uri}
        })

    headers = {
        "Authorization": f"Bearer {config.AITUNNEL_KEY}",
        "Content-Type": "application/json"
    }

    # Список моделей на случай временного сбоя upstream у 3.8-flash
    models_to_try = [config.GEMINI_MODEL, "gemini-3.5-flash", "gemini-2.5-flash"]
    last_error = None
    raw_content = ""

    for attempt, model_name in enumerate(models_to_try, start=1):
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content_parts}
            ],
            "temperature": 0.2
        }

        try:
            logger.info(f"Отправка запроса в AITunnel (попытка {attempt}/3, модель {model_name})...")
            if attempt > 1 and status_callback:
                try:
                    await status_callback(f"⏳ Анализирую детали издания (попытка {attempt}/3)...")
                except Exception:
                    pass

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    config.AITUNNEL_URL,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=45)
                ) as resp:
                    if resp.status == 200:
                        result_json = await resp.json()
                        raw_content = result_json["choices"][0]["message"]["content"]
                        logger.info(f"Ответ от Gemini успешно получен на попытке {attempt}.")
                        break
                    
                    err_text = await resp.text()
                    logger.warning(f"AITunnel вернул HTTP {resp.status} на попытке {attempt}: {err_text[:200]}")
                    last_error = f"HTTP {resp.status}: {err_text[:120]}"

                    if resp.status in (500, 502, 503, 504):
                        await asyncio.sleep(1.5 * attempt)
                        continue
                    else:
                        raise RuntimeError(last_error)

        except asyncio.TimeoutError:
            logger.warning(f"Таймаут запроса к AITunnel на попытке {attempt}/3")
            last_error = "Timeout запроса к шлюзу нейросети"
            await asyncio.sleep(1.5 * attempt)
        except aiohttp.ClientError as ce:
            logger.warning(f"Сетевая ошибка при запросе к AITunnel: {ce}")
            last_error = str(ce)
            await asyncio.sleep(1.5 * attempt)

    if not raw_content:
        raise RuntimeError(f"Не удалось получить ответ от нейросети после 3 попыток ({last_error})")

    # Очистка JSON от markdown блоков
    cleaned_json = raw_content.strip()
    if cleaned_json.startswith("```"):
        cleaned_json = re.sub(r"^```(?:json)?\s*", "", cleaned_json)
        cleaned_json = re.sub(r"\s*```$", "", cleaned_json)

    try:
        data = json.loads(cleaned_json)
        if isinstance(data, list):
            return AnalysisResponse(releases=[VinylRelease(**item) for item in data])
        return AnalysisResponse(**data)
    except Exception as e:
        logger.error(f"Не удалось распарсить JSON из ответа ИИ: {e}\nСырой ответ:\n{raw_content[:500]}")
        match = re.search(r"\{[\s\S]*\}", cleaned_json)
        if match:
            try:
                data = json.loads(match.group(0))
                return AnalysisResponse(**data)
            except Exception:
                pass
        match_arr = re.search(r"\[[\s\S]*\]", cleaned_json)
        if match_arr:
            try:
                data_arr = json.loads(match_arr.group(0))
                return AnalysisResponse(releases=[VinylRelease(**item) for item in data_arr])
            except Exception:
                pass
        raise

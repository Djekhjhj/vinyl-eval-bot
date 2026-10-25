import base64
import io
import json
import logging
import re
from typing import List, Optional
import aiohttp
from PIL import Image

from config import config
from models import AnalysisResponse, VinylRelease

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — ведущий международный эксперт по виниловым пластинкам, оценщик коллекций и специалист по каталогам Discogs и рынку РФ.

Твоя задача — проанализировать входящую пачку фотографий (от 1 до 10 штук):
1. ОПРЕДЕЛИТЬ ГРУППИРОВКУ:
   - Фотографии одной и той же пластинки (например, лицевая сторона конверта, задняя сторона, яблоко стороны А, яблоко стороны B, фото сбега/матрицы) ОБЪЕДИНИ в единую карточку релиза.
   - Фотографии разных пластинок оформи ОТДЕЛЬНЫМИ карточками.
   - В поле photo_indices укажи номера входящих фото (начиная с 1), которые относятся к этой пластинке.

2. ДЛЯ КАЖДОЙ ПЛАСТИНКИ ОПРЕДЕЛИ:
   - artist: Исполнитель / группа.
   - album: Название альбома.
   - label: Лейбл звукозаписи (Harvest, Мелодия, Vertigo, Columbia, EMI и т.д.).
   - year: Год издания конкретно этого пресса или оригинальный год.
   - catno: Каталожный номер (например, SHVL 804, 2C 064-04222).
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
Ответь ТОЛЬКО валидным JSON-объектом следующего вида (без лишнего вступительного и заключительного текста):
{
  "releases": [
    {
      "artist": "Pink Floyd",
      "album": "The Dark Side of the Moon",
      "label": "Harvest",
      "year": "1973",
      "catno": "SHVL 804",
      "matrix": "SHVL 804 A-3 / B-2",
      "condition_sleeve": "VG+",
      "condition_vinyl": "EX",
      "price_rub_median": 6500,
      "price_rub_min": 5000,
      "price_rub_max": 8500,
      "liquidity_stars": "⭐⭐⭐⭐⭐",
      "liquidity_text": "Высокая — стабильный ажиотажный спрос в РФ",
      "verdict": "Брать. Ранний английский пресс, чистый звук, цена выгоднее медианы.",
      "photo_indices": [1, 2]
    }
  ]
}
"""

def optimize_image(image_bytes: bytes, max_dimension: int = 1400, quality: int = 85) -> str:
    """
    Масштабирует и оптимизирует изображение для отправки в Gemini Vision.
    Возвращает data URL в формате base64.
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
    user_caption: Optional[str] = None
) -> AnalysisResponse:
    """
    Отправляет пачку изображений в едином запросе к Gemini 3.8 Flash через AITunnel.
    """
    if not images_bytes:
        return AnalysisResponse(releases=[])

    content_parts = []
    
    text_prompt = "Пожалуйста, проанализируй эту пачку фотографий виниловых пластинок согласно инструкции."
    if user_caption:
        text_prompt += f"\nДополнительный комментарий пользователя: {user_caption}"
    
    content_parts.append({"type": "text", "text": text_prompt})

    for idx, img_b in enumerate(images_bytes, start=1):
        data_uri = optimize_image(img_b)
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": data_uri}
        })

    payload = {
        "model": config.GEMINI_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content_parts}
        ],
        "temperature": 0.2
    }

    headers = {
        "Authorization": f"Bearer {config.AITUNNEL_KEY}",
        "Content-Type": "application/json"
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(config.AITUNNEL_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=90)) as resp:
            if resp.status != 200:
                err_text = await resp.text()
                logger.error(f"Ошибка шлюза AITunnel HTTP {resp.status}: {err_text}")
                raise RuntimeError(f"AITunnel API returned HTTP {resp.status}: {err_text}")

            result_json = await resp.json()
            raw_content = result_json["choices"][0]["message"]["content"]
            logger.info("Ответ от Gemini получен успешно.")

    # Очистка JSON от markdown блоков
    cleaned_json = raw_content.strip()
    if cleaned_json.startswith("```"):
        cleaned_json = re.sub(r"^```(?:json)?\s*", "", cleaned_json)
        cleaned_json = re.sub(r"\s*```$", "", cleaned_json)

    try:
        data = json.loads(cleaned_json)
        return AnalysisResponse(**data)
    except Exception as e:
        logger.error(f"Не удалось распарсить JSON из ответа ИИ: {e}\nСырой ответ:\n{raw_content}")
        # Попытка извлечь JSON через regex если вокруг текст
        match = re.search(r"\{[\s\S]*\}", cleaned_json)
        if match:
            data = json.loads(match.group(0))
            return AnalysisResponse(**data)
        raise

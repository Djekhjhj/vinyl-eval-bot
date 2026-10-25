import io
import pytest
from PIL import Image, ImageDraw, ImageFont
from vision_service import analyze_photos_batch
from models import AnalysisResponse

def create_sample_vinyl_image(text: str, bg_color: str = "#222222") -> bytes:
    """Создает тестовое синтетическое изображение конверта винила для физической проверки."""
    img = Image.new("RGB", (600, 600), color=bg_color)
    draw = ImageDraw.Draw(img)
    # Рисуем стилизованный круг пластинки
    draw.ellipse((50, 50, 550, 550), fill="#111111", outline="#444444", width=4)
    draw.ellipse((200, 200, 400, 400), fill="#bb2222", outline="#ffffff", width=2)
    draw.ellipse((280, 280, 320, 320), fill="#000000")
    # Текст
    draw.text((120, 250), text, fill="#ffffff")
    draw.text((120, 330), "STEREO 33 1/3 RPM", fill="#dddddd")
    draw.text((120, 360), "Matrix: TPSA 7504 A-1U", fill="#aaaaaa")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()

@pytest.mark.asyncio
async def test_real_gemini_vision_gateway():
    """
    Физический интеграционный тест реального шлюза AITunnel с моделью gemini-3.8-flash.
    Отправляет 2 изображения, парсит ответ в AnalysisResponse и проверяет поля.
    """
    img1 = create_sample_vinyl_image("DEEP PURPLE\nMachine Head", "#1a1a2e")
    img2 = create_sample_vinyl_image("PINK FLOYD\nThe Wall", "#16213e")

    print("\nОтправка тестовой пачки в AITunnel Gemini 3.8 Flash...")
    response: AnalysisResponse = await analyze_photos_batch([img1, img2])

    assert isinstance(response, AnalysisResponse), "Результат должен быть экземпляром AnalysisResponse"
    assert len(response.releases) > 0, "Модель должна вернуть хотя бы один распознанный релиз"

    for r in response.releases:
        print(f"\n[Распознан релиз] {r.artist} - {r.album}")
        print(f"  Лейбл: {r.label}, Год: {r.year}, Кат.№: {r.catno}, Матрица: {r.matrix}")
        print(f"  Состояние: Конверт {r.condition_sleeve} / Винил {r.condition_vinyl}")
        print(f"  Цена в РФ: {r.price_rub_median} ₽ (Коридор: {r.price_rub_min} - {r.price_rub_max} ₽)")
        print(f"  Ликвидность: {r.liquidity_stars} {r.liquidity_text}")
        print(f"  Вердикт: {r.verdict}")
        print(f"  Фото: {r.photo_indices}")

        assert r.artist, "Исполнитель должен быть указан"
        assert r.album, "Альбом должен быть указан"
        assert r.verdict, "Вердикт должен быть сформирован"
        assert isinstance(r.price_rub_median, int), "Медиана цены должна быть числом"

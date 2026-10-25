import urllib.request
import urllib.parse
import json
import re

AITUNNEL_API_KEY = "sk-aitunnel-qeuouwWXHwYtJmmartJ6xlCEvSbAEBaG"
DEFAULT_MODEL = "gemini-3.7-flash"

def call_llm(prompt: str, model: str = DEFAULT_MODEL, temperature: float = 0.2) -> str:
    """Отправляет запрос к LLM через шлюз aitunnel."""
    data = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.aitunnel.ru/v1/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {AITUNNEL_API_KEY}",
            "Content-Type": "application/json"
        }
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        res_json = json.loads(resp.read().decode())
        content = res_json["choices"][0]["message"]["content"]
        # Очистка markdown блоков json при необходимости
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
        return content.strip()

def search_discogs_release(query: str, catno: str = "", matrix: str = "") -> dict:
    """Ищет релиз в базе Discogs и достает официальные данные пресса."""
    search_terms = f"{query} {catno} {matrix}".strip()
    url = f"https://api.discogs.com/database/search?q={urllib.parse.quote(search_terms)}&type=release"
    req = urllib.request.Request(url, headers={"User-Agent": "VinylAssistantBot/2.0"})
    
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            results = data.get("results", [])
            if not results:
                return {}
            
            top_result = results[0]
            rel_id = top_result.get("id")
            
            # Получаем подробную карточку релиза
            rel_url = f"https://api.discogs.com/releases/{rel_id}"
            rel_req = urllib.request.Request(rel_url, headers={"User-Agent": "VinylAssistantBot/2.0"})
            with urllib.request.urlopen(rel_req, timeout=10) as rel_resp:
                details = json.loads(rel_resp.read().decode())
                
            matrix_list = [
                i.get("value") for i in details.get("identifiers", [])
                if i.get("type") == "Matrix / Runout"
            ]
            
            return {
                "discogs_id": rel_id,
                "discogs_url": f"https://www.discogs.com/release/{rel_id}",
                "title": details.get("title"),
                "year": details.get("year"),
                "country": details.get("country"),
                "notes": details.get("notes", ""),
                "lowest_price_eur": details.get("lowest_price"),
                "num_for_sale": details.get("num_for_sale"),
                "official_matrices": matrix_list[:6],
                "formats": [f.get("descriptions", []) for f in details.get("formats", [])]
            }
    except Exception as e:
        return {"error": f"Discogs lookup failed: {str(e)}"}

def analyze_vinyl_listing(title: str, description: str, price_str: str) -> dict:
    """Полный пайплайн анализа винила: парсинг -> Discogs Grounding -> экспертный вердикт меломану."""
    print(" 1. Извлечение параметров объявления с помощью Gemini...")
    extract_prompt = f"""Извлеки данные из объявления о продаже виниловой пластинки:
Заголовок: {title}
Описание: {description}
Цена: {price_str}

Ответь строго валидным JSON без пояснений со следующими полями:
{{
  "artist": "имя исполнителя",
  "album": "название альбома",
  "year_claimed": "год из объявления",
  "catno": "каталожный номер если есть (например, TPSA 7504)",
  "matrix": "номер матрицы со сбега если указан",
  "label": "лейбл звукозаписи",
  "grade_vinyl": "заявленное состояние пластинки (например EX, VG+)",
  "grade_sleeve": "заявленное состояние конверта",
  "price_rub": 3500
}}"""

    extracted_raw = call_llm(extract_prompt)
    try:
        extracted = json.loads(extracted_raw)
    except Exception:
        extracted = {"artist": title, "album": "", "matrix": "", "catno": "", "price_rub": 0}

    print(f"    Артист: {extracted.get('artist')}, Альбом: {extracted.get('album')}")
    print(f"    Матрицы/Каталог: {extracted.get('catno')} / {extracted.get('matrix')}")

    print(" 2. Верификация через мировую базу Discogs...")
    discogs_data = search_discogs_release(
        f"{extracted.get('artist', '')} {extracted.get('album', '')}",
        catno=extracted.get("catno", ""),
        matrix=extracted.get("matrix", "")
    )

    if discogs_data and "discogs_id" in discogs_data:
        print(f"    Найден релиз в Discogs: #{discogs_data['discogs_id']} ({discogs_data['country']} {discogs_data['year']})")
    else:
        print("    Точное совпадение в Discogs не найдено, анализ по общим критериям.")

    print(" 3. Экспертная оценка с точки зрения меломана и безопасности покупки...")
    expert_prompt = f"""Ты — опытный консультант по винилу и звуку. 
Твой клиент — обычный меломан, который недавно купил свой первый виниловый проигрыватель. Он хочет слушать качественную музыку без переплат, треска и разочарований, и не разбирается в тонкостях прессов.

ДАННЫЕ ОБЪЯВЛЕНИЯ:
- Заголовок: {title}
- Описание продавца: {description}
- Цена: {price_str}
- Извлеченные атрибуты: {json.dumps(extracted, ensure_ascii=False)}

ОФИЦИАЛЬНЫЕ ДАННЫЕ ИЗ БАЗЫ DISCOGS ДЛЯ ЭТОГО РЕЛИЗА:
{json.dumps(discogs_data, ensure_ascii=False, indent=2)}

СФОРМИРУЙ ОБЪЕКТИВНЫЙ И ЧЕСТНЫЙ АНАЛИЗ ДЛЯ МЕЛОМАНА В ФОРМАТЕ JSON СО СТРУКТУРОЙ:
{{
  "is_authentic_claim": true/false (соответствуют ли слова продавца реальности по базе Discogs),
  "authenticity_details": "пояснение: действительно ли это оригинал/первопресс по матрице и лейблу",
  "missing_bundle_items": ["список вещей, которые шли с оригиналом, но не упомянуты продавцом (например, плакат, оригинальный иннер с патентом)"],
  "sound_playback_risks": "какие риски по звуку на проигрывателе новичка несет заявленный грейд (песок, щелчки, износ дорожек на громких местах)",
  "price_evaluation": "оценка цены {price_str} (дешево/рынок/дорого относительно состояния и отсутствия комплектных вещей)",
  "beginner_verdict": "BUY / BARGAIN / ASK_QUESTIONS / AVOID",
  "beginner_advice": "понятный совет человеческим языком: стоит ли брать новичку, или лучше купить современный ровный ремастер 180g без щелчков",
  "questions_to_ask_seller": [
    "вопрос 1: конкретная деталь (например, фото матрицы на сбеге)",
    "вопрос 2: проверка звука / мойка",
    "вопрос 3: геометрия пластинки (восьмерка) и упаковка при доставке"
  ]
}}
Ответь ТОЛЬКО валидным JSON без markdown разметки."""

    expert_raw = call_llm(expert_prompt)
    try:
        verdict = json.loads(expert_raw)
    except Exception:
        verdict = {"raw_response": expert_raw}

    return {
        "listing": extracted,
        "discogs_verification": discogs_data,
        "expert_analysis": verdict
    }

if __name__ == "__main__":
    sample_title = "Deep Purple - Machine Head (1972) UK Original"
    sample_desc = "Продаю оригинальный английский первопресс 1972 года, Purple Records, матрица Tpsa 7504 A-1U / B-1U. Состояние пластинки отличное, легкие поверхностные следы (EX), конверт с разворотом целый, углы целые (EX)."
    sample_price = "3500 руб."

    result = analyze_vinyl_listing(sample_title, sample_desc, sample_price)
    print("\n" + "="*60)
    print("ИТОГОВЫЙ РЕЗУЛЬТАТ АНАЛИЗА:")
    print("="*60)
    print(json.dumps(result, ensure_ascii=False, indent=2))

from typing import List, Optional
from pydantic import BaseModel, Field

class VinylRelease(BaseModel):
    artist: str = Field(default="Не определен", description="Исполнитель / Группа")
    album: str = Field(default="Не определен", description="Название альбома")
    label: str = Field(default="Не указан", description="Лейбл звукозаписи (например EMI, Мелодия)")
    year: str = Field(default="Не указан", description="Год издания или оригинальной записи")
    catno: str = Field(default="Не указан", description="Каталожный номер релиза")
    matrix: str = Field(default="Не видна", description="Номер матрицы со сбега (runout), если читаем")
    condition_sleeve: str = Field(default="Не оценен", description="Грейд конверта (NM, EX, VG+, VG, G)")
    condition_vinyl: str = Field(default="Не оценен", description="Грейд винила (NM, EX, VG+, VG, G)")
    price_rub_median: int = Field(default=0, description="Медианная цена в РФ в рублях")
    price_rub_min: int = Field(default=0, description="Нижняя граница цены в рублях")
    price_rub_max: int = Field(default=0, description="Верхняя граница цены в рублях")
    liquidity_stars: str = Field(default="⭐⭐⭐", description="Звездочный рейтинг ликвидности от ⭐ до ⭐⭐⭐⭐⭐")
    liquidity_text: str = Field(default="Средняя", description="Текстовая оценка ликвидности и спроса")
    verdict: str = Field(default="", description="Экспертный вердикт: брать/не брать, нюансы тиража")
    photo_indices: List[int] = Field(default_factory=list, description="Номера входящих фото, относящихся к этой пластинке (1..N)")
    tracklist: List[str] = Field(default_factory=list, description="Список распознанных треков/песен с конверта или яблока")
    discogs_id: Optional[str] = Field(default=None, description="ID релиза в базе Discogs")
    discogs_url: Optional[str] = Field(default=None, description="Прямая ссылка на релиз на Discogs.com")
    discogs_lowest_eur: Optional[float] = Field(default=None, description="Минимальная мировая цена в EUR на Discogs")

    @property
    def short_title(self) -> str:
        title = f"{self.artist} - {self.album}".strip(" -")
        return title if title else "Пластинка"

class AnalysisResponse(BaseModel):
    releases: List[VinylRelease] = Field(default_factory=list)

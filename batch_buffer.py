import asyncio
import logging
from typing import Callable, Coroutine, Dict, List, Any, Optional

logger = logging.getLogger(__name__)

class PhotoBatchBuffer:
    """
    Асинхронный буфер тишины (Debounce) для Telegram-фотографий.
    Собирает поток отдельных сообщений (альбомы, серии фото) в единый пакет
    и вызывает обработчик строго один раз после истечения окна тишины.
    """
    def __init__(self, delay: float = 2.8, callback: Optional[Callable[[int, List[Any]], Coroutine[Any, Any, None]]] = None):
        self.delay = delay
        self.callback = callback
        self._buffers: Dict[int, List[Any]] = {}
        self._timers: Dict[int, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    def set_callback(self, callback: Callable[[int, List[Any]], Coroutine[Any, Any, None]]):
        self.callback = callback

    async def add_item(self, chat_id: int, item: Any):
        """
        Добавляет фото в буфер чата и сбрасывает/перезапускает таймер тишины.
        """
        async with self._lock:
            if chat_id not in self._buffers:
                self._buffers[chat_id] = []
            self._buffers[chat_id].append(item)

            # Отменяем предыдущий таймер, если он был активен
            if chat_id in self._timers:
                old_task = self._timers[chat_id]
                if not old_task.done():
                    old_task.cancel()

            # Запускаем новый таймер ожидания тишины
            self._timers[chat_id] = asyncio.create_task(self._debounce_timer(chat_id))

    async def _debounce_timer(self, chat_id: int):
        try:
            await asyncio.sleep(self.delay)
            # Если таймер не был отменен, извлекаем пачку
            await self._flush(chat_id)
        except asyncio.CancelledError:
            # Таймер был сброшен новым входящим фото
            pass
        except Exception as e:
            logger.error(f"Ошибка в таймере буфера чата {chat_id}: {e}", exc_info=True)

    async def _flush(self, chat_id: int):
        items = []
        async with self._lock:
            items = self._buffers.pop(chat_id, [])
            self._timers.pop(chat_id, None)

        if items and self.callback:
            try:
                await self.callback(chat_id, items)
            except Exception as e:
                logger.error(f"Ошибка при обработке пачки для чата {chat_id}: {e}", exc_info=True)

    async def flush_now(self, chat_id: int):
        """Принудительный сброс буфера (например, при завершении работы)."""
        async with self._lock:
            if chat_id in self._timers:
                self._timers[chat_id].cancel()
                self._timers.pop(chat_id, None)
        await self._flush(chat_id)

import asyncio
import pytest
from batch_buffer import PhotoBatchBuffer

@pytest.mark.asyncio
async def test_debounce_collects_8_photos_into_single_call():
    """
    Эмулирует отправку 8 сообщений с фото с интервалом 150 мс.
    Проверяет, что происходит ровно ОДИН вызов обработчика и в нем ровно 8 фото.
    """
    processed_batches = []

    async def mock_ai_handler(chat_id: int, items: list):
        processed_batches.append({
            "chat_id": chat_id,
            "count": len(items),
            "items": items
        })

    # Используем debounce окно 1.0 с для быстрого теста
    buffer = PhotoBatchBuffer(delay=1.0, callback=mock_ai_handler)
    test_chat_id = 123456789

    # Отправляем 8 фото с интервалом 150 мс
    for i in range(1, 9):
        await buffer.add_item(test_chat_id, f"photo_file_id_{i}")
        await asyncio.sleep(0.15)

    # В этот момент прошло 8 * 0.15 = 1.2 с с момента старта,
    # но с момента последнего 8-го фото прошло всего ~0 мс.
    # Обработчик еще НЕ должен сработать!
    assert len(processed_batches) == 0, "Обработчик не должен срабатывать до истечения окна тишины!"

    # Ждем истечения окна тишины (1.0 сек + 0.2 сек запас)
    await asyncio.sleep(1.2)

    # Проверяем, что сработал ровно 1 вызов со всеми 8 фото
    assert len(processed_batches) == 1, f"Ожидался ровно 1 вызов, но получено {len(processed_batches)}"
    assert processed_batches[0]["count"] == 8, f"Ожидалось 8 фото в пачке, получено {processed_batches[0]['count']}"
    assert processed_batches[0]["items"] == [f"photo_file_id_{i}" for i in range(1, 9)]

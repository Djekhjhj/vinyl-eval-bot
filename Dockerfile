FROM python:3.11-slim

# Отключение буферизации вывода Python для мгновенных логов в облаке
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Копируем только requirements для эффективного кэширования слоев Docker
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходный код
COPY . .

# Запуск автономного бота в режиме Long Polling
CMD ["python", "bot.py"]

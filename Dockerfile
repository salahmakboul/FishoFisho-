FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    libjpeg-dev \
    zlib1g-dev \
    libfreetype6-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Use bash to run commands with proper error handling
CMD bash -c "python manage.py migrate --noinput && exec gunicorn fishofisho.wsgi:application --bind 0.0.0.0:$PORT"

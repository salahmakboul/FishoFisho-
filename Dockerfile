FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
RUN python manage.py migrate --noinput
CMD ["sh", "-c", "gunicorn fishofisho.wsgi:application --bind 0.0.0.0:${PORT:-8000}"]

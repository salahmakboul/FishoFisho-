FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
# Run migrations at startup, not during build
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn fishofisho.wsgi:application --bind 0.0.0.0:${PORT:-8000}"]

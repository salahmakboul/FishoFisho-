FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Use Django dev server instead of Gunicorn for testing
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py runserver 0.0.0.0:${PORT:-8000}"]

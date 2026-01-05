FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
# Run migrations and start with error checking
CMD ["sh", "-c", "
echo === STARTING ===
python manage.py migrate --noinput || echo Migration failed with exit code: $?
echo === CHECKING DATABASE ===
python -c \"
import os
print(
DATABASE_URL
in
env:, DATABASE_URL in os.environ)
if DATABASE_URL not in os.environ:
    print(ERROR:
DATABASE_URL
missing!)
    exit(1)
\"
echo === STARTING GUNICORN ===
gunicorn fishofisho.wsgi:application --bind 0.0.0.0:${PORT:-8000} --access-logfile -
"]

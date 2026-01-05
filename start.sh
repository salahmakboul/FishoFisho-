#!/bin/bash
# Get port from Railway environment or use default
PORT=${PORT:-8000}

echo "Starting Gunicorn on port: $PORT"

# Run migrations
python manage.py migrate --noinput

# Start Gunicorn
gunicorn fishofisho.wsgi:application --bind 0.0.0.0:$PORT

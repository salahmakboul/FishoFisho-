#!/bin/bash
# Debug
echo "=== STARTUP SCRIPT ==="

# Test database
python test_db.py

# Run migrations
echo "Running migrations..."
python manage.py migrate --noinput

# Start Gunicorn
echo "Starting Gunicorn..."
exec gunicorn fishofisho.wsgi:application --bind 0.0.0.0:${PORT:-8000}

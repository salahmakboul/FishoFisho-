#!/bin/bash
# Debug
echo "=== STARTUP SCRIPT ==="

# Test database
python test_db.py

# Run migrations
echo "Running migrations..."
python manage.py migrate --noinput

# Test Django
echo "Testing Django setup..."
python -c "
import django
django.setup()
from django.conf import settings
print(f
Django
apps:
len(settings.INSTALLED_APPS)
)
print(f
Middleware:
len(settings.MIDDLEWARE)
)
"

# Start Gunicorn
echo "Starting Gunicorn..."
exec gunicorn fishofisho.wsgi:application --bind 0.0.0.0:${PORT:-8000}

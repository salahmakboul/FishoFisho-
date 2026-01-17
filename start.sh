#!/bin/bash
echo "=== STARTUP SCRIPT ==="

# Create or reset superuser
echo "Setting up superuser..."
python -c "
import os
os.environ.setdefault(\"DJANGO_SETTINGS_MODULE\", \"fishofisho.settings\")
import django
django.setup()
from django.contrib.auth import get_user_model
User = get_user_model()
try:
    user = User.objects.get(username=\"admin\")
    user.set_password(\"Admin123!\")
    user.is_staff = True
    user.is_superuser = True
    user.save()
    print(\"✓ Admin password reset\")
except User.DoesNotExist:
    User.objects.create_superuser(\"admin\", \"admin@example.com\", \"Admin123!\")
    print(\"✓ Superuser created: admin / Admin123!\")
"

# Run migrations
python manage.py migrate --noinput

# Start Gunicorn
echo "Starting Gunicorn on port: \${PORT}"
exec gunicorn fishofisho.wsgi:application --bind 0.0.0.0:\${PORT} --workers 1 --timeout 120

#!/bin/bash
echo "=== STARTUP SCRIPT ==="

# Run migrations
python manage.py migrate --noinput

# Reset salah password on Railway
echo "Resetting salah password..."
python -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'fishofisho.settings')
import django
django.setup()
from django.contrib.auth import get_user_model
User = get_user_model()
try:
    user = User.objects.get(username='salah')
    user.set_password('Salah123!')
    user.save()
    print('✓ Password reset for salah: Salah123!')
except Exception as e:
    print(f'Error: {e}')
"

# Start Gunicorn
echo "Starting Gunicorn on port: ${PORT}"
exec gunicorn fishofisho.wsgi:application --bind 0.0.0.0:${PORT} --workers 1 --timeout 120

#!/bin/bash
echo "=== STARTUP SCRIPT ==="

# Run migrations
python manage.py migrate --noinput

# Fix salah user permissions
echo "Fixing user permissions..."
python -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'fishofisho.settings')
import django
django.setup()
from django.contrib.auth import get_user_model
User = get_user_model()

# Fix salah
try:
    user = User.objects.get(username='salah')
    user.set_password('Salah123!')
    user.is_staff = True
    user.is_superuser = True
    user.is_active = True
    user.save()
    print('✓ Fixed salah: is_staff=True, is_superuser=True, password=Salah123!')
except Exception as e:
    print(f'Error with salah: {e}')

# Also ensure other superusers
for username in ['makboul', 'kbn']:
    try:
        user = User.objects.get(username=username)
        user.is_staff = True
        user.is_superuser = True
        user.save()
        print(f'✓ Fixed {username} permissions')
    except:
        pass
"

# Start Gunicorn
echo "Starting Gunicorn on port: ${PORT}"
exec gunicorn fishofisho.wsgi:application --bind 0.0.0.0:${PORT} --workers 1 --timeout 120

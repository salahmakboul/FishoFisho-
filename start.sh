#!/bin/bash
echo "=== STARTUP SCRIPT ==="

# Run migrations
python manage.py migrate --noinput

# Fix users on Railway
echo "Setting up admin users..."
python -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'fishofisho.settings')
import django
django.setup()
from django.contrib.auth import get_user_model
User = get_user_model()

# 1. Create fresh railwayadmin
try:
    User.objects.filter(username='railwayadmin').delete()
    User.objects.create_superuser('railwayadmin', 'admin@railway.com', 'RailwayAdmin123!')
    print('✓ Created: railwayadmin / RailwayAdmin123!')
except Exception as e:
    print(f'Error creating railwayadmin: {e}')

# 2. Also fix salah
try:
    user = User.objects.get(username='salah')
    user.set_password('Salah123!')
    user.is_staff = True
    user.is_superuser = True
    user.is_active = True
    user.save()
    print('✓ Fixed: salah / Salah123!')
except Exception as e:
    print(f'Error fixing salah: {e}')
"

# Start Gunicorn
echo "Starting Gunicorn on port: ${PORT}"
exec gunicorn fishofisho.wsgi:application --bind 0.0.0.0:${PORT} --workers 1 --timeout 120

import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'fishofisho.settings')
django.setup()

from django.conf import settings

print("Checking deployment settings...")
print(f"DEBUG: {settings.DEBUG}")
print(f"ALLOWED_HOSTS: {settings.ALLOWED_HOSTS}")
print(f"Database ENGINE: {settings.DATABASES['default']['ENGINE']}")
print(f"Static ROOT: {settings.STATIC_ROOT}")
print("✅ Settings look good for Railway!" if '.railway.app' in settings.ALLOWED_HOSTS else "❌ Need to fix ALLOWED_HOSTS")
import os
from pathlib import Path
import sys
import dj_database_url
from dotenv import load_dotenv

# Load .env
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# Security
SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-dev-key')
DEBUG = os.environ.get('DEBUG', 'False').lower() == 'true'

# Get Railway domain
RAILWAY_DOMAIN = os.environ.get('RAILWAY_STATIC_URL', 'fishofisho-production.up.railway.app')

# ALLOWED_HOSTS - include both patterns
ALLOWED_HOSTS = ['localhost', '127.0.0.1', '.railway.app', RAILWAY_DOMAIN]

# Remove duplicate if exists
ALLOWED_HOSTS = list(set(ALLOWED_HOSTS))

# CSRF
CSRF_TRUSTED_ORIGINS = [f'https://{RAILWAY_DOMAIN}', 'https://*.railway.app']

print(f"ALLOWED_HOSTS: {ALLOWED_HOSTS}")
print(f"CSRF_TRUSTED_ORIGINS: {CSRF_TRUSTED_ORIGINS}")

# ... rest of your settings.py continues ...

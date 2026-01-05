"""
Django settings for fishofisho project.
FIXED for Railway deployment - NO redirect loops
"""

import os
from pathlib import Path
import dj_database_url
from dotenv import load_dotenv

# ========== LOAD ENVIRONMENT ==========
load_dotenv()

# ========== BASE CONFIGURATION ==========
BASE_DIR = Path(__file__).resolve().parent.parent

# ========== SECURITY ==========
SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-change-this-in-production')
DEBUG = True
# ========== HOST CONFIGURATION ==========
# FIX: Add exact domain to prevent redirects
ALLOWED_HOSTS = [
    'localhost', 
    '127.0.0.1', 
    '.railway.app',
    'fishofisho-production.up.railway.app',  # ADDED: Your exact domain
    'fishofisho-production.railway.app',     # ADDED: Alternative domain
]

# FIX: Always set CSRF_TRUSTED_ORIGINS (not conditional)
CSRF_TRUSTED_ORIGINS = [
    'https://*.railway.app',
    'https://fishofisho-production.up.railway.app',
    'https://fishofisho-production.railway.app',
]

# ========== DATABASE ==========
# Railway provides DATABASE_URL, fallback to SQLite locally
DATABASE_URL = os.environ.get('DATABASE_URL')

if DATABASE_URL:
    # PostgreSQL on Railway
    DATABASES = {
        'default': dj_database_url.parse(DATABASE_URL, conn_max_age=600)
    }
else:
    # SQLite for local development
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# ========== APPLICATION DEFINITION ==========
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'playground',
]

if DEBUG:
    INSTALLED_APPS.append('debug_toolbar')

# ========== MIDDLEWARE ==========
# FIX: Simplified middleware - removed problematic ones
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',  # KEPT but with secure cookies disabled
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    # REMOVED: 'django.middleware.clickjacking.XFrameOptionsMiddleware', - causing issues
]

if DEBUG:
    MIDDLEWARE.insert(1, 'debug_toolbar.middleware.DebugToolbarMiddleware')  # FIXED position

INTERNAL_IPS = ['127.0.0.1']

# ========== URL & TEMPLATES ==========
ROOT_URLCONF = 'fishofisho.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'fishofisho.wsgi.application'

# ========== AUTHENTICATION ==========
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'home'

# ========== INTERNATIONALIZATION ==========
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# ========== STATIC FILES ==========
STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
STATICFILES_DIRS = [BASE_DIR / 'static']

# ========== MEDIA FILES ==========
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# ========== DEFAULT PRIMARY KEY ==========
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ========== AI CONFIGURATION ==========
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')

# ========== CRITICAL FIX: DISABLE SECURITY REDIRECTS ==========
# FIX: These were causing infinite redirects on Railway
SECURE_SSL_REDIRECT = False  # MUST BE FALSE for Railway
SESSION_COOKIE_SECURE = False  # Disable for now
CSRF_COOKIE_SECURE = False    # Disable for now

# Keep other security headers (they don't cause redirects)
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True

# DISABLE HSTS for now (can cause redirects)
SECURE_HSTS_SECONDS = 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False

# ========== RAILWAY SPECIFIC ==========
# Detect if running on Railway
IS_RAILWAY = 'RAILWAY' in os.environ or 'RAILWAY_STATIC_URL' in os.environ

if IS_RAILWAY:
    WHITENOISE_USE_FINDERS = True
    WHITENOISE_MANIFEST_STRICT = False
    # Force debug info
    print("🚂 RAILWAY ENVIRONMENT DETECTED")

# ========== DEBUG OUTPUT ==========
print("=" * 60)
print(f"✅ Django settings loaded")
print(f"🔐 DEBUG: {DEBUG}")
print(f"🌐 ALLOWED_HOSTS: {ALLOWED_HOSTS}")
print(f"🔒 SSL Redirect: {SECURE_SSL_REDIRECT} (MUST BE False)")
print(f"🗄️ Database: {DATABASES['default']['ENGINE']}")
print(f"🤖 Gemini API: {'✅ SET' if GEMINI_API_KEY else '❌ NOT SET'}")
print("=" * 60)
print(f"DATABASE_URL from env: {os.environ.get('DATABASE_URL', 'NOT FOUND')}")
# Debug database URL
print('=' * 60)
print(f'DATABASE_URL exists: {\
DATABASE_URL\ in os.environ}')
print(f'DATABASE_URL value: {os.environ.get(\
DATABASE_URL\, \NOT
SET\)[:50]}...' if os.environ.get(\DATABASE_URL\) else 'DATABASE_URL: NOT SET')
print('=' * 60)

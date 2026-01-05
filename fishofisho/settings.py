"""
Django settings for fishofisho project.
Production-ready for Railway deployment.
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
DEBUG = os.environ.get('DEBUG', 'False').lower() == 'true'

# ========== HOST CONFIGURATION ==========
ALLOWED_HOSTS = ['localhost', '127.0.0.1', '.railway.app']

# Add Railway domain if available
railway_domain = os.environ.get('RAILWAY_STATIC_URL')
if railway_domain:
    ALLOWED_HOSTS.append(railway_domain)
    CSRF_TRUSTED_ORIGINS = [f'https://{railway_domain}', 'https://*.railway.app']
else:
    CSRF_TRUSTED_ORIGINS = ['https://*.railway.app']

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
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

if DEBUG:
    MIDDLEWARE.insert(3, 'debug_toolbar.middleware.DebugToolbarMiddleware')

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

# ========== PRODUCTION SECURITY ==========
if not DEBUG:
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# ========== RAILWAY SPECIFIC ==========
if 'RAILWAY' in os.environ:
    WHITENOISE_USE_FINDERS = True
    WHITENOISE_MANIFEST_STRICT = False

# ========== DEBUG OUTPUT ==========
print("=" * 50)
print(f"✅ Django settings loaded")
print(f"🔐 DEBUG: {DEBUG}")
print(f"🌐 ALLOWED_HOSTS: {ALLOWED_HOSTS}")
print(f"🗄️ Database: {DATABASES['default']['ENGINE']}")
print(f"🤖 Gemini API: {'SET' if GEMINI_API_KEY else 'NOT SET'}")
print("=" * 50)
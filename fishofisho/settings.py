"""
Django settings for fishofisho project.
FIXED for Railway deployment - NO redirect loops
"""

import os
from pathlib import Path
import dj_database_url
from dotenv import load_dotenv
import logging

# ========== LOAD ENVIRONMENT ==========
load_dotenv()

# ========== BASE CONFIGURATION ==========
BASE_DIR = Path(__file__).resolve().parent.parent

# ========== SECURITY ==========
SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-change-this-in-production')

# DEBUG now actually comes from the environment (local .env has DEBUG=True;
# Railway/production must NOT set that var, so this defaults to False there)
# instead of being hardcoded — it was previously hardcoded True here AND
# forced True again at the bottom of this file, so neither this var nor a
# real env override could ever actually turn it off in production. Fixed.
DEBUG = os.environ.get('DEBUG', 'False') == 'True'

if not DEBUG and SECRET_KEY == 'django-insecure-change-this-in-production':
    raise RuntimeError(
        "SECRET_KEY env var is not set. Refusing to run with the insecure "
        "default outside DEBUG — set SECRET_KEY in the deployment environment."
    )

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
    # 'daphne' must be first: it provides the `runserver` command override
    # that makes `manage.py runserver` actually serve ASGI_APPLICATION
    # (so WebSockets work) instead of falling back to Django's plain WSGI
    # dev server. Channels 3+/4 no longer ships this override itself (it
    # used to) — without daphne here, `channels` being installed silently
    # does nothing for `runserver`, and only the deployed ASGI server would
    # ever really speak WebSocket. See:
    # https://channels.readthedocs.io/en/latest/installation.html
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'channels',  # must precede django.contrib.staticfiles per Channels docs
    'django.contrib.staticfiles',
    'rest_framework',
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
    # Previously removed with a "causing issues" comment — it wasn't actually
    # related to the SSL-redirect-loop problem (that was SECURE_SSL_REDIRECT,
    # now properly fixed above via SECURE_PROXY_SSL_HEADER). This just adds
    # an X-Frame-Options header to block clickjacking; safe to restore.
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
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

# ========== CHANNELS (WebSocket / ASGI) ==========
ASGI_APPLICATION = 'fishofisho.asgi.application'

# Local dev channel layer: in-process, single-process only, no external deps.
# PRODUCTION TODO: swap for channels_redis.core.RedisChannelLayer (requires
# the `channels_redis` package + a Redis instance) so the layer works across
# multiple worker processes/machines. Not added now to keep deps minimal.
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    },
}

# ========== DJANGO REST FRAMEWORK ==========
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    # Points at our own IsAuthenticatedNotBanned (playground/permissions.py),
    # not DRF's plain IsAuthenticated, as a defense-in-depth default for any
    # view that doesn't set its own permission_classes — every view in
    # api_views.py/admin_views.py already imports
    # "IsAuthenticatedNotBanned as IsAuthenticated" and lists it explicitly,
    # so this default rarely actually fires, but it means a future view that
    # forgets to set permission_classes still gets the ban check rather than
    # silently falling back to plain DRF IsAuthenticated.
    'DEFAULT_PERMISSION_CLASSES': [
        'playground.permissions.IsAuthenticatedNotBanned',
    ],
    # ---- rate limiting (admin/moderation slice) ----
    # UserRateThrottle applies to every authenticated view at the general
    # 'user' rate below. ScopedRateThrottle is a no-op for any view that
    # doesn't set `throttle_scope` — it only kicks in for the views that
    # opt into a named scope (MessageListCreateView/PrivateMessageListCreateView
    # both set throttle_scope = "messages", see api_views.py), so message
    # creation ends up covered by BOTH throttles at once (whichever trips
    # first wins). Judgment call: ScopedRateThrottle also throttles those
    # views' GET/list requests, not just POST — DRF scopes per-view, not
    # per-method, so a stricter read rate on those two endpoints is an
    # accepted side effect of the simpler per-view scoping the task asked
    # for, rather than writing a custom per-method throttle class.
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.UserRateThrottle',
        'rest_framework.throttling.ScopedRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'user': '120/min',
        'messages': '30/min',
    },
}

# ========== AUTHENTICATION ==========
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# NOTE: no LOGIN_URL/LOGIN_REDIRECT_URL — the old server-rendered 'login'
# URL name is gone (auth is now the React app calling /api/v1/auth/*), and
# nothing in this app uses @login_required/LoginRequiredMixin that would
# need these. Django admin's own login view is unaffected by this setting.

# ========== EMAIL (password reset "sending") ==========
# No SMTP/transactional-email provider is configured yet, so password-reset
# emails go to the console backend: Django just prints the message (subject,
# body, headers) to stdout/the server log instead of actually sending it.
# Good enough for now since this app doesn't even collect a real email
# address per-user yet (see PasswordResetRequestView). Swapping in real
# SMTP later is a one-line change, e.g.:
#   EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
#   EMAIL_HOST = ...; EMAIL_PORT = ...; EMAIL_HOST_USER = ...; etc.
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# Base URL of the frontend SPA, used to build the password-reset link that
# gets "emailed" (see EMAIL_BACKEND above) — e.g.
# f"{FRONTEND_URL}/reset-password?uid=...&token=...". Overridable via env
# for deployed environments; defaults to the local Vite dev server.
FRONTEND_URL = os.environ.get('FRONTEND_URL', 'http://localhost:5173')

# ========== SESSION EXPIRY ==========
# Explicit rather than relying on Django's implicit default (which happens
# to be the same 2 weeks) — makes the policy visible here instead of buried
# in framework defaults.
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14  # 2 weeks
# Sliding expiry: every authenticated request refreshes the session's
# expiry clock (rather than it counting down from login time on a fixed
# schedule), so a chat app people leave open in a tab all day doesn't get
# logged out from under them mid-session. See LoginView for the
# "remember me" opt-out (browser-session-only cookies).
SESSION_SAVE_EVERY_REQUEST = True

# ========== INTERNATIONALIZATION ==========
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# ========== STATIC FILES ==========
STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
# frontend/dist is the built React app (see frontend/vite.config.ts, base="/static/"
# for production builds) — its assets/ dir is served alongside the legacy static/ dir.
STATICFILES_DIRS = [BASE_DIR / 'static', BASE_DIR / 'frontend' / 'dist']

# ========== MEDIA FILES ==========
# MEDIA_ROOT/MEDIA_URL cover only "plainly public" media now: avatars.
# Avatars are shown to any authenticated user in the member directory with
# no per-room privacy concern, so they stay on the simple statically-served
# path (see fishofisho/urls.py / playground/urls.py).
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# Message attachments are DIFFERENT: they can belong to messages in private
# rooms, so "anyone with the URL" must not be enough to fetch one (that was
# exactly the gap this ATTACHMENTS_ROOT/signed-URL setup closes). Attachment
# files are stored here, OUTSIDE MEDIA_ROOT, and are therefore not reachable
# through the blanket static(MEDIA_URL, ...) serving at all — the only way
# to read one is playground.api_views.AttachmentDownloadView, which requires
# session auth AND a signed, time-limited token (see playground/signing.py)
# AND re-checks the requesting user's membership in the attachment's room
# (user_can_access_room) before streaming the file. See MessageAttachment's
# `file` field (playground/models.py) for the FileSystemStorage pointed at
# this directory.
ATTACHMENTS_ROOT = os.path.join(BASE_DIR, 'protected_media', 'attachments')

# PRODUCTION TODO: this whole signed-URL setup is deliberately built on
# Django's local-filesystem storage + django.core.signing (stdlib, zero new
# deps) so it works out of the box wherever the app runs today. It mirrors
# the same interface S3 pre-signed URLs provide (a time-limited, tamper-
# proof, permission-scoped download link) — swapping the backing storage to
# django-storages' S3Boto3Storage later is a natural evolution of this same
# shape, not a rewrite: MessageAttachment.file's storage= becomes an S3
# storage instance, and AttachmentDownloadView's manual signing can either
# stay (S3 object stays private, Django still gatekeeps + streams/redirects)
# or be replaced by storage.url()'s own native pre-signed URLs once a real
# bucket + credentials exist. Not done now because no S3/GCS credentials are
# configured or available in this project (same reasoning as EMAIL_BACKEND
# and CHANNEL_LAYERS above — minimal deps until the real infra exists).

# ========== WEBHOOKS (integrations slice) ==========
# Outgoing webhook delivery (playground/signals.py's dispatch_outgoing_webhooks)
# fires on a plain `threading.Thread(daemon=True)` per delivery, not a real
# task queue — same "minimal deps until the real infra exists" reasoning as
# EMAIL_BACKEND/CHANNEL_LAYERS above. No Celery/RQ (or a broker like Redis/
# RabbitMQ for one) is configured anywhere in this project yet, so adding
# one just for webhook delivery would be new infrastructure out of scope
# for this slice. A background thread is enough to not block the request
# and to not leak resources on a dead endpoint (bounded by
# WEBHOOK_TIMEOUT_SECONDS), but it does NOT retry failed deliveries and
# in-flight deliveries can be lost on process shutdown. Celery or RQ (with
# a durable broker) is the natural production upgrade path once a real
# integration needs at-least-once delivery guarantees with retry/backoff.

# ========== DEFAULT PRIMARY KEY ==========
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ========== AI CONFIGURATION ==========
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')

# ========== HTTPS / SECURE COOKIES ==========
# Railway (like most PaaS) terminates TLS at its own proxy and forwards the
# request to this app as plain HTTP — without telling Django that, enabling
# SECURE_SSL_REDIRECT causes an infinite redirect loop (Django sees "http",
# redirects to "https", proxy forwards as "http" again...). That's why this
# was previously just hardcoded off entirely. The actual fix is
# SECURE_PROXY_SSL_HEADER: Railway's proxy sets X-Forwarded-Proto, so this
# tells Django to trust that header as the real scheme, which makes the
# redirect logic (and request.is_secure()) correct instead of just disabled.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# All of these are now DEBUG-derived instead of hardcoded: relaxed for local
# HTTP dev, properly secure in production. Cookies must never be sent over
# plain HTTP in production, and SSL redirect is now safe to enable given the
# proxy header fix above.
SECURE_SSL_REDIRECT = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True

# HSTS: only meaningful (and only safe to turn on) once SSL redirect is
# confirmed working, since a bad HSTS header can lock out a misconfigured
# domain for a long time. Starts at a conservative 1 day in production;
# raise once the deploy is confirmed stable, per Django's own HSTS rollout
# guidance (start short, increase gradually).
SECURE_HSTS_SECONDS = 86400 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = False

# ========== RAILWAY SPECIFIC ==========
# Detect if running on Railway
IS_RAILWAY = 'RAILWAY' in os.environ or 'RAILWAY_STATIC_URL' in os.environ

if IS_RAILWAY:
    WHITENOISE_USE_FINDERS = True
    WHITENOISE_MANIFEST_STRICT = False
    # Force debug info
    print("🚂 RAILWAY ENVIRONMENT DETECTED")

# ========== STARTUP OUTPUT ==========
# Only in DEBUG — a production log doesn't need a startup banner on every
# boot, and this used to run unconditionally.
if DEBUG:
    print("=" * 60)
    print("✅ Django settings loaded")
    print(f"🔐 DEBUG: {DEBUG}")
    print(f"🌐 ALLOWED_HOSTS: {ALLOWED_HOSTS}")
    print(f"🔒 SSL Redirect: {SECURE_SSL_REDIRECT}")
    print('DEBUG: DATABASE_URL exists:', 'DATABASE_URL' in os.environ)
    print('=' * 60)

# Real production misconfiguration check — this one should always run and
# actually fail loudly (not just print) so a missing DATABASE_URL on Railway
# is caught at boot instead of silently falling back to a throwaway local
# SQLite file that resets on every deploy.
if "RAILWAY" in os.environ and not os.environ.get("DATABASE_URL"):
    raise RuntimeError(
        "Running on Railway but DATABASE_URL is not set — check the "
        "Railway Variables tab. Refusing to silently fall back to SQLite "
        "in production."
    )



LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
        },
        "playground": {  # Your app name
            "handlers": ["console"],
            "level": "DEBUG" if DEBUG else "INFO",
        },
    },
}
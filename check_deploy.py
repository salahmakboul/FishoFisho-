import os
import sys
import django

# Add the project root to Python path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.append(project_root)

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'fishofisho.settings')

try:
    django.setup()
    from django.conf import settings
    
    print("=== Deployment Settings Check ===")
    print(f"DEBUG: {settings.DEBUG}")
    print(f"ALLOWED_HOSTS: {settings.ALLOWED_HOSTS}")
    print(f"Database ENGINE: {settings.DATABASES['default']['ENGINE']}")
    
    # Check static files
    if hasattr(settings, 'STATIC_ROOT'):
        print(f"STATIC_ROOT: {settings.STATIC_ROOT}")
    else:
        print("STATIC_ROOT: ❌ NOT SET")
    
    # Check for whitenoise
    middleware_list = getattr(settings, 'MIDDLEWARE', [])
    has_whitenoise = any('whitenoise' in str(mw) for mw in middleware_list)
    print(f"Has whitenoise: {has_whitenoise}")
    
    # Railway check
    has_railway = any('.railway.app' in str(host) for host in settings.ALLOWED_HOSTS)
    print(f"Railway ready: {has_railway}")
    
    print("\n✅ Check complete!" if has_railway else "\n❌ Fix ALLOWED_HOSTS for Railway!")
    
except Exception as e:
    print(f"❌ Error: {e}")
    print("\nTroubleshooting:")
    print("1. Check if Django is installed: pip install django")
    print("2. Verify fishofisho folder exists")

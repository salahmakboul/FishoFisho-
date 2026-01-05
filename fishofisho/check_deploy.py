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
    print(f"1. DEBUG mode: {settings.DEBUG}")
    print(f"2. ALLOWED_HOSTS: {settings.ALLOWED_HOSTS}")
    print(f"3. Database: {settings.DATABASES['default']['ENGINE']}")
    
    # Check for static files configuration
    if hasattr(settings, 'STATIC_ROOT'):
        print(f"4. Static files root: {settings.STATIC_ROOT}")
    else:
        print("4. Static files root: ❌ NOT SET")
    
    # Check for whitenoise middleware
    middleware_list = getattr(settings, 'MIDDLEWARE', [])
    has_whitenoise = any('whitenoise' in str(mw) for mw in middleware_list)
    print(f"5. Has whitenoise: {has_whitenoise}")
    
    # Railway-specific checks
    has_railway = any('.railway.app' in host for host in settings.ALLOWED_HOSTS)
    has_static = hasattr(settings, 'STATIC_ROOT') and settings.STATIC_ROOT
    
    print("\n=== Railway Readiness ===")
    print(f"{'✅' if has_railway else '❌'} Railway domains allowed")
    print(f"{'✅' if has_static else '❌'} Static files configured")
    print(f"{'✅' if has_whitenoise else '❌'} Whitenoise middleware")
    
    if all([has_railway, has_static, has_whitenoise]):
        print("\n🎉 All checks passed! Ready for Railway deployment.")
    else:
        print("\n⚠️  Fix the issues above before deploying.")
        
except Exception as e:
    print(f"❌ Error loading settings: {e}")
    print("\nMake sure:")
    print("1. You're in the correct directory (where manage.py is)")
    print("2. Django is installed: pip install django")
    print("3. Your project structure is correct")
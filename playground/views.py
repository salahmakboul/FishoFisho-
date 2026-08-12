from pathlib import Path

from django.conf import settings
from django.http import HttpResponse


def hello(request):
    try:
        # Simple response to test
        from django.http import HttpResponse

        return HttpResponse("HELLO VIEW WORKS!")
    except Exception as e:
        # Log error
        import logging

        logger = logging.getLogger(__name__)
        logger.error(f"Error in hello view: {e}")
        raise


# ---- New React frontend shell ----
#
# The old server-rendered chat UI (home/room/profile/notifications/inbox/
# private-chat/etc.) has been retired in favor of the React SPA built at
# frontend/dist (see frontend/). This view serves that build's index.html
# raw for the app's authenticated area (mounted at "/" and as a catch-all
# for any other now-removed path in playground/urls.py), so both direct
# navigation and browser refreshes on client-side routes work. Auth itself
# (login/register/logout) is now handled entirely by the session-authenticated
# /api/v1/auth/ endpoints (see api_views.py) called from the React app; the
# old server-rendered login_register.html page and its views are gone.
_FRONTEND_INDEX = Path(settings.BASE_DIR) / "frontend" / "dist" / "index.html"


def spa_index(request):
    try:
        html = _FRONTEND_INDEX.read_text(encoding="utf-8")
    except FileNotFoundError:
        return HttpResponse(
            "Frontend build not found. Run `npm run build` in frontend/ to "
            "generate frontend/dist/.",
            status=500,
        )
    return HttpResponse(html, content_type="text/html")

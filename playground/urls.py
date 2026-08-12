import re

from django.urls import include, path, re_path
from . import views
from django.conf import settings
from django.views.static import serve as serve_static_file

urlpatterns = [
    path('api/v1/', include('playground.api_urls')),
    path('hello/', views.hello),
    path('', views.spa_index, name="home"),
]

# MEDIA_ROOT now holds ONLY avatars (message attachments were moved to
# ATTACHMENTS_ROOT, a separate directory that nothing here serves — see the
# ATTACHMENTS_ROOT comment in fishofisho/settings.py and
# AttachmentDownloadView in api_views.py for why: attachments can belong to
# private-room messages and need a real permission check per request, which
# a static file server can never provide). Avatars have no such privacy
# tier — they're shown to any authenticated user in the member directory —
# so serving them as plain files is fine.
#
# This used to be `if settings.DEBUG: urlpatterns += static(MEDIA_URL, ...)`.
# django.conf.urls.static.static() is Django's documented dev-only helper —
# it internally no-ops (returns []) whenever settings.DEBUG is False, so
# that line served nothing at all in production: avatars 404'd there with
# no media serving whatsoever. Fixed by wiring django.views.static.serve
# directly and unconditionally (not gated on DEBUG, and not going through
# static()'s DEBUG check either) instead of going through WhiteNoise:
# WhiteNoise (see STATICFILES_STORAGE below) is built around
# `collectstatic`'s immutable, hashed, build-time file set — it's the right
# tool for STATIC_ROOT, but avatars are mutable, uploaded after deploy, so
# there's no "recollect and redeploy" moment to hand them to WhiteNoise's
# manifest. django.views.static.serve is documented as unsuitable for
# high-traffic production use, but for this app's scale (avatars only, low
# volume, no room-privacy concern) an honest, simple Django view beats
# "worked in DEBUG, silently broken everywhere else."
urlpatterns += [
    re_path(
        r"^%s(?P<path>.*)$" % re.escape(settings.MEDIA_URL.lstrip("/")),
        serve_static_file,
        {"document_root": settings.MEDIA_ROOT},
    ),
]

# Catch-all so the React Router (client-side) app can own any other path —
# both direct navigation to a deep link and a browser refresh on one need
# to still get the SPA shell back. Must stay LAST: admin/api/ws/media all
# need to keep matching their own patterns first. Auth (login/register/
# logout) now lives entirely under api/v1/auth/, not as separate top-level
# paths, so the SPA shell can own /login and /register client-side too.
urlpatterns += [
    re_path(r'^.*$', views.spa_index, name='spa-catchall'),
]

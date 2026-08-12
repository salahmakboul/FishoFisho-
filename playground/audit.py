"""Small helper for writing AuditEvent rows (see models.py's AuditEvent
docstring). Kept as its own module rather than folded into permissions.py
since it's a write-side concern (creating a row), not an authorization
check — permissions.py stays the "may this user do X" vocabulary, this is
the "record that X happened" vocabulary.
"""

from .models import AuditEvent


def log_audit_event(actor, action, *, target_user=None, target_room=None, target_message=None, detail=""):
    """Create one AuditEvent row. `actor` may be None (or an anonymous
    user) defensively, though every call site today has a real
    authenticated actor — an admin-gated view always has one by the time it
    reaches this call.
    """
    if actor is not None and not getattr(actor, "is_authenticated", True):
        actor = None
    AuditEvent.objects.create(
        actor=actor,
        action=action,
        target_user=target_user,
        target_room=target_room,
        target_message=target_message,
        detail=detail[:255],
    )

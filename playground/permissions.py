from django.db.models import Q
from django.utils import timezone
from rest_framework.permissions import BasePermission

from .models import RoomMembership, UserProfile


def user_role(user):
    """Best-effort role lookup; UserProfile is created via signal on User
    creation but stay defensive in case it's missing (mirrors the pattern
    already used for avatar lookups in serializers.py)."""
    profile = getattr(user, "userprofile", None)
    if profile is None:
        return UserProfile.ROLE_MEMBER
    return profile.role


def is_guest(user):
    """True if `user`'s workspace-wide UserProfile.role is "guest". Guests
    are a restricted role (Slack-style): they can see/participate in rooms
    they've been explicitly added to, but cannot create rooms, cannot be
    promoted to a room's admin, and don't get the "any public room is
    freely joinable" treatment members/admins/owners get (see
    RoomViewSet.get_queryset). Workspace invitations are already
    admin/owner-gated (IsWorkspaceAdminOrOwner), so guests are implicitly
    blocked from sending them without a separate check."""
    return user_role(user) == UserProfile.ROLE_GUEST


def is_timed_out(user):
    """True if `user`'s UserProfile.timed_out_until is set and still in the
    future. Used by MessageListCreateView/PrivateMessageListCreateView to
    block posting (but not reading) while a timeout is active — see
    ModerationActionView, which sets this field when a "timeout"
    ModerationAction is issued."""
    profile = getattr(user, "userprofile", None)
    if not profile or not profile.timed_out_until:
        return False
    return timezone.now() < profile.timed_out_until


class IsAuthenticatedNotBanned(BasePermission):
    """The workspace's real "IsAuthenticated" — imported and used as
    `IsAuthenticated` throughout api_views.py/admin_views.py (see the
    `IsAuthenticatedNotBanned as IsAuthenticated` import there) so every
    endpoint that currently gates on plain authentication automatically
    picks up the ban check too, without having to touch each individual
    view's permission_classes by hand.

    A banned user (UserProfile.is_banned) is authenticated (their session
    cookie is still valid — Django doesn't invalidate other sessions when a
    flag flips) but rejected here on every subsequent request. Paired with
    the explicit check in LoginView (which blocks a banned user from even
    establishing a *new* session) and StreamConsumer.connect (which closes
    a banned user's WebSocket), this is the DRF-request-layer half of ban
    enforcement — see those two call sites for the other layers. What this
    does NOT do: forcibly terminate an already-open browser tab's UI state
    or an already-open WebSocket that connected before the ban — a banned
    user's *existing* page will start getting 403s on its next API call
    rather than being kicked out mid-render.
    """

    message = "Your account has been banned."

    def has_permission(self, request, view):
        user = request.user
        if user is None or not user.is_authenticated:
            return False
        profile = getattr(user, "userprofile", None)
        return not (profile and profile.is_banned)


class IsNotGuest(BasePermission):
    """Blocks the workspace "guest" role from actions reserved for
    member/admin/owner — currently room creation. Authentication itself is
    still required first (this only adds the guest carve-out); pair with
    IsAuthenticated in `permission_classes`/`get_permissions()`, the same
    way IsRoomAdminOrWorkspaceAdmin etc. are paired."""

    message = "Guests cannot perform this action."

    def has_permission(self, request, view):
        user = request.user
        if user is None or not user.is_authenticated:
            return False
        return not is_guest(user)


def user_can_access_room(user, room):
    """True if `user` may view/participate in `room` at all (read messages,
    react, thread, set their own notification override, etc — the baseline
    "can they see this room" check every room-scoped nested view needs
    before doing anything more specific).

    A private room has always required RoomMembership. Guests (see
    is_guest) are additionally denied PUBLIC rooms they haven't been
    explicitly added to — unlike member/admin/owner, who can freely read/
    join any public room just by virtue of being authenticated. So: any
    non-member of a private room is denied, and a guest who isn't a member
    of ANY room (public or private) is denied too."""
    if room.is_private or is_guest(user):
        return room.is_member(user)
    return True


def visible_rooms_for(user):
    """Queryset of every Room `user` may see at all — the exact same
    visibility rule `user_can_access_room` applies per-room, expressed as a
    queryset filter so callers that need to search/list across many rooms
    (RoomViewSet.get_queryset, and the search slice's SearchView in
    api_views.py) don't reimplement "public rooms + private rooms I'm a
    member of, guests further restricted to rooms they're explicitly in"
    a second time. Keeping this in exactly one place matters here
    specifically: the search endpoint reuses it (rather than a parallel
    Q() filter) so it can never drift out of sync with RoomViewSet and
    accidentally leak private-room content into search results.
    """
    from .models import Room

    if is_guest(user):
        return Room.objects.filter(memberships__user=user).distinct()
    return Room.objects.filter(Q(is_private=False) | Q(memberships__user=user)).distinct()


def user_can_manage_room(user, room):
    """True if `user` may administer `room` — edit it, archive/unarchive it,
    or manage its membership: either a workspace admin/owner, or that room's
    own RoomMembership admin. Shared by IsRoomAdminOrWorkspaceAdmin (used for
    RoomViewSet's update/archive actions) and the room-member-management
    views in api_views.py, so "who can manage this room" lives in exactly
    one place instead of being reimplemented per endpoint."""
    if user is None or not user.is_authenticated:
        return False
    profile = getattr(user, "userprofile", None)
    if profile and profile.is_admin_or_owner:
        return True
    return RoomMembership.objects.filter(room=room, user=user, role="admin").exists()


class IsWorkspaceAdminOrOwner(BasePermission):
    """Grants access only to authenticated users whose UserProfile.role is
    "admin" or "owner" — the single-workspace equivalent of a workspace-wide
    admin check. Used to gate invitation management today; other admin-only
    endpoints in later slices can reuse this. Also used directly (not via
    IsRoomAdminOrWorkspaceAdmin) as RoomViewSet's destroy permission — hard
    room deletion is reserved for workspace-level authority only, not a
    room's own admin (see IsRoomAdminOrWorkspaceAdmin's docstring)."""

    message = "You must be a workspace admin or owner to do this."

    def has_permission(self, request, view):
        user = request.user
        if user is None or not user.is_authenticated:
            return False
        profile = getattr(user, "userprofile", None)
        return bool(profile and profile.is_admin_or_owner)


class IsRoomAdminOrWorkspaceAdmin(BasePermission):
    """Object-level permission for Room instances: only that room's own
    RoomMembership admin, or a workspace admin/owner, may modify it (update/
    partial_update, archive/unarchive). Deliberately NOT used for `destroy`
    — permanently deleting a room's message history is reserved for
    workspace-level authority (IsWorkspaceAdminOrOwner), a bigger and rarer
    action than a room admin renaming their room or archiving it."""

    message = "You must be this room's admin or a workspace admin/owner to do this."

    def has_object_permission(self, request, view, obj):
        return user_can_manage_room(request.user, obj)

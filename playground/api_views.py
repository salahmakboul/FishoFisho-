import random

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from django.utils import timezone

from .audit import log_audit_event
from .ai_helper import AIAssistant
from .models import (
    AIInteraction,
    AuditEvent,
    BlockedKeyword,
    Invitation,
    Message,
    MessageAttachment,
    Notification,
    NotificationPreference,
    PrivateConversation,
    PrivateMessage,
    Reaction,
    Room,
    RoomMembership,
    RoomNotificationSetting,
    ThreadFollow,
    Topic,
    UserProfile,
    Webhook,
    notification_allowed,
    sanitize_attachment_filename,
)
from .pagination import (
    ConversationMessageCursorPagination,
    RoomMessageCursorPagination,
    RoomSearchCursorPagination,
    UserSearchCursorPagination,
)
from .permissions import (
    IsAuthenticatedNotBanned as IsAuthenticated,
    IsNotGuest,
    IsRoomAdminOrWorkspaceAdmin,
    IsWorkspaceAdminOrOwner,
    is_guest,
    is_timed_out,
    user_can_access_room,
    user_can_manage_room,
    visible_rooms_for,
)
from .signing import sign_attachment_url, verify_attachment_signature
from .serializers import (
    InvitationSerializer,
    MessageSearchResultSerializer,
    MessageSerializer,
    NotificationPreferenceSerializer,
    NotificationSerializer,
    PrivateConversationSerializer,
    PrivateMessageSerializer,
    RoomMembershipSerializer,
    RoomNotificationSettingSerializer,
    RoomSerializer,
    TopicSerializer,
    UserProfileSerializer,
    UserSerializer,
    WebhookSerializer,
)

# --- basic attachment validation (messaging slice; NOT slice 10's cloud/
# signed-URL storage) ---
MAX_ATTACHMENT_SIZE_BYTES = 10 * 1024 * 1024  # 10MB
ALLOWED_ATTACHMENT_CONTENT_TYPES = {
    # images
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    # common docs
    "application/pdf",
    "text/plain",
    "text/csv",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/zip",
}


def reject_if_room_archived(room):
    """Shared with IncomingWebhookView (webhooks slice) — an archived room
    stops accepting new messages regardless of whether the poster is an
    authenticated FishoFisho user (MessageListCreateView.perform_create) or
    an external service posting through a token-authenticated incoming
    webhook."""
    if room.is_archived:
        raise ValidationError(
            {"detail": "This room is archived and no longer accepts new messages."}
        )


def reject_if_blocked_keyword(body):
    """Case-insensitive substring match against the workspace-wide
    BlockedKeyword list. Extracted out of MessageListCreateView so
    IncomingWebhookView (webhooks slice) can apply the exact same filtering
    rule to messages posted by external services, instead of copy-pasting
    the check a second time. See BlockedKeywordSerializer's docstring for
    why this is a substring filter, not real spam detection."""
    if not body:
        return
    lowered = str(body).lower()
    for phrase in BlockedKeyword.objects.values_list("phrase", flat=True):
        if phrase and phrase.lower() in lowered:
            raise ValidationError({"body": ["This message contains a blocked phrase."]})


# ---------------------------------------------------------------------------
# Auth
#
# Replaces the old server-rendered loginPage/registerUser/logoutUSER views
# (playground/views.py, now removed) with session-cookie-based API
# endpoints the React SPA calls directly. These still use Django's regular
# session auth (django.contrib.auth.login/logout, which just mutates
# request.session) — only the transport changed from an HTML form post to
# a JSON POST, so the resulting session cookie is exactly what
# rest_framework.authentication.SessionAuthentication already expects on
# every other /api/v1/ endpoint.
# ---------------------------------------------------------------------------


class CsrfView(APIView):
    """GET /api/v1/auth/csrf/ — has no purpose other than making sure the
    `csrftoken` cookie is set (via @ensure_csrf_cookie) before the SPA's
    first unsafe-method call. DRF's SessionAuthentication enforces CSRF on
    POST/PUT/PATCH/DELETE, and the browser only ever sends the cookie back
    once something has set it; a plain page load doesn't guarantee that.
    The frontend calls this once, reads the cookie itself (see
    lib/api.ts's getCookie), and sends it back as X-CSRFToken on writes.
    """

    permission_classes = [AllowAny]

    @method_decorator(ensure_csrf_cookie)
    def get(self, request):
        return Response({"detail": "ok"})


class LoginView(APIView):
    """POST /api/v1/auth/login/ {"username", "password"}.

    Same authenticate()+login() logic as the old loginPage view, including
    lowercasing the username before authenticating (usernames are stored
    lowercase — see RegisterView below). Returns the logged-in user
    (UserSerializer) on success, or 400 with a single error message on bad
    credentials, matching the old view's "Username or password does not
    exist." messages.error() text.

    Also accepts an optional ``remember`` boolean. This is a chat app people
    expect to stay signed into, so the default (``remember`` omitted or
    true) is the normal sliding session from SESSION_COOKIE_AGE (see
    settings.py). Only an explicit ``remember: false`` shortens the session
    to "expires when the browser closes" via request.session.set_expiry(0).
    """

    permission_classes = [AllowAny]

    def post(self, request):
        username = (request.data.get("username") or "").lower()
        password = request.data.get("password") or ""
        remember = request.data.get("remember", True)
        user = authenticate(request, username=username, password=password)
        if user is None:
            return Response(
                {"detail": "Username or password does not exist."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Moderation slice: a banned user's credentials may still be
        # correct, but they never get a session at all — checked here
        # (AllowAny, so the IsAuthenticatedNotBanned permission chain check
        # in permissions.py never runs for this view) rather than just
        # relying on that permission to reject their FIRST post-login
        # request, which would otherwise briefly hand them a live session.
        profile = getattr(user, "userprofile", None)
        if profile and profile.is_banned:
            return Response(
                {"detail": "This account has been banned."},
                status=status.HTTP_403_FORBIDDEN,
            )
        login(request, user)
        if not remember:
            request.session.set_expiry(0)
        return Response(UserSerializer(user).data)


class RegisterView(APIView):
    """POST /api/v1/auth/register/ {"username", "password1", "password2",
    "invite_token"?}.

    Validates via UserCreationForm — the same form (and therefore the same
    password-confirmation + AUTH_PASSWORD_VALIDATORS checks) the old
    registerUser view used — rather than hand-rolling validation. On
    success, lowercases the username (matching the old view) and logs the
    new user in immediately. On failure, returns the form's field errors
    (e.g. {"username": [...], "password2": [...]}) as 400 so the frontend
    can surface them next to the relevant field.

    Registration stays open (no "invite-only" toggle exists anywhere in
    this app) — `invite_token` is optional. When present, it must resolve
    to a valid (unaccepted, unexpired) Invitation or the whole request 400s
    with a clear error rather than silently falling back to a normal
    signup — a caller who bothered to include a token wanted its role
    applied, not for it to be quietly ignored. On success the new user's
    UserProfile.role is set from the invitation's role (instead of the
    default "member") and the invitation is marked accepted.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        invite_token = (request.data.get("invite_token") or "").strip()
        invitation = None
        if invite_token:
            invitation = Invitation.objects.filter(token=invite_token).first()
            if invitation is None or not invitation.is_valid:
                return Response(
                    {"invite_token": ["This invite link is invalid or has expired."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        form = UserCreationForm(request.data)
        if not form.is_valid():
            return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
        user = form.save(commit=False)
        user.username = user.username.lower()
        user.save()

        if invitation is not None:
            # Mutate the *same* UserProfile instance signals.py's
            # save_user_profile already cached on `user.userprofile` during
            # user.save() above (rather than fetching a separate instance
            # via UserProfile.objects.get_or_create) — login() below fires
            # Django's built-in update_last_login receiver, which calls
            # user.save(update_fields=["last_login"]) and therefore
            # re-triggers save_user_profile, which re-saves whatever is
            # still cached on user.userprofile. If that were a stale
            # role="member" object, it would silently clobber the role we
            # just set here.
            profile = user.userprofile
            profile.role = invitation.role
            profile.save(update_fields=["role"])
            invitation.accept(user)

        login(request, user)
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


class LogoutView(APIView):
    """POST /api/v1/auth/logout/. AllowAny because logging out an
    already-anonymous session should cleanly no-op (django.contrib.auth's
    logout() already does nothing harmful if there's no active session) —
    not 401 just because you're not logged in.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        logout(request)
        return Response({"detail": "ok"})


_GENERIC_RESET_MESSAGE = (
    "If that account exists, a password reset link has been sent."
)


class PasswordResetRequestView(APIView):
    """POST /api/v1/auth/password-reset/ {"username"}.

    This app has no email field collected at registration (RegisterView
    uses plain UserCreationForm — username + password only), so there's no
    real address to send a reset link to. Requesting by username is
    therefore the only option available; the "email" is "sent" via
    settings.EMAIL_BACKEND, which is the console backend for now (prints to
    the server log instead of actually delivering anything — see the
    comment in settings.py for how to swap in real SMTP later).

    Always returns 200 with the same generic message whether or not the
    username exists, so a caller can't use this endpoint to enumerate
    registered usernames.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        username = (request.data.get("username") or "").lower().strip()
        user = User.objects.filter(username=username).first() if username else None
        if user is not None:
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            reset_link = f"{settings.FRONTEND_URL}/reset-password?uid={uid}&token={token}"
            send_mail(
                subject="Reset your FishoFisho password",
                message=(
                    f"Hi {user.username},\n\n"
                    "Someone (hopefully you) requested a password reset for "
                    "your FishoFisho account. Use the link below to choose a "
                    "new password:\n\n"
                    f"{reset_link}\n\n"
                    "If you didn't request this, you can safely ignore this "
                    "email."
                ),
                from_email=None,
                recipient_list=[f"{user.username}@example.invalid"],
                fail_silently=True,
            )
        return Response({"detail": _GENERIC_RESET_MESSAGE})


class PasswordResetConfirmView(APIView):
    """POST /api/v1/auth/password-reset-confirm/
    {"uid", "token", "password1", "password2"}.

    Decodes ``uid`` back to a user, validates ``token`` via the same
    PasswordResetTokenGenerator used to issue it (this is what actually
    enforces expiry — Django's default generator invalidates tokens after
    PASSWORD_RESET_TIMEOUT seconds, default 3 days, and also invalidates a
    token the moment the password it was issued for has already been
    changed). On success, sets the new password and logs the user in
    immediately, matching RegisterView's "you're in right away" behavior.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        uid = request.data.get("uid") or ""
        token = request.data.get("token") or ""
        password1 = request.data.get("password1") or ""
        password2 = request.data.get("password2") or ""

        try:
            user_id = force_str(urlsafe_base64_decode(uid))
            user = User.objects.get(pk=user_id)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            user = None

        if user is None or not default_token_generator.check_token(user, token):
            return Response(
                {"detail": "This password reset link is invalid or has expired."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if password1 != password2:
            return Response(
                {"password2": ["The two password fields didn't match."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            validate_password(password1, user=user)
        except DjangoValidationError as exc:
            return Response(
                {"password1": list(exc.messages)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(password1)
        user.save()
        login(request, user)
        return Response(UserSerializer(user).data)


class TopicViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only list of topics (topics are only created via admin today)."""

    queryset = Topic.objects.all()
    serializer_class = TopicSerializer
    permission_classes = [IsAuthenticated]


class RoomViewSet(viewsets.ModelViewSet):
    """CRUD for rooms.

    Query params:
      - ``topic``: filter by topic id
      - ``q``: search room name/description (mirrors the `home` view's search)

    Private rooms (``is_private=True``) are only visible/listed to their
    RoomMembership members; public rooms stay open to any authenticated
    user, same as before privacy existed.

    Authorization:
      - list/retrieve: any authenticated user, EXCEPT the "guest" workspace
        role — see get_queryset's guest carve-out below, guests only see
        rooms they've been explicitly added to (public or private), not
        every public room the way member/admin/owner do.
      - create: any authenticated user except guests (IsNotGuest) — Slack-
        style: a guest can participate in rooms they're invited into, but
        can't spin up new ones.
      - update/partial_update, archive/unarchive: that room's own
        RoomMembership admin, or a workspace admin/owner
        (IsRoomAdminOrWorkspaceAdmin).
      - destroy (hard delete): workspace admin/owner ONLY, not room admins
        — see IsRoomAdminOrWorkspaceAdmin's docstring for why that split
        exists. Room admins can archive instead (reversible, keeps
        history); permanently deleting a room's message history is a
        bigger, rarer action reserved for workspace-level authority.

    Archived rooms (``is_archived=True``) are excluded from the default
    ``list`` results (they're not deleted — still reachable by id, and
    still visible to members via ``?include_archived=true``); only the
    `list` action applies this filter so a member can still open/retrieve
    an archived room directly, or a room admin can unarchive one, without
    needing the query param.
    """

    serializer_class = RoomSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.action in ("update", "partial_update", "archive", "unarchive"):
            return [IsAuthenticated(), IsRoomAdminOrWorkspaceAdmin()]
        if self.action == "destroy":
            return [IsAuthenticated(), IsWorkspaceAdminOrOwner()]
        if self.action == "create":
            return [IsAuthenticated(), IsNotGuest()]
        return [IsAuthenticated()]

    def get_queryset(self):
        # Guests (UserProfile.role == "guest") only ever see rooms they've
        # been explicitly added to via RoomMembership — even public ones —
        # unlike member/admin/owner, who can freely see/join any public
        # room. This is a judgment call (the brief doesn't enumerate exact
        # guest restrictions): it mirrors Slack's "single/multi-channel
        # guest" model, where a guest's visibility is whatever they were
        # explicitly invited into, not the whole public workspace.
        queryset = visible_rooms_for(self.request.user)
        topic = self.request.query_params.get("topic")
        if topic:
            queryset = queryset.filter(topic_id=topic)
        q = self.request.query_params.get("q")
        if q:
            queryset = queryset.filter(
                Q(name__icontains=q) | Q(description__icontains=q)
            )
        include_archived = (
            self.request.query_params.get("include_archived", "").lower() == "true"
        )
        if self.action == "list" and not include_archived:
            queryset = queryset.exclude(is_archived=True)
        return queryset

    def perform_create(self, serializer):
        room = serializer.save(host=self.request.user)
        RoomMembership.objects.get_or_create(
            room=room, user=self.request.user, defaults={"role": "admin"}
        )

    def perform_destroy(self, instance):
        # Set target_room=instance so the AuditEvent captures it before the
        # delete; Django's SET_NULL collector nulls it back out on the row
        # we just created the moment instance.delete() actually runs (see
        # AuditEvent's docstring) — `detail` is what survives that.
        log_audit_event(
            self.request.user,
            AuditEvent.ACTION_ROOM_DELETED,
            target_room=instance,
            detail=f"room '{instance.name}' deleted",
        )
        instance.delete()

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        room = self.get_object()
        room.is_archived = True
        room.save(update_fields=["is_archived"])
        log_audit_event(
            request.user, AuditEvent.ACTION_ROOM_ARCHIVED, target_room=room, detail=f"room '{room.name}' archived"
        )
        return Response(RoomSerializer(room).data)

    @action(detail=True, methods=["post"])
    def unarchive(self, request, pk=None):
        room = self.get_object()
        room.is_archived = False
        room.save(update_fields=["is_archived"])
        return Response(RoomSerializer(room).data)


class MessageListCreateView(generics.ListCreateAPIView):
    """List/create messages scoped to a single room: /api/v1/rooms/<room_pk>/messages/

    Ports the AI-assistant auto-reply behavior that used to live in the old
    server-rendered `room` view's POST handler (retired along with the rest
    of the template UI): after the user's message is saved, if the body
    mentions "@fishoai" (case-insensitive) or on an unconditional 20% random
    roll, ask the existing `AIAssistant` (ai_helper.py, unchanged) for a
    reply and save it as a new Message from the FishoAI bot user. That save
    fires Message's own post_save signal (signals.py), which broadcasts the
    bot reply over the channel layer to connected WebSocket clients exactly
    like a normal message — no extra wiring needed here.

    Kept synchronous to match the old view's behavior (see task notes) —
    this runs inside the DRF request/response cycle, not as a background
    task. `AIAssistant()` and its Gemini calls are defensively wrapped in
    try/except, same as the original view, so a flaky/missing API key can
    never turn a normal message post into a 500.

    Judgment call: `AIAssistant.respond_to_message()` only returns a reply
    ~30% of the time it's called (its own internal chance check) and
    returns None otherwise. The old view created a Message with
    `body=None` in that case regardless — since `Message.body` is
    nullable, that silently created empty AI messages, and worse,
    `Message.save()` -> `check_mentions()` does `re.findall(pattern,
    self.body)`, which raises TypeError on a None body. That is fixed here
    by simply not creating a bot message when there is no reply text.
    """

    serializer_class = MessageSerializer
    permission_classes = [IsAuthenticated]
    # Cursor pagination, newest-first (see pagination.py docstring): the
    # first page is "the latest N messages", "next" walks further into the
    # past for a "load older messages" trigger on the frontend.
    pagination_class = RoomMessageCursorPagination
    # Rate limiting slice: message creation gets its own, stricter scoped
    # rate (see DEFAULT_THROTTLE_RATES["messages"] in settings.py) on top of
    # the general per-user rate every view already gets — this is the
    # endpoint flooding/spam actually targets. Only meaningful for the
    # unsafe methods (POST); GET (listing) is still covered by the general
    # 'user' rate only, same as every other read endpoint.
    throttle_scope = "messages"

    def get_room(self):
        room = get_object_or_404(Room, pk=self.kwargs["room_pk"])
        # Public rooms stay open to any authenticated user, as before
        # privacy existed. Private rooms require RoomMembership — matching
        # the 404 (not 403) RoomViewSet's own get_queryset effectively
        # produces for a private room a non-member can't see in listing,
        # this keeps a non-member from being able to distinguish "room
        # doesn't exist" from "room exists but is private".
        if not user_can_access_room(self.request.user, room):
            from django.http import Http404

            raise Http404("Room not found")
        return room

    def get_queryset(self):
        room = self.get_room()
        return (
            Message.objects.filter(room=room)
            .select_related("user", "reply_to", "reply_to__user")
            .prefetch_related("reactions", "attachments")
            # Annotated so MessageSerializer.get_reply_count can read
            # `reply_count` straight off each row instead of issuing a
            # per-message COUNT query on every page of a room's history.
            .annotate(reply_count=Count("replies", distinct=True))
            .order_by("-created")
        )

    def create(self, request, *args, **kwargs):
        # Idempotency: a retried POST (e.g. after a flaky network drop where
        # the client isn't sure the first attempt landed) carrying the same
        # client-generated client_id for this user returns the message that
        # was already created instead of creating a duplicate. Only applies
        # when client_id is provided — existing callers that never send one
        # are unaffected. Scoped to (user, client_id) only, not per-room,
        # matching the client_id's job as a global per-attempt key.
        client_id = (request.data.get("client_id") or "").strip()
        if client_id:
            existing = (
                Message.objects.filter(user=request.user, client_id=client_id)
                .select_related("user", "reply_to", "reply_to__user")
                .prefetch_related("reactions", "attachments")
                .first()
            )
            if existing is not None:
                return Response(self.get_serializer(existing).data, status=status.HTTP_200_OK)
        return super().create(request, *args, **kwargs)

    def _resolve_reply_to(self, room):
        reply_to_id = self.request.data.get("reply_to")
        if not reply_to_id:
            return None
        from rest_framework.exceptions import ValidationError

        reply_to = Message.objects.filter(pk=reply_to_id, room=room).first()
        if reply_to is None:
            raise ValidationError({"reply_to": ["Message not found in this room."]})
        # Threading: always resolve onto the thread ROOT, not the message the
        # user happened to hit "reply" on. If the target is itself already a
        # reply (has its own reply_to), walk up one level to its root instead
        # of chaining — see Message.reply_to's docstring for why this keeps
        # "all messages in a thread" a flat query.
        if reply_to.reply_to_id is not None:
            root = Message.objects.filter(pk=reply_to.reply_to_id, room=room).first()
            if root is not None:
                return root
        return reply_to

    def _reject_if_blocked_keyword(self, body):
        """Keyword/spam filtering slice: case-insensitive substring match
        against the workspace-wide BlockedKeyword list. Judgment call: a
        hard 400 rejection (the message is never created at all), not a
        "moderation queue" that holds it for admin review — simplest,
        most honest behavior for what's explicitly a substring filter, not
        real spam detection (see BlockedKeyword's docstring). Only wired
        into room messages (not PrivateMessage/DMs) per the task scope.

        Delegates to the module-level reject_if_blocked_keyword (webhooks
        slice extracted it out so IncomingWebhookView can reuse the exact
        same rule for externally-posted messages).
        """
        reject_if_blocked_keyword(body)

    def _validate_attachment(self, file):
        from rest_framework.exceptions import ValidationError

        if file.size > MAX_ATTACHMENT_SIZE_BYTES:
            raise ValidationError(
                {
                    "attachment": [
                        f"File too large (max {MAX_ATTACHMENT_SIZE_BYTES // (1024 * 1024)}MB)."
                    ]
                }
            )
        content_type = file.content_type or ""
        if content_type not in ALLOWED_ATTACHMENT_CONTENT_TYPES:
            raise ValidationError({"attachment": ["Unsupported file type."]})

    def perform_create(self, serializer):
        room = self.get_room()
        reject_if_room_archived(room)
        # Moderation slice: a timed-out user can still read (GET isn't
        # touched at all) but every POST here is rejected until
        # timed_out_until passes. Deliberately a hard 403, not a queued/
        # silently-dropped post — the user should know why their message
        # didn't land.
        if is_timed_out(self.request.user):
            from rest_framework.exceptions import PermissionDenied

            until = self.request.user.userprofile.timed_out_until
            raise PermissionDenied(f"You are timed out until {until.isoformat()}.")

        self._reject_if_blocked_keyword(self.request.data.get("body"))

        reply_to = self._resolve_reply_to(room)
        attachment_file = self.request.FILES.get("attachment")
        if attachment_file is not None:
            # Validate *before* creating the message, so a rejected
            # attachment never leaves an orphaned Message row behind.
            self._validate_attachment(attachment_file)

        message = serializer.save(user=self.request.user, room=room, reply_to=reply_to)
        RoomMembership.objects.get_or_create(room=room, user=self.request.user)

        if attachment_file is not None:
            MessageAttachment.objects.create(
                message=message,
                file=attachment_file,
                original_filename=sanitize_attachment_filename(attachment_file.name),
                content_type=attachment_file.content_type or "",
                size_bytes=attachment_file.size,
            )

        if reply_to is not None:
            self._handle_thread_reply(message, reply_to)

        self._maybe_trigger_ai_reply(message, room)
        return message

    def _handle_thread_reply(self, message, root):
        """`root` is already the thread root (see _resolve_reply_to). Auto-
        follow both the replier and the root's own author — the root author
        may not have an explicit ThreadFollow row yet if this is the first
        reply their message has ever gotten — then notify every OTHER
        follower (existing repliers, anyone who explicitly followed via
        MessageFollowView) that a new reply landed. The replier themselves
        never gets notified about their own reply.
        """
        ThreadFollow.objects.get_or_create(root_message=root, user=self.request.user)
        ThreadFollow.objects.get_or_create(root_message=root, user=root.user)

        followers = User.objects.filter(
            id__in=ThreadFollow.objects.filter(root_message=root)
            .exclude(user_id=self.request.user.id)
            .values_list("user_id", flat=True)
        )
        for follower in followers:
            if not notification_allowed(follower, message.room, "thread_replies"):
                continue
            Notification.objects.create(
                recipient=follower,
                sender=self.request.user,
                notification_type="thread_reply",
                message=f"{self.request.user.username} replied in a thread you're following",
                room=message.room,
                is_read=False,
            )

    # Usage-limit slice: caps AI invocations per user to protect against a
    # user spamming "@fishoai" (or just getting unlucky on the 20% random
    # roll repeatedly) to run up Gemini API costs. This isn't its own DRF
    # endpoint (the trigger is a side effect of posting a message, not a
    # request in its own right), so it can't reuse ScopedRateThrottle
    # directly the way MessageListCreateView's own throttle_scope = "messages"
    # does — instead it's an application-level check against AIInteraction
    # rows, the same "count recent rows for this user" shape a throttle
    # would use internally. 20/hour chosen as a generous-but-bounded cap: it
    # comfortably covers a legitimate power user mentioning the bot several
    # times while making a spam loop (e.g. scripted @fishoai posts) cost the
    # attacker nothing after the cap trips — trips are silent (the user's
    # own message still posts fine, the AI just doesn't reply), never a 4xx.
    AI_HOURLY_LIMIT = 20

    def _ai_usage_limited(self, user):
        since = timezone.now() - timezone.timedelta(hours=1)
        return AIInteraction.objects.filter(user=user, created_at__gte=since).count() >= self.AI_HOURLY_LIMIT

    def _record_ai_interaction(self, *, user, room, trigger_message, trigger_type, context_count, response_message, succeeded):
        try:
            AIInteraction.objects.create(
                user=user,
                room=room,
                trigger_message=trigger_message,
                trigger_type=trigger_type,
                prompt_context_message_count=context_count,
                response_message=response_message,
                succeeded=succeeded,
            )
        except Exception:
            # Auditing must never be able to turn a successful (or already-
            # failed) AI reply into a 500 for the user's original message.
            pass

    def _maybe_trigger_ai_reply(self, message, room):
        """The single AI trigger point (mention command / random reply).
        Permission/context-boundary notes:

        - This is only ever reached from perform_create, AFTER
          `self.get_room()` has already enforced `user_can_access_room` for
          `self.request.user` — there is no path to trigger the AI in a room
          the invoking user can't themselves access.
        - Every code path below builds AI context (when it builds any at
          all — the plain mention/random paths don't; only "summarize"
          does, via AIAssistant.build_room_context) strictly from `room`,
          the exact Room this message was just posted in. No other room is
          ever queried.
        - Every trigger — success or failure, mention/random/summarize —
          writes exactly one AIInteraction row for auditability.
        """
        body = message.body or ""
        lowered = body.lower()
        user = self.request.user

        if "@fishoai" not in lowered and random.random() >= 0.2:
            return  # No trigger this time — nothing to record.

        if self._ai_usage_limited(user):
            # Silently skip: the user's message already saved fine, we just
            # don't call the AI (and don't spend an audit row on a call that
            # never happened).
            return

        is_mention = "@fishoai" in lowered
        # Keyword match only — not a full NLU/intent system (see task
        # notes): "summarize"/"summary" anywhere in a @fishoai mention is
        # treated as the summarize command, anything else falls back to the
        # existing general Q&A behavior.
        if is_mention and ("summarize" in lowered or "summary" in lowered):
            trigger_type = AIInteraction.TRIGGER_SUMMARIZE
        elif is_mention:
            trigger_type = AIInteraction.TRIGGER_MENTION
        else:
            if len(body) <= 5:
                return  # Matches the old "too short to bother" guard.
            trigger_type = AIInteraction.TRIGGER_RANDOM

        ai_response = None
        context_count = 0
        succeeded = False
        try:
            ai = AIAssistant()
            if trigger_type == AIInteraction.TRIGGER_SUMMARIZE:
                ai_response, context_count = ai.summarize_room(room, user)
            elif trigger_type == AIInteraction.TRIGGER_MENTION:
                ai_response = ai.handle_mention(body, room, user)
            else:
                ai_response = ai.respond_to_message(body, room, user)

            response_message = None
            if ai_response and ai.ai_user:
                response_message = Message.objects.create(
                    user=ai.ai_user, room=room, body=ai_response
                )
                succeeded = True
        except Exception:
            response_message = None

        self._record_ai_interaction(
            user=user,
            room=room,
            trigger_message=message,
            trigger_type=trigger_type,
            context_count=context_count,
            response_message=response_message,
            succeeded=succeeded,
        )


class MessageDetailView(generics.GenericAPIView):
    """Edit/delete a single message: /api/v1/rooms/<room_pk>/messages/<pk>/

    - PATCH ``{"body": "..."}`` — author only. Editing someone else's words
      is a more sensitive action than deleting them (deleting just removes
      it; editing puts words in their mouth), so unlike delete this is NOT
      extended to room/workspace admins. Sets `edited_at`; the mention list
      established at creation is left untouched (see Message.save()).
    - DELETE — author OR room admin/workspace admin (reuses
      `user_can_manage_room`, same helper RoomViewSet's admin actions use).
      Soft delete: flips `is_deleted`/`deleted_at` and blanks `body` rather
      than removing the row, so `reply_to` references and room history stay
      intact. Both actions broadcast over the channel layer via Message's
      own post_save signal (signals.py), following the exact same
      post_save-driven pattern "message.new" already uses — delete uses a
      save() with update_fields, not an actual post_delete, since it's a
      soft delete.
    """

    serializer_class = MessageSerializer
    permission_classes = [IsAuthenticated]

    def get_room(self):
        room = get_object_or_404(Room, pk=self.kwargs["room_pk"])
        if not user_can_access_room(self.request.user, room):
            raise Http404("Room not found")
        return room

    def get_object(self):
        room = self.get_room()
        return get_object_or_404(Message, pk=self.kwargs["pk"], room=room)

    def patch(self, request, room_pk, pk):
        message = self.get_object()
        if message.user_id != request.user.id:
            raise PermissionDenied("Only the author can edit this message.")
        if message.is_deleted:
            raise ValidationError({"detail": "Cannot edit a deleted message."})

        body = request.data.get("body")
        if body is None or not str(body).strip():
            raise ValidationError({"body": ["This field may not be blank."]})

        message.body = body
        message.edited_at = timezone.now()
        message.save(update_fields=["body", "edited_at", "update"])
        return Response(self.get_serializer(message).data)

    def delete(self, request, room_pk, pk):
        message = self.get_object()
        if message.user_id != request.user.id and not user_can_manage_room(
            request.user, message.room
        ):
            raise PermissionDenied(
                "Only the author or this room's admin/workspace admin can delete this message."
            )

        if not message.is_deleted:
            message.is_deleted = True
            message.deleted_at = timezone.now()
            message.body = ""
            message.save(update_fields=["is_deleted", "deleted_at", "body", "update"])
            # Audit slice: only log when someone OTHER than the author
            # removed it — the author deleting their own message is
            # ordinary self-service, not a moderation action worth an
            # audit trail entry.
            if request.user.id != message.user_id:
                log_audit_event(
                    request.user,
                    AuditEvent.ACTION_MESSAGE_REMOVED,
                    target_user=message.user,
                    target_room=message.room,
                    target_message=message,
                    detail=f"message by {message.user.username} removed in '{message.room.name}'",
                )

        return Response(self.get_serializer(message).data)


class MessageReactionView(APIView):
    """Toggle an emoji reaction on a message:
    POST /api/v1/rooms/<room_pk>/messages/<pk>/reactions/ ``{"emoji": "..."}``

    If the requesting user already reacted to this message with that exact
    emoji, the reaction is removed; otherwise it's added — a single endpoint
    handles both add and remove, matching how a reaction picker in the UI
    just toggles a pill. Reaction's own post_save/post_delete signals
    (signals.py) broadcast the updated summary as "message.reactions_changed";
    the response here returns the same summary directly so the actor doesn't
    need to wait on the websocket round-trip.
    """

    permission_classes = [IsAuthenticated]

    def get_room(self):
        room = get_object_or_404(Room, pk=self.kwargs["room_pk"])
        if not user_can_access_room(self.request.user, room):
            raise Http404("Room not found")
        return room

    def post(self, request, room_pk, pk):
        room = self.get_room()
        message = get_object_or_404(Message, pk=pk, room=room)

        emoji = request.data.get("emoji")
        if not emoji or not isinstance(emoji, str) or len(emoji) > 16:
            raise ValidationError({"emoji": ["A short emoji string is required."]})

        existing = Reaction.objects.filter(
            message=message, user=request.user, emoji=emoji
        ).first()
        if existing:
            existing.delete()
        else:
            Reaction.objects.create(message=message, user=request.user, emoji=emoji)

        return Response({"reactions": message.reaction_summary()})


class MessageThreadView(APIView):
    """GET /api/v1/rooms/<room_pk>/messages/<pk>/thread/ — the root message
    plus every reply in its thread, oldest first.

    `pk` may be the root itself or one of its replies (either way this
    resolves to the same thread) — mirrors _resolve_reply_to's "walk up to
    the root" behavior in MessageListCreateView, and lets the frontend open
    a thread panel from either "N replies" on a root or (in principle) a
    reply's own affordance without needing to know which id it has.

    Judgment call: a plain ordered list, not cursor-paginated. A thread's
    reply count is expected to be far smaller than a whole room's history —
    the same scale RoomMessageCursorPagination exists to handle — so paging
    would be needless complexity for the common case; if a thread genuinely
    grows huge this can gain pagination later without changing the URL.
    """

    serializer_class = MessageSerializer
    permission_classes = [IsAuthenticated]

    def get_room(self):
        room = get_object_or_404(Room, pk=self.kwargs["room_pk"])
        if not user_can_access_room(self.request.user, room):
            raise Http404("Room not found")
        return room

    def _resolve_root(self, room):
        message = get_object_or_404(Message, pk=self.kwargs["pk"], room=room)
        if message.reply_to_id is not None:
            root = Message.objects.filter(pk=message.reply_to_id, room=room).first()
            if root is not None:
                return root
        return message

    def get(self, request, room_pk, pk):
        room = self.get_room()
        root = self._resolve_root(room)

        thread_messages = (
            Message.objects.filter(room=room)
            .filter(Q(pk=root.pk) | Q(reply_to_id=root.pk))
            .select_related("user", "reply_to", "reply_to__user")
            .prefetch_related("reactions", "attachments")
            .annotate(reply_count=Count("replies", distinct=True))
            .order_by("created")
        )
        serializer = MessageSerializer(thread_messages, many=True, context={"request": request})
        return Response(serializer.data)


class MessageFollowView(APIView):
    """POST/DELETE /api/v1/rooms/<room_pk>/messages/<pk>/follow/ — explicitly
    follow/unfollow a thread's "thread_reply" notifications, for someone who
    wants updates without (yet) having posted in it themselves. Posting a
    reply already auto-follows (see MessageListCreateView._handle_thread_reply)
    — this is only needed for the "just watching" case. `pk` resolves to the
    thread root the same way MessageThreadView does, so it works whether you
    pass the root's id or a reply's id.
    """

    permission_classes = [IsAuthenticated]

    def get_room(self):
        room = get_object_or_404(Room, pk=self.kwargs["room_pk"])
        if not user_can_access_room(self.request.user, room):
            raise Http404("Room not found")
        return room

    def _resolve_root(self, room):
        message = get_object_or_404(Message, pk=self.kwargs["pk"], room=room)
        if message.reply_to_id is not None:
            root = Message.objects.filter(pk=message.reply_to_id, room=room).first()
            if root is not None:
                return root
        return message

    def post(self, request, room_pk, pk):
        room = self.get_room()
        root = self._resolve_root(room)
        ThreadFollow.objects.get_or_create(root_message=root, user=request.user)
        return Response({"following": True})

    def delete(self, request, room_pk, pk):
        room = self.get_room()
        root = self._resolve_root(room)
        ThreadFollow.objects.filter(root_message=root, user=request.user).delete()
        return Response({"following": False})


class AttachmentDownloadView(APIView):
    """GET /api/v1/attachments/<pk>/download/?sig=<token> — the ONLY way to
    read a message attachment's file (see ATTACHMENTS_ROOT/attachment_storage
    in models.py: the file itself lives outside anything statically served,
    so there is no raw /media/attachments/... URL anymore).

    Three checks, strictly in this order:
      1. signature valid and unexpired (playground/signing.py) — 400 if not.
         Checked first and cheaply, before touching the DB, so a forged/
         expired token can't be used to probe whether an attachment id
         exists at all.
      2. attachment exists — 404 (standard get_object_or_404) if not.
      3. requesting user can access the room the attachment's message
         belongs to (user_can_access_room, same helper every other
         room-scoped view uses) — 404, not 403, matching this codebase's
         existing convention (see MessageFollowView.get_room et al.) of not
         letting a non-member distinguish "doesn't exist" from "exists but
         you can't see it" for private-room resources.

    Requires session auth on top of the signature (IsAuthenticated) —
    deliberately NOT a fully-public S3-style signed URL. Real S3 pre-signed
    URLs are often used specifically to grant temporary *unauthenticated*
    access (e.g. so an <img> tag can load one with no special headers); here
    that's unnecessary since the frontend is same-origin and the session
    cookie already rides along with a plain <img src> request, so requiring
    auth too costs nothing and adds defense in depth. If this API ever grows
    a cross-origin consumer, that tradeoff would need revisiting.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        token = request.query_params.get("sig", "")
        if not verify_attachment_signature(pk, token):
            return Response(
                {"detail": "Invalid or expired download link."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        attachment = get_object_or_404(
            MessageAttachment.objects.select_related("message__room"), pk=pk
        )
        room = attachment.message.room
        if not user_can_access_room(request.user, room):
            raise Http404("Attachment not found")

        from django.http import FileResponse

        filename = sanitize_attachment_filename(attachment.original_filename)
        response = FileResponse(
            attachment.file.open("rb"),
            content_type=attachment.content_type or "application/octet-stream",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class RoomMemberListCreateView(generics.ListCreateAPIView):
    """List/add members of a single room: /api/v1/rooms/<room_pk>/members/

    - GET: visible under the exact same room-visibility rule
      MessageListCreateView.get_room() already applies (public room -> any
      authenticated user; private room -> members only, 404 rather than 403
      for non-members so a private room's existence isn't distinguishable
      from "doesn't exist").
    - POST ``{"user_id": N, "role"?: "admin"|"member"}`` (role defaults to
      "member"): adds a RoomMembership row. Room admin or workspace
      admin/owner only. Mainly meaningful for private rooms — public rooms
      already let anyone read/post without an explicit membership row
      (MessageListCreateView.perform_create lazily creates one on first
      post), this just lets an admin pre-add someone.
    """

    serializer_class = RoomMembershipSerializer
    permission_classes = [IsAuthenticated]

    def get_room(self):
        room = get_object_or_404(Room, pk=self.kwargs["room_pk"])
        if not user_can_access_room(self.request.user, room):
            from django.http import Http404

            raise Http404("Room not found")
        return room

    def get_queryset(self):
        room = self.get_room()
        return room.memberships.select_related("user").all()

    def perform_create(self, serializer):
        room = self.get_room()
        if not user_can_manage_room(self.request.user, room):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied(
                "You must be this room's admin or a workspace admin/owner to do this."
            )
        # Same guest-can't-be-room-admin rule RoomMemberDetailView.patch
        # enforces on role changes — apply it here too so an admin can't
        # route around it by directly adding the guest as "admin" instead
        # of adding-then-promoting.
        requested_role = self.request.data.get("role", "member")
        if requested_role == "admin" and is_guest(serializer.validated_data.get("user")):
            from rest_framework.exceptions import ValidationError

            raise ValidationError({"role": ["Guests cannot be added as a room admin."]})
        serializer.save(room=room)


class RoomMemberDetailView(APIView):
    """Change a member's role or remove them:
    /api/v1/rooms/<room_pk>/members/<user_id>/

    - PATCH ``{"role": "admin"|"member"}`` — room admin or workspace
      admin/owner only.
    - DELETE — room admin/workspace admin/owner, OR the member removing
      themselves ("leave room"). Either way, removing the room's last
      remaining admin is rejected with 400 rather than silently leaving the
      room admin-less.
    """

    permission_classes = [IsAuthenticated]

    def get_room(self):
        room = get_object_or_404(Room, pk=self.kwargs["room_pk"])
        if not user_can_access_room(self.request.user, room):
            from django.http import Http404

            raise Http404("Room not found")
        return room

    def get_membership(self, room):
        return get_object_or_404(
            RoomMembership, room=room, user_id=self.kwargs["user_id"]
        )

    def patch(self, request, room_pk, user_id):
        room = self.get_room()
        if not user_can_manage_room(request.user, room):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied(
                "You must be this room's admin or a workspace admin/owner to do this."
            )
        membership = self.get_membership(room)
        role = request.data.get("role")
        if role not in dict(RoomMembership.ROLE_CHOICES):
            return Response(
                {"role": ["Must be one of: admin, member."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if role == "admin" and is_guest(membership.user):
            return Response(
                {"role": ["Guests cannot be promoted to a room admin."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if membership.role == "admin" and role != "admin":
            other_admins = room.memberships.filter(role="admin").exclude(
                pk=membership.pk
            )
            if not other_admins.exists():
                return Response(
                    {"detail": "Cannot demote the last admin of this room."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        old_role = membership.role
        membership.role = role
        membership.save(update_fields=["role"])
        if old_role != role:
            log_audit_event(
                request.user,
                AuditEvent.ACTION_ROLE_CHANGED,
                target_user=membership.user,
                target_room=room,
                detail=f"room role: {old_role} -> {role}",
            )
        return Response(RoomMembershipSerializer(membership).data)

    def delete(self, request, room_pk, user_id):
        room = self.get_room()
        membership = self.get_membership(room)
        is_self = str(request.user.id) == str(user_id)
        if not is_self and not user_can_manage_room(request.user, room):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied(
                "You must be this room's admin or a workspace admin/owner to do this."
            )
        if membership.role == "admin":
            other_admins = room.memberships.filter(role="admin").exclude(
                pk=membership.pk
            )
            if not other_admins.exists():
                return Response(
                    {"detail": "Cannot remove the last admin of this room."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        removed_user = membership.user
        membership.delete()
        # Only an admin removing SOMEONE ELSE is a moderation action worth
        # auditing — a member leaving on their own ("Leave room" in the UI,
        # is_self above) is ordinary self-service.
        if not is_self:
            log_audit_event(
                request.user,
                AuditEvent.ACTION_MEMBER_REMOVED,
                target_user=removed_user,
                target_room=room,
                detail=f"removed from room '{room.name}'",
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class SearchView(APIView):
    """GET /api/v1/search/?q=<query>&type=messages|rooms|users|all
    (default "all"). Authenticated only.

    - **messages**: ``Message.objects.filter(body__icontains=q)`` scoped to
      ONLY rooms the requester can see — via `visible_rooms_for` (see
      permissions.py), the exact same helper RoomViewSet.get_queryset now
      uses for its own room listing, extracted into one shared function
      specifically so search can reuse it instead of re-deriving "public
      rooms + private rooms I'm a member of, guests further restricted"
      a second time and risking the two definitions drifting apart and
      leaking private-room content. Excludes soft-deleted messages
      (``is_deleted=True``).

      Archived rooms ARE included. Archiving only blocks new posts
      (MessageListCreateView.perform_create rejects POSTs to an archived
      room) — it doesn't hide the room or its history: RoomViewSet's own
      get_queryset only excludes archived rooms from the default *list*
      view, and a member can still GET/retrieve an archived room and its
      full message history same as before. Excluding archived-room
      messages from search would make previously-findable content silently
      vanish from search results the moment a room is archived, which
      doesn't match that read-access story.

    - **rooms**: ``Q(name__icontains=q) | Q(description__icontains=q)``,
      same `visible_rooms_for` scoping (also includes archived rooms, for
      the same reason as above).

    - **users**: ``Q(username__icontains=q) | Q(userprofile__bio__icontains=q)``
      — no extra scoping beyond the existing user directory's baseline
      (UserViewSet: any authenticated user can see any user).

    ``?type=all`` (the default) returns a fixed-size preview — the top
    ``PREVIEW_LIMIT`` (20) results per category, unpaginated — so a
    combined "search everything" view stays fast and doesn't need a client
    to know it's fetching 3 separate paginated lists at once. ``?type=`` a
    single category instead returns that category's results cursor-
    paginated (reusing RoomMessageCursorPagination for messages, and the
    new RoomSearchCursorPagination/UserSearchCursorPagination in
    pagination.py for rooms/users) for a caller that wants to page through
    more than the preview cap.

    Ordering: messages are newest-first (matches every other message feed
    in this app). Rooms/users use a simple heuristic — NOT real relevance
    scoring, which would be overkill for this slice — exact
    name/username match first, then partial matches, each group
    alphabetical; applied only in the ``?type=all`` preview (see
    `_rank_exact_first`). The single-category paginated case uses plain
    alphabetical order instead: CursorPagination encodes its cursor purely
    from stable ordering-field values, and "exact match to THIS search
    term first" isn't a stable, term-independent ordering it can page
    across (see RoomSearchCursorPagination's docstring) — a deliberate
    trade-off of the ranking nicety for correct pagination.

    Highlighting: done client-side, not here. Each result carries the raw
    matched text (message body / room name+description / username+bio)
    plus enough context to act on it (room id+name for a message result);
    the frontend wraps the query substring itself (SearchCommand.tsx) —
    simpler than maintaining duplicate snippet/position-marker rendering
    logic on both the server and the client for a plain `icontains` match.
    """

    permission_classes = [IsAuthenticated]
    PREVIEW_LIMIT = 20
    # How many extra rows (beyond PREVIEW_LIMIT) to pull before ranking
    # exact-matches-first in Python for the ?type=all preview — cheap
    # enough for a "top 20" preview, avoids a DB-side CASE/WHEN just for a
    # capped, unpaginated convenience view.
    PREVIEW_OVERFETCH = 60

    def get(self, request):
        q = (request.query_params.get("q") or "").strip()
        search_type = (request.query_params.get("type") or "all").strip().lower()
        if search_type not in ("all", "messages", "rooms", "users"):
            raise ValidationError({"type": ["Must be one of: all, messages, rooms, users."]})

        if not q:
            if search_type == "all":
                return Response({"messages": [], "rooms": [], "users": []})
            return Response({"count": 0, "next": None, "previous": None, "results": []})

        if search_type == "messages":
            return self._paginated_messages(request, q)
        if search_type == "rooms":
            return self._paginated_rooms(request, q)
        if search_type == "users":
            return self._paginated_users(request, q)
        return self._preview(request, q)

    # --- base querysets (shared by both the preview and paginated paths) ---

    def _message_queryset(self, user, q):
        return (
            Message.objects.filter(room__in=visible_rooms_for(user), body__icontains=q, is_deleted=False)
            .select_related("user", "room")
            .order_by("-created")
        )

    def _room_queryset(self, user, q):
        return visible_rooms_for(user).filter(Q(name__icontains=q) | Q(description__icontains=q))

    def _user_queryset(self, q):
        return User.objects.filter(
            Q(username__icontains=q) | Q(userprofile__bio__icontains=q)
        ).distinct()

    @staticmethod
    def _rank_exact_first(items, q, attr):
        """Stable-sort `items` (already alphabetically ordered by the DB
        query) so exact (case-insensitive) matches on `attr` come first,
        without disturbing alphabetical order within each group — see the
        class docstring for why this is a Python-side preview-only
        heuristic rather than a DB ordering used for real pagination."""
        ql = q.lower()
        return sorted(items, key=lambda obj: getattr(obj, attr).lower() != ql)

    def _preview(self, request, q):
        messages = list(self._message_queryset(request.user, q)[: self.PREVIEW_LIMIT])

        rooms = list(self._room_queryset(request.user, q).order_by("name")[: self.PREVIEW_OVERFETCH])
        rooms = self._rank_exact_first(rooms, q, "name")[: self.PREVIEW_LIMIT]

        users = list(self._user_queryset(q).order_by("username")[: self.PREVIEW_OVERFETCH])
        users = self._rank_exact_first(users, q, "username")[: self.PREVIEW_LIMIT]

        return Response(
            {
                "messages": MessageSearchResultSerializer(messages, many=True, context={"request": request}).data,
                "rooms": RoomSerializer(rooms, many=True, context={"request": request}).data,
                "users": UserSerializer(users, many=True, context={"request": request}).data,
            }
        )

    def _paginated_messages(self, request, q):
        paginator = RoomMessageCursorPagination()
        page = paginator.paginate_queryset(self._message_queryset(request.user, q), request, view=self)
        serializer = MessageSearchResultSerializer(page, many=True, context={"request": request})
        return paginator.get_paginated_response(serializer.data)

    def _paginated_rooms(self, request, q):
        paginator = RoomSearchCursorPagination()
        qs = self._room_queryset(request.user, q).order_by("name")
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = RoomSerializer(page, many=True, context={"request": request})
        return paginator.get_paginated_response(serializer.data)

    def _paginated_users(self, request, q):
        paginator = UserSearchCursorPagination()
        qs = self._user_queryset(q).order_by("username")
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = UserSerializer(page, many=True, context={"request": request})
        return paginator.get_paginated_response(serializer.data)


class UserViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only user directory, used for the user directory page and
    @mention autocomplete."""

    queryset = User.objects.all().order_by("username")
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"])
    def me(self, request):
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        return Response(UserProfileSerializer(profile).data)


class ProfileView(generics.RetrieveUpdateAPIView):
    """Retrieve/update the authenticated user's own profile (bio + avatar).

    Supports multipart for avatar upload, matching the existing
    edit_profile view/ProfileForm.
    """

    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_object(self):
        profile, _ = UserProfile.objects.get_or_create(user=self.request.user)
        return profile


class InvitationViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Admin/owner-only invitation management.

    - ``GET /api/v1/invitations/`` — list pending (unaccepted) invitations.
    - ``POST /api/v1/invitations/`` — create one, returns it including the
      shareable ``invite_link``.
    - ``DELETE /api/v1/invitations/{id}/`` — revoke an unaccepted invite.
    """

    serializer_class = InvitationSerializer
    permission_classes = [IsAuthenticated, IsWorkspaceAdminOrOwner]

    def get_queryset(self):
        # Listing only shows pending invites (the usable/actionable set).
        # Detail actions (destroy) need to see accepted ones too, so
        # perform_destroy's "already accepted" check below can actually
        # run instead of get_object() 404ing first.
        if self.action == "list":
            return Invitation.objects.filter(accepted_by__isnull=True)
        return Invitation.objects.all()

    def perform_create(self, serializer):
        invitation = serializer.save(invited_by=self.request.user)
        log_audit_event(
            self.request.user,
            AuditEvent.ACTION_INVITATION_CREATED,
            detail=f"role={invitation.role}" + (f", note={invitation.note}" if invitation.note else ""),
        )

    def perform_destroy(self, instance):
        if instance.is_accepted:
            from rest_framework.exceptions import ValidationError

            raise ValidationError("Cannot revoke an already-accepted invitation.")
        log_audit_event(
            self.request.user, AuditEvent.ACTION_INVITATION_REVOKED, detail=f"role={instance.role}"
        )
        instance.delete()


class NotificationViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """List the authenticated user's notifications + mark-read actions.

    Re-expresses the logic already implemented in views.py's
    mark_notification_read / mark_all_read / get_unread_count as DRF actions.
    """

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return self.request.user.notifications.all()

    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        notification = get_object_or_404(
            Notification, id=pk, recipient=request.user
        )
        notification.is_read = True
        notification.save()
        return Response({"success": True})

    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        request.user.notifications.filter(is_read=False).update(is_read=True)
        return Response({"success": True})

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        count = request.user.notifications.filter(is_read=False).count()
        return Response({"count": count})

    @action(detail=False, methods=["get", "patch"], url_path="preferences")
    def preferences(self, request):
        """The requesting user's own global notification-category toggles
        (mentions/channel_wide/thread_replies). Lazily get-or-created — see
        NotificationPreference.for_user — so there's nothing to backfill for
        existing users."""
        pref = NotificationPreference.for_user(request.user)
        if request.method == "PATCH":
            serializer = NotificationPreferenceSerializer(pref, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response(serializer.data)
        return Response(NotificationPreferenceSerializer(pref).data)


class RoomNotificationSettingView(generics.RetrieveUpdateAPIView):
    """GET/PATCH the requesting user's own per-room notification override:
    /api/v1/rooms/<room_pk>/notification-setting/. This is a PERSONAL
    preference (mute/all/mentions-only for a room I'm in), not room
    administration, so — unlike the rest of room settings — it's open to
    any authenticated user who can see the room, not gated behind
    IsRoomAdminOrWorkspaceAdmin. Matches RoomSettingsPanel.tsx on the
    frontend, which deliberately renders this section outside its
    admin-only gate."""

    serializer_class = RoomNotificationSettingSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        room = get_object_or_404(Room, pk=self.kwargs["room_pk"])
        # Audit fix: this previously created/returned the setting for ANY
        # room id without checking the requester could even see that room
        # (the docstring already claimed "who can see the room" but nothing
        # enforced it) — a non-member of a private room, or a guest not
        # added to a public room, could still get/patch a
        # RoomNotificationSetting row for it. Same access rule every other
        # room-scoped nested view applies.
        if not user_can_access_room(self.request.user, room):
            raise Http404("Room not found")
        setting, _ = RoomNotificationSetting.objects.get_or_create(room=room, user=self.request.user)
        return setting


class PrivateConversationViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Conversations the authenticated user participates in.

    Create expects either ``{"participant_id": <user id>}`` (legacy, 1:1
    only, kept for backward compatibility) or ``{"participant_ids": [<user
    id>, ...]}`` (accepts one or more — the group-DM path). The requesting
    user is always included as a participant automatically, whichever key
    is used.

    For exactly 2 total participants (1 other + self) this keeps the
    existing get-or-create behaviour, mirroring 1:1 DM semantics (mirrors
    the get-or-create behaviour of the existing start_chat view). For 3+
    participants a new conversation is always created — group DMs aren't
    deduped by membership, since two separately-started groups with the
    same members are still logically distinct conversations (a judgment
    call: unlike 1:1 DMs there's no single canonical "the" group between a
    set of people).
    """

    serializer_class = PrivateConversationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return PrivateConversation.objects.filter(
            participants=self.request.user
        ).prefetch_related("participants", "messages")

    def create(self, request, *args, **kwargs):
        participant_ids = request.data.get("participant_ids")
        if participant_ids is None:
            single = request.data.get("participant_id")
            participant_ids = [single] if single is not None else []
        if not isinstance(participant_ids, (list, tuple)):
            participant_ids = [participant_ids]

        other_users = list(User.objects.filter(id__in=participant_ids))
        if not other_users:
            from rest_framework.exceptions import ValidationError

            raise ValidationError({"detail": "At least one participant_id is required."})

        if len(other_users) == 1:
            other_user = other_users[0]
            # Two separate .filter() calls each add their own JOIN against
            # participants, so an .annotate(Count("participants")) here
            # would double-count (the classic multi-filter M2M + Count
            # pitfall) — check the exact participant count in Python
            # instead over the (usually tiny) set of candidates.
            candidates = (
                PrivateConversation.objects.filter(participants=request.user)
                .filter(participants=other_user)
                .distinct()
                .prefetch_related("participants")
            )
            conversation = next(
                (c for c in candidates if c.participants.count() == 2), None
            )
            if not conversation:
                conversation = PrivateConversation.objects.create()
                conversation.participants.add(request.user, other_user)
        else:
            conversation = PrivateConversation.objects.create()
            conversation.participants.add(request.user, *other_users)

        serializer = self.get_serializer(conversation)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class PrivateMessageListCreateView(generics.ListCreateAPIView):
    """List/create private messages scoped to a single conversation:
    /api/v1/conversations/<conversation_pk>/messages/

    Mirrors the existing private_chat view: viewing the list marks messages
    addressed to the requesting user as read (kept for behavioral parity
    with the current app rather than inventing a separate mark-read
    endpoint for private messages).
    """

    serializer_class = PrivateMessageSerializer
    permission_classes = [IsAuthenticated]
    # Same cursor-pagination reasoning as RoomMessageCursorPagination, just
    # ordered on this model's `created_at` field instead of `created`.
    pagination_class = ConversationMessageCursorPagination
    # Same scoped-throttle reasoning as MessageListCreateView.throttle_scope.
    throttle_scope = "messages"

    def get_conversation(self):
        conversation = get_object_or_404(
            PrivateConversation, pk=self.kwargs["conversation_pk"]
        )
        if self.request.user not in conversation.participants.all():
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You are not a participant of this conversation.")
        return conversation

    def create(self, request, *args, **kwargs):
        # Same idempotency behavior as MessageListCreateView.create() — see
        # its comment for the rationale. Scoped to (sender, client_id).
        client_id = (request.data.get("client_id") or "").strip()
        if client_id:
            existing = PrivateMessage.objects.filter(
                sender=request.user, client_id=client_id
            ).first()
            if existing is not None:
                return Response(self.get_serializer(existing).data, status=status.HTTP_200_OK)
        return super().create(request, *args, **kwargs)

    def get_queryset(self):
        conversation = self.get_conversation()
        queryset = PrivateMessage.objects.filter(
            conversation=conversation
        ).order_by("-created_at")
        # Viewing the list marks the conversation read *for this
        # participant only* (per-participant read tracking — see
        # ConversationRead in models.py), not a single shared is_read flag.
        conversation.mark_read_for(self.request.user)
        return queryset

    def perform_create(self, serializer):
        conversation = self.get_conversation()
        # Same timeout enforcement as MessageListCreateView.perform_create —
        # a timed-out user can still read DMs, just not send them.
        if is_timed_out(self.request.user):
            from rest_framework.exceptions import PermissionDenied

            until = self.request.user.userprofile.timed_out_until
            raise PermissionDenied(f"You are timed out until {until.isoformat()}.")
        message = serializer.save(
            conversation=conversation,
            sender=self.request.user,
        )
        conversation.save()  # bump updated_at
        # Sending a message implicitly means you've read up to it yourself.
        conversation.mark_read_for(self.request.user)
        return message


# ---------------------------------------------------------------------------
# Webhooks (integrations/webhooks slice)
#
# This is the "generic webhook primitive" boundary the roadmap brief calls
# for — a real GitHub/Jira/calendar integration would plug into it later
# rather than each hand-rolling its own event dispatch/auth. Outgoing
# delivery itself lives in signals.py (hooked into the existing Message
# post_save signal — see dispatch_outgoing_webhooks there, called from
# push_message_to_room_group), so this event model stays the ONE event
# system in the app rather than a second, parallel one. The views below are
# just the management surface (list/create/delete a room's Webhook rows)
# plus the incoming endpoint external services POST to.
# ---------------------------------------------------------------------------


def get_webhook_bot_user():
    """Get-or-create the shared bot identity messages posted through an
    incoming webhook are authored as — mirrors ai_helper.py's AIAssistant
    get-or-create of the "FishoAI" user. One generic "Webhook" bot (rather
    than one per Webhook row/integration) keeps this simple, per the task's
    own "your call which is simpler" — nothing here stops a later slice
    from giving each integration its own bot identity instead."""
    user, _created = User.objects.get_or_create(
        username="Webhook",
        defaults={
            "email": "webhook@fishofisho.com",
            "first_name": "Fisho",
            "last_name": "Webhook",
            "is_active": True,
            "is_staff": False,
            "is_superuser": False,
        },
    )
    return user


class WebhookListCreateView(generics.ListCreateAPIView):
    """List/create outgoing+incoming Webhook rows for a room:
    /api/v1/rooms/<room_pk>/webhooks/

    Gated by user_can_manage_room (that room's own admin, or a workspace
    admin/owner) — matching how RoomMemberListCreateView and friends are
    already authorized elsewhere in this codebase. Unlike most room-scoped
    views, the manage check applies to list as well as create: a webhook's
    target URL and (transiently, on create) its signing secret are
    sensitive enough that even seeing the configured list is admin-only,
    not "any room member".
    """

    serializer_class = WebhookSerializer
    permission_classes = [IsAuthenticated]

    def get_room(self):
        room = get_object_or_404(Room, pk=self.kwargs["room_pk"])
        if not user_can_manage_room(self.request.user, room):
            raise PermissionDenied(
                "You must be this room's admin or a workspace admin/owner to manage webhooks."
            )
        return room

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["request"] = self.request
        return context

    def get_queryset(self):
        return Webhook.objects.filter(room=self.get_room())

    def create(self, request, *args, **kwargs):
        room = self.get_room()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        webhook = serializer.save(room=room, created_by=request.user)
        # Shown-once secret: only THIS create response ever includes it —
        # every later GET/list of this row goes through WebhookSerializer
        # alone, which has no `secret` field at all (see its docstring).
        data = self.get_serializer(webhook).data
        data["secret"] = webhook.secret
        headers = self.get_success_headers(data)
        return Response(data, status=status.HTTP_201_CREATED, headers=headers)


class WebhookDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/rooms/<room_pk>/webhooks/<pk>/ — same
    user_can_manage_room gate as WebhookListCreateView. PATCH only really
    exists to flip `is_active` (the frontend's active toggle) — every other
    field (target_url, event_types, room, secret) is deliberately left
    editable at the serializer level too, but nothing in the UI exposes
    changing them after creation, and re-editing target_url without
    rotating the secret would be an odd half-feature to build UI for in
    this slice."""

    serializer_class = WebhookSerializer
    permission_classes = [IsAuthenticated]

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["request"] = self.request
        return context

    def get_object(self):
        room = get_object_or_404(Room, pk=self.kwargs["room_pk"])
        if not user_can_manage_room(self.request.user, room):
            raise PermissionDenied(
                "You must be this room's admin or a workspace admin/owner to manage webhooks."
            )
        return get_object_or_404(Webhook, pk=self.kwargs["pk"], room=room)


# Sliding-window-ish rate limit for POSTs to a single incoming-webhook
# token — mirrors MessageListCreateView.AI_HOURLY_LIMIT's "count recent
# activity in a window" shape, but backed by the cache (a lightweight
# per-token counter) rather than a DB table: there's no natural "one row
# per incoming webhook post" model to count the way AIInteraction rows
# already existed for the AI slice, and adding one purely to satisfy a rate
# limit would be more machinery than this primitive needs. An external
# service flooding a room can only do it INCOMING_WEBHOOK_RATE_LIMIT times
# per INCOMING_WEBHOOK_RATE_WINDOW_SECONDS before getting a 429.
INCOMING_WEBHOOK_RATE_LIMIT = 20
INCOMING_WEBHOOK_RATE_WINDOW_SECONDS = 60


def _incoming_webhook_rate_limited(token):
    from django.core.cache import cache

    cache_key = f"webhook_incoming_rate:{token}"
    count = cache.get(cache_key, 0)
    if count >= INCOMING_WEBHOOK_RATE_LIMIT:
        return True
    # Fixed-window counter: the window resets from whichever request
    # happens to start a new one, the same simple/approximate tradeoff
    # DRF's own throttle classes make elsewhere in this codebase (see
    # settings.py's DEFAULT_THROTTLE_RATES).
    cache.set(cache_key, count + 1, timeout=INCOMING_WEBHOOK_RATE_WINDOW_SECONDS)
    return False


class IncomingWebhookView(APIView):
    """POST /api/v1/webhooks/incoming/<token>/ {"text": "..."} — NO session
    auth: this is how an external service without a FishoFisho login posts
    a message into a room, authenticated only by knowing the unguessable
    per-Webhook `incoming_token`.

    Goes through the same archived-room rejection and keyword filtering
    MessageListCreateView.perform_create applies to normal messages
    (reject_if_room_archived / reject_if_blocked_keyword — both were
    extracted out of that view for exactly this reuse), but obviously skips
    every auth/session/per-user check that view has (there is no
    authenticated user here) in favor of a per-token rate limit.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, token):
        webhook = get_object_or_404(Webhook, incoming_token=token, is_active=True)
        room = webhook.room

        reject_if_room_archived(room)

        if _incoming_webhook_rate_limited(token):
            return Response(
                {"detail": "Too many requests for this webhook."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        text = (request.data.get("text") or "").strip()
        if not text:
            raise ValidationError({"text": ["This field is required."]})

        reject_if_blocked_keyword(text)

        bot_user = get_webhook_bot_user()
        message = Message.objects.create(user=bot_user, room=room, body=text)
        RoomMembership.objects.get_or_create(room=room, user=bot_user)

        return Response(MessageSerializer(message).data, status=status.HTTP_201_CREATED)

from django.conf import settings
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import serializers

from .signing import sign_attachment_url

from .models import (
    AIInteraction,
    AuditEvent,
    BlockedKeyword,
    Invitation,
    MessageAttachment,
    ModerationAction,
    Notification,
    NotificationPreference,
    PrivateConversation,
    PrivateMessage,
    Reaction,
    Room,
    RoomMembership,
    RoomNotificationSetting,
    Message,
    ThreadFollow,
    Topic,
    UserProfile,
    Webhook,
)


def _avatar_url(user):
    """Best-effort avatar URL lookup; UserProfile is created via signal on
    User creation but stay defensive in case it's missing."""
    profile = getattr(user, "userprofile", None)
    if profile and profile.avatar:
        try:
            return profile.avatar.url
        except ValueError:
            return None
    return None


def _role(user):
    """Best-effort role lookup; UserProfile is created via signal on User
    creation but stay defensive in case it's missing."""
    profile = getattr(user, "userprofile", None)
    return profile.role if profile else UserProfile.ROLE_MEMBER


class UserSerializer(serializers.ModelSerializer):
    """Lightweight user representation for directory listing / mention
    autocomplete / nested display on rooms & messages. Includes `role`
    (workspace-wide owner/admin/member/guest) so the frontend can gate
    admin-only UI (e.g. the invitations panel) without a separate request."""

    avatar = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()
    is_online = serializers.SerializerMethodField()
    # Moderation slice: surfaced here (rather than a separate lookup) so the
    # admin panel's user search / directory can show ban state without an
    # extra round trip. Harmless for every other consumer of this
    # serializer (messages, room members, etc.) — just one more boolean.
    is_banned = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "avatar", "role", "is_online", "is_banned"]

    def get_avatar(self, obj):
        return _avatar_url(obj)

    def get_role(self, obj):
        return _role(obj)

    def get_is_online(self, obj):
        profile = getattr(obj, "userprofile", None)
        return bool(profile and profile.is_online)

    def get_is_banned(self, obj):
        profile = getattr(obj, "userprofile", None)
        return bool(profile and profile.is_banned)


class UserProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = UserProfile
        fields = ["id", "username", "avatar", "avatar_url", "bio"]
        extra_kwargs = {
            "avatar": {"write_only": True, "required": False},
        }

    def get_avatar_url(self, obj):
        return _avatar_url(obj.user)


class TopicSerializer(serializers.ModelSerializer):
    class Meta:
        model = Topic
        fields = ["id", "name"]


class RoomSerializer(serializers.ModelSerializer):
    host = UserSerializer(read_only=True)
    topic = TopicSerializer(read_only=True)
    topic_id = serializers.PrimaryKeyRelatedField(
        queryset=Topic.objects.all(), source="topic", write_only=True
    )
    # Sourced from RoomMembership (the real source of truth for "who's in
    # this room") rather than the legacy `participants` M2M.
    participants = serializers.SerializerMethodField()
    participants_count = serializers.SerializerMethodField()
    # The requesting user's RoomMembership role in this room, or null if
    # they're not a member — lets the frontend gate the room-settings/
    # archive affordance without a second request per room. Needs
    # `request` in serializer context; RoomViewSet (a plain ModelViewSet)
    # already supplies that by default via get_serializer_context().
    my_role = serializers.SerializerMethodField()

    class Meta:
        model = Room
        fields = [
            "id",
            "host",
            "topic",
            "topic_id",
            "name",
            "description",
            "is_private",
            "is_archived",
            "participants",
            "participants_count",
            "my_role",
            "update",
            "created",
        ]
        read_only_fields = ["host", "is_archived", "update", "created"]

    def get_participants(self, obj):
        members = User.objects.filter(room_memberships__room=obj)
        return UserSerializer(members, many=True).data

    def get_participants_count(self, obj):
        return obj.memberships.count()

    def get_my_role(self, obj):
        request = self.context.get("request")
        if request is None or not request.user.is_authenticated:
            return None
        membership = obj.memberships.filter(user=request.user).first()
        return membership.role if membership else None


class RoomMembershipSerializer(serializers.ModelSerializer):
    """A single RoomMembership row: who's in a room and with what per-room
    role. `user_id` is write-only (used by RoomMemberListCreateView.create),
    `user` is the read-only nested representation returned everywhere else."""

    user = UserSerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), source="user", write_only=True
    )

    class Meta:
        model = RoomMembership
        fields = ["id", "user", "user_id", "role", "joined_at"]
        read_only_fields = ["id", "joined_at"]


class MessageAttachmentSerializer(serializers.ModelSerializer):
    """Never exposes the raw storage path (`file`) — attachment files live
    outside anything statically served (see attachment_storage in models.py)
    and are only reachable through a freshly-minted signed, time-limited
    `download_url` (playground/signing.py + AttachmentDownloadView). The
    signature is generated per-request/per-serialization, never stored, so
    every time a message is (re)serialized — including over the WebSocket
    broadcast on send — the client gets a link valid for a fresh window."""

    download_url = serializers.SerializerMethodField()

    class Meta:
        model = MessageAttachment
        fields = [
            "id",
            "download_url",
            "original_filename",
            "content_type",
            "size_bytes",
            "uploaded_at",
        ]
        read_only_fields = fields

    def get_download_url(self, obj):
        token = sign_attachment_url(obj.id)
        path = reverse("api-attachment-download", kwargs={"pk": obj.id}) + f"?sig={token}"
        request = self.context.get("request")
        return request.build_absolute_uri(path) if request is not None else path


class ReplyPreviewSerializer(serializers.ModelSerializer):
    """Compact quoted-message preview for a message's `reply_to` — just
    enough to render the "replying to: ..." chip, not a full nested
    MessageSerializer (which would recurse into its own reply_to_preview,
    reactions, attachments, etc. for no reason)."""

    user = UserSerializer(read_only=True)

    class Meta:
        model = Message
        fields = ["id", "user", "body", "is_deleted"]
        read_only_fields = fields


class MessageSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    mentions = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    reply_to = serializers.PrimaryKeyRelatedField(read_only=True)
    reply_to_preview = serializers.SerializerMethodField()
    reactions = serializers.SerializerMethodField()
    attachments = MessageAttachmentSerializer(many=True, read_only=True)
    # --- threading (see Message.reply_to's docstring) ---
    # `reply_count` is only meaningful/nonzero on a thread root (reply_to is
    # None); a reply's own reply_count is always 0 since replies never have
    # their own replies chained onto them (they resolve onto the root
    # instead). Reads an annotated `reply_count` attribute when the queryset
    # provided one (see MessageListCreateView.get_queryset) to avoid N+1
    # queries on a message list; falls back to a direct count otherwise
    # (single-message responses, the WS broadcast payload, etc).
    # Message.body allows null at the model level (attachment-only sends),
    # which let None through to the API/frontend and crashed message
    # rendering (MentionBadge.tsx's text.split(null)). Coerce to "" here so
    # every response guarantees a string, matching what soft-delete already
    # does (blanks to "", never null).
    body = serializers.CharField(required=False, allow_blank=True, default="")
    reply_count = serializers.SerializerMethodField()
    is_thread_root = serializers.SerializerMethodField()
    # Whether the requesting user currently gets "thread_reply" notifications
    # for this message's thread. Always False for a reply (only roots are
    # followable) and for anonymous/contextless serialization.
    is_following_thread = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            "id",
            "user",
            "room",
            "body",
            "mentions",
            "reply_to",
            "reply_to_preview",
            "reply_count",
            "is_thread_root",
            "is_following_thread",
            "edited_at",
            "is_deleted",
            "deleted_at",
            "reactions",
            "attachments",
            "client_id",
            "update",
            "created",
        ]
        # "room" is read-only here because MessageListCreateView.perform_create
        # (api_views.py) injects it from the URL's room_pk, not from the
        # request body — without this, DRF's default validation demands
        # "room" in every POST payload and rejects otherwise-valid message
        # creates with a 400 "This field is required."
        #
        # "reply_to" is likewise read-only on the serializer itself: it's
        # settable only at creation, and MessageListCreateView.perform_create
        # resolves + validates the incoming `reply_to` id (must reference a
        # message in the same room) before passing it to serializer.save(),
        # the same pattern already used for "room"/"user" here.
        read_only_fields = [
            "user",
            "room",
            "mentions",
            "reply_to",
            "edited_at",
            "is_deleted",
            "deleted_at",
            "update",
            "created",
        ]

    def get_reply_to_preview(self, obj):
        if obj.reply_to_id is None:
            return None
        return ReplyPreviewSerializer(obj.reply_to).data

    def get_reactions(self, obj):
        return obj.reaction_summary()

    def get_reply_count(self, obj):
        annotated = getattr(obj, "reply_count", None)
        if annotated is not None:
            return annotated
        return obj.replies.count()

    def get_is_thread_root(self, obj):
        return obj.reply_to_id is None

    def get_is_following_thread(self, obj):
        if obj.reply_to_id is not None:
            return False
        request = self.context.get("request")
        if request is None or not request.user.is_authenticated:
            return False
        return obj.thread_follows.filter(user_id=request.user.id).exists()


class MessageSearchResultSerializer(serializers.ModelSerializer):
    """Compact message representation for search results (SearchView in
    api_views.py). The full MessageSerializer is overkill here — reactions/
    attachments/thread state aren't needed to jump to a search hit — and,
    more importantly, its `room` field is just a bare PrimaryKeyRelatedField
    (id only), whereas a search result needs the room's *name* too so the
    frontend can label the hit and know what to switch into before it even
    loads that room. Highlighting is deliberately NOT done here: this just
    returns the raw matched `body` and lets the frontend wrap the query
    substring itself (simpler than maintaining snippet/position-marker
    logic on both ends for a plain `icontains` match)."""

    user = UserSerializer(read_only=True)
    room = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = ["id", "user", "room", "body", "created"]
        read_only_fields = fields

    def get_room(self, obj):
        return {"id": obj.room_id, "name": obj.room.name}


class NotificationSerializer(serializers.ModelSerializer):
    sender = UserSerializer(read_only=True)
    recipient = UserSerializer(read_only=True)

    class Meta:
        model = Notification
        fields = [
            "id",
            "recipient",
            "sender",
            "notification_type",
            "message",
            "room",
            "is_read",
            "created_at",
        ]
        read_only_fields = fields


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    """The requesting user's own global per-category toggles — always
    scoped to `request.user` by the view (get_or_create), never addressed
    by id, so there's no `user` field on the wire."""

    class Meta:
        model = NotificationPreference
        fields = ["mentions", "channel_wide", "thread_replies"]


class RoomNotificationSettingSerializer(serializers.ModelSerializer):
    """The requesting user's own per-room override. `room` is read-only —
    it's fixed by the URL (rooms/<room_pk>/notification-setting/), not the
    request body."""

    class Meta:
        model = RoomNotificationSetting
        fields = ["room", "setting"]
        read_only_fields = ["room"]


class PrivateMessageSerializer(serializers.ModelSerializer):
    sender = UserSerializer(read_only=True)

    class Meta:
        model = PrivateMessage
        fields = [
            "id",
            "conversation",
            "sender",
            "content",
            "created_at",
            "client_id",
        ]
        # `conversation` is set server-side by the view (from the URL, via
        # serializer.save(conversation=...)) — it was missing from this
        # list, which made POSTing a message 400 unless the client also
        # supplied a `conversation` field in the body (redundant with the
        # URL and never actually sent by the frontend). Fixed as part of
        # this slice since it blocks message creation outright.
        read_only_fields = ["conversation", "sender", "created_at"]


class PrivateConversationSerializer(serializers.ModelSerializer):
    participants = UserSerializer(many=True, read_only=True)
    last_message = serializers.SerializerMethodField()
    display_name = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()

    class Meta:
        model = PrivateConversation
        fields = [
            "id",
            "participants",
            "last_message",
            "display_name",
            "unread_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_last_message(self, obj):
        last = obj.get_last_message()
        return PrivateMessageSerializer(last).data if last else None

    def _current_user(self):
        request = self.context.get("request")
        return getattr(request, "user", None) if request else None

    def get_display_name(self, obj):
        user = self._current_user()
        return obj.display_name_for(user) if user else None

    def get_unread_count(self, obj):
        user = self._current_user()
        return obj.unread_count_for(user) if user else 0


class InvitationSerializer(serializers.ModelSerializer):
    invited_by = UserSerializer(read_only=True)
    accepted_by = UserSerializer(read_only=True)
    invite_link = serializers.SerializerMethodField()
    is_expired = serializers.BooleanField(read_only=True)

    class Meta:
        model = Invitation
        fields = [
            "id",
            "note",
            "token",
            "invite_link",
            "invited_by",
            "role",
            "created_at",
            "expires_at",
            "accepted_by",
            "accepted_at",
            "is_expired",
        ]
        read_only_fields = [
            "token",
            "invited_by",
            "created_at",
            "expires_at",
            "accepted_by",
            "accepted_at",
            "is_expired",
        ]

    def get_invite_link(self, obj):
        return f"{settings.FRONTEND_URL}/join?token={obj.token}"


# ---------------------------------------------------------------------------
# Admin/moderation slice
# ---------------------------------------------------------------------------


class AuditEventSerializer(serializers.ModelSerializer):
    actor = UserSerializer(read_only=True)
    target_user = UserSerializer(read_only=True)
    target_room_name = serializers.SerializerMethodField()

    class Meta:
        model = AuditEvent
        fields = [
            "id",
            "actor",
            "action",
            "target_user",
            "target_room",
            "target_room_name",
            "target_message",
            "detail",
            "created_at",
        ]
        read_only_fields = fields

    def get_target_room_name(self, obj):
        # None once the room itself has been deleted (target_room is
        # SET_NULL) — `detail` is where the room's name survives that, see
        # AuditEvent's docstring.
        return obj.target_room.name if obj.target_room_id else None


class ModerationActionSerializer(serializers.ModelSerializer):
    target_user = UserSerializer(read_only=True)
    issued_by = UserSerializer(read_only=True)

    class Meta:
        model = ModerationAction
        fields = ["id", "target_user", "action_type", "issued_by", "reason", "created_at", "expires_at"]
        read_only_fields = fields


class BlockedKeywordSerializer(serializers.ModelSerializer):
    added_by = UserSerializer(read_only=True)

    class Meta:
        model = BlockedKeyword
        fields = ["id", "phrase", "added_by", "created_at"]
        read_only_fields = ["id", "added_by", "created_at"]


class AIInteractionSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    room_name = serializers.SerializerMethodField()

    class Meta:
        model = AIInteraction
        fields = [
            "id",
            "user",
            "room",
            "room_name",
            "trigger_type",
            "prompt_context_message_count",
            "response_message",
            "succeeded",
            "created_at",
        ]
        read_only_fields = fields

    def get_room_name(self, obj):
        # None once the room itself has been deleted — room is CASCADE here
        # (unlike AuditEvent's SET_NULL target_room), so this is mostly
        # defensive, but cheap to guard the same way.
        return obj.room.name if obj.room_id else None


class WebhookSerializer(serializers.ModelSerializer):
    """Management-facing representation of a Webhook (list/create/delete —
    see api_views.py's WebhookListCreateView/WebhookDetailView).

    Deliberately does NOT expose `secret` at all — unlike Invitation.token
    (always readable), the outgoing signing secret follows the real "shown
    once" pattern: WebhookListCreateView.create() adds it into the response
    dict by hand, only for the single create-response, after this
    serializer has already produced everything else. Any later GET/list of
    this same row goes through this serializer alone and never includes it.

    `incoming_url` (unlike `incoming_token` alone) is exposed on every read
    exactly like Invitation.invite_link — it's the ready-to-paste URL an
    external service POSTs to, so nothing about it needs one-time-only
    treatment.
    """

    created_by = UserSerializer(read_only=True)
    incoming_url = serializers.SerializerMethodField()

    class Meta:
        model = Webhook
        fields = [
            "id",
            "room",
            "target_url",
            "event_types",
            "is_active",
            "created_by",
            "created_at",
            "incoming_token",
            "incoming_url",
        ]
        read_only_fields = ["room", "created_by", "created_at", "incoming_token", "incoming_url"]

    def get_incoming_url(self, obj):
        request = self.context.get("request")
        path = f"/api/v1/webhooks/incoming/{obj.incoming_token}/"
        return request.build_absolute_uri(path) if request else path

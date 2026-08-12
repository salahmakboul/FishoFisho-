from django.db import models
import secrets
import uuid
from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.storage import FileSystemStorage
from django.utils import timezone
import re

# Create your models here.

#topic model
class Topic(models.Model):
    name=models.CharField(max_length=200)#topic name
    def __str__(self):#string representation
        return self.name


#room model
class Room(models.Model):
    host=models.ForeignKey(User,on_delete=models.SET_NULL,null=True)#each room has one host
    topic =models.ForeignKey(Topic,on_delete=models.SET_NULL,null=True)#each room has one topic
    name=models.CharField(max_length=200)
    description=models.TextField(null=True, blank=True)
    is_private = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)
    # Legacy plain M2M — kept only so any code/data relying on the bare
    # `participants` relation doesn't break. The real source of truth for
    # "who's in this room, and with what per-room role" is RoomMembership
    # below; RoomViewSet/consumers.py/MessageListCreateView all go through
    # RoomMembership now. Do not add new code that reads/writes this field.
    participants=models.ManyToManyField(User,
     related_name="participants" ,
     blank=True)
    update=models.DateTimeField(auto_now=True)
    created=models.DateTimeField(auto_now_add=True)
    class Meta:#metadata
        ordering=['-update','-created']#order by update and created time descending

    def __str__(self):#string representation
        return self.name#return the room name

    def is_member(self, user):
        if user is None or user.is_anonymous:
            return False
        return self.memberships.filter(user=user).exists()


class RoomMembership(models.Model):
    """Per-room membership, replacing bare `Room.participants` as the real
    source of truth for "who's in this room". Distinct from the workspace-
    wide UserProfile.role (owner/admin/member/guest) — this is scoped to a
    single room, e.g. so a room creator can manage that room without being
    a workspace admin."""

    ROLE_CHOICES = [
        ("admin", "Admin"),
        ("member", "Member"),
    ]

    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="room_memberships")
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default="member")
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("room", "user")
        ordering = ["joined_at"]

    def __str__(self):
        return f"{self.user.username} in {self.room.name} ({self.role})"

# MOVE NOTIFICATION MODEL BEFORE MESSAGE MODEL

class Notification(models.Model):
    NOTIFICATION_TYPES = (
        ('mention', 'Mention'),
        ('message', 'Message'),
        ('follow', 'Follow'),
        ('like', 'Like'),
        # threading slice: fired at every follower of a thread (except the
        # replier themselves) when a new reply is posted — see ThreadFollow
        # below and MessageListCreateView.perform_create's
        # _notify_thread_followers.
        ('thread_reply', 'Thread Reply'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_notifications')
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES)
    message = models.TextField()
    room = models.ForeignKey('Room', on_delete=models.SET_NULL, null=True, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.sender.username} → {self.recipient.username}: {self.notification_type}"

# THEN KEEP THE MESSAGE MODEL
class Message(models.Model) :
    user=models.ForeignKey(User,on_delete=models.CASCADE)#each message has one user
    room=models.ForeignKey(Room,on_delete=models.CASCADE)#each message has one room
    body=models.TextField(null=True,blank=True)
    update=models.DateTimeField(auto_now=True)
    created=models.DateTimeField(auto_now_add=True)
    mentions = models.ManyToManyField(User, related_name='mentioned_in', blank=True)

    # --- edit/delete (messaging slice) ---
    # edited_at stays null until the author's first PATCH; the frontend uses
    # its presence to show "(edited)". Soft delete (is_deleted/deleted_at)
    # rather than a hard row delete preserves reply_to references (below)
    # and room history integrity — deleted messages still show up in
    # listings as a blanked-body placeholder, matching Slack's "this
    # message was deleted".
    edited_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    # --- reply / threading ---
    # SET_NULL so deleting the original message doesn't cascade-delete every
    # reply to it; only settable at creation (enforced in api_views.py, not
    # here) so there's no "move a reply later" concept to worry about.
    #
    # Threading slice: `reply_to` always points at the THREAD ROOT, not just
    # the immediately-preceding message. MessageListCreateView._resolve_reply_to
    # walks up one level when the target being replied to is itself already a
    # reply, so a "reply to a reply" flattens onto the same root instead of
    # building a multi-level chain. That's what makes "this thread's replies"
    # and "this message's reply count" simple flat queries
    # (`Message.objects.filter(reply_to_id=root_id)` / `root.replies.count()`)
    # instead of needing a recursive tree walk. A message with `reply_to=None`
    # is either an ordinary message or a thread root (has 1+ `replies`); see
    # MessageSerializer's `is_thread_root`/`reply_count`.
    reply_to = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replies",
    )

    # --- idempotent sends (real-time infra hardening slice) ---
    # Client-generated key (e.g. crypto.randomUUID()) sent alongside a
    # message create. Nullable/optional so existing callers that never send
    # one keep working unchanged. api_views.py's perform_create checks for
    # an existing row with the same (user, client_id) before creating a new
    # one, so retrying a POST after a flaky network drop is safe instead of
    # producing a duplicate message. Not a DB-level unique constraint —
    # dedup is an application-level best-effort check, not a hard guarantee
    # under concurrent retries.
    client_id = models.CharField(max_length=64, null=True, blank=True, db_index=True)

    class Meta:
        ordering=['-update','-created']

    def __str__(self):#string representation
        return (self.body or "")[:50]#return first 50 characters of the message body

    def save(self, *args, **kwargs):
        # Only run mention/@channel/@here notification fan-out on the
        # message's *first* save. Message.save() used to run check_mentions()
        # unconditionally, which was fine when there was no way to re-save an
        # existing message — now that edit (PATCH) and soft-delete both call
        # .save() again on an existing row, re-running it on every save would
        # re-notify every mentioned user on every edit. Judgment call: treat
        # mentions as fixed at creation time; editing a message never
        # re-triggers notifications.
        is_new = self._state.adding
        super().save(*args, **kwargs)
        if is_new:
            self.check_mentions()

    def check_mentions(self):
        """Extract @mentions from message body and create notifications.

        Also recognizes the broadcast tokens @channel / @here (case
        insensitive) — either one fans a notification out to every member of
        the room (via RoomMembership) except the sender. There's no
        presence/online-tracking in this app yet, so @here is simplified to
        behave identically to @channel for now rather than half-building
        presence-awareness (see task notes).
        """
        if not self.body:
            return

        tokens = re.findall(r'@(\w+)', self.body)

        # Clear existing per-user mentions (kept exactly as before for
        # create-time behavior).
        self.mentions.clear()

        broadcast_tokens = {"channel", "here"}
        notify_channel = False
        seen_usernames = set()

        for token in tokens:
            if token.lower() in broadcast_tokens:
                notify_channel = True
                continue
            if token in seen_usernames:
                continue
            seen_usernames.add(token)
            try:
                user = User.objects.get(username=token)
            except User.DoesNotExist:
                continue
            if user == self.user:  # Don't notify self-mentions
                continue
            # `mentions` M2M always records who was @named (used for the
            # frontend's highlight/UI), independent of whether a
            # Notification row is actually created for them below.
            self.mentions.add(user)
            if not notification_allowed(user, self.room, "mentions"):
                continue
            Notification.objects.create(
                recipient=user,
                sender=self.user,
                notification_type='mention',
                message=f"{self.user.username} mentioned you in a message",
                room=self.room,
                is_read=False,
            )

        if notify_channel:
            members = User.objects.filter(
                id__in=RoomMembership.objects.filter(room=self.room)
                .exclude(user=self.user)
                .values_list("user_id", flat=True)
            )
            for member in members:
                if not notification_allowed(member, self.room, "channel_wide"):
                    continue
                Notification.objects.create(
                    recipient=member,
                    sender=self.user,
                    notification_type='mention',
                    message=f"{self.user.username} notified the channel",
                    room=self.room,
                    is_read=False,
                )

    def reaction_summary(self):
        """[{"emoji": "...", "count": N, "user_ids": [...]}], grouped from
        this message's Reaction rows. `user_ids` (rather than a single
        `reacted_by_me` bool) is used so the exact same payload can be
        broadcast to every client in the room over the channel layer
        (signals.py) — each client derives "did I react?" locally by
        checking its own user id against the list, instead of the server
        trying to personalize a group-broadcast payload per recipient."""
        from collections import defaultdict

        grouped = defaultdict(list)
        for user_id, emoji in self.reactions.order_by("created_at").values_list("user_id", "emoji"):
            grouped[emoji].append(user_id)
        return [
            {"emoji": emoji, "count": len(user_ids), "user_ids": user_ids}
            for emoji, user_ids in grouped.items()
        ]


class NotificationPreference(models.Model):
    """Per-user GLOBAL on/off toggle for each notification category this app
    actually generates today (see Notification.NOTIFICATION_TYPES and every
    place a Notification row gets created — Message.check_mentions below for
    'mentions'/'channel_wide', MessageListCreateView._handle_thread_reply in
    api_views.py for 'thread_replies').

    Direct messages (PrivateMessage) deliberately do NOT get a category here
    (and don't create Notification rows at all — see PrivateMessage's
    docstring/signals.py's push_private_message_to_participants): DMs are
    already surfaced live via the "private_message.new" WS event plus each
    conversation's own unread badge (ConversationRead), which is a complete,
    always-on notification path with no per-category on/off to make sense
    of yet. Judgment call: scope this preferences slice to the categories
    that actually flow through the Notification model rather than bolting a
    new Notification-row path onto DMs just to have something to toggle.

    Deliberately just three booleans rather than Slack's fuller "every
    message / mentions & keywords / nothing" per-channel matrix — a global
    per-category on/off, with room-level nuance layered on by
    RoomNotificationSetting below, covers what this slice needs.

    Rows are created lazily via `for_user()` (get-or-create) rather than
    backfilled for every existing user by a data migration — cheaper, and
    a brand new user never needs a migration rerun to get sane defaults.
    Defaults are all True ("on") specifically so earlier slices' tests
    (which assert notifications ARE created for mentions/@channel/thread
    replies) keep passing unmodified against a user who has no preference
    row yet.
    """

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="notification_preference"
    )
    mentions = models.BooleanField(default=True)
    channel_wide = models.BooleanField(default=True)
    thread_replies = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.user.username}'s notification preferences"

    @classmethod
    def for_user(cls, user):
        pref, _ = cls.objects.get_or_create(user=user)
        return pref


class RoomNotificationSetting(models.Model):
    """One user's personal override for one specific room — mute it,
    restrict it to mentions-only, force it to "all", or (the default)
    fall back to that user's global NotificationPreference. This is a
    personal preference like RoomNotificationSetting's sibling
    NotificationPreference, NOT room administration — any room member can
    set their own, not just that room's admins (see RoomSettingsPanel.tsx
    on the frontend, which deliberately does not gate this section behind
    the room-admin check that gates the rest of that panel)."""

    DEFAULT = "default"
    ALL = "all"
    MENTIONS_ONLY = "mentions_only"
    MUTED = "muted"
    SETTING_CHOICES = [
        (DEFAULT, "Default (use global preference)"),
        (ALL, "All activity"),
        (MENTIONS_ONLY, "Mentions only"),
        (MUTED, "Muted"),
    ]

    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="notification_settings")
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="room_notification_settings"
    )
    setting = models.CharField(max_length=15, choices=SETTING_CHOICES, default=DEFAULT)

    class Meta:
        unique_together = ("room", "user")

    def __str__(self):
        return f"{self.user.username}'s notifications in {self.room.name}: {self.setting}"


def notification_allowed(recipient, room, category):
    """Whether `recipient` should get a Notification row for `category`
    ("mentions" | "channel_wide" | "thread_replies") triggered in `room`.

    Precedence (room override wins over global, mentions bypass a mute):
      1. Look up `recipient`'s RoomNotificationSetting for `room`, if any.
         A row that's missing, or explicitly "default", means no override
         — fall straight through to the global NotificationPreference.
      2. An override of "all" allows every category for that room,
         regardless of the global preference (room override > global).
      3. An override of "muted" or "mentions_only" blocks non-mention
         categories outright, but for the "mentions" category specifically
         falls back to the global `mentions` preference instead of an
         absolute block — realistic Slack-like behavior: muting a room
         silences general noise, not someone directly naming you, UNLESS
         the user has ALSO turned mentions off globally.
    """
    pref = NotificationPreference.for_user(recipient)
    global_allowed = getattr(pref, category)

    override = None
    if room is not None:
        override = (
            RoomNotificationSetting.objects.filter(room=room, user=recipient)
            .exclude(setting=RoomNotificationSetting.DEFAULT)
            .first()
        )

    if override is None:
        return global_allowed

    if override.setting == RoomNotificationSetting.ALL:
        return True

    if override.setting in (RoomNotificationSetting.MUTED, RoomNotificationSetting.MENTIONS_ONLY):
        if category == "mentions":
            return global_allowed
        return False

    return global_allowed


class ThreadFollow(models.Model):
    """`user` wants "thread_reply" notifications for the thread rooted at
    `root_message`. Deliberately its own tiny model rather than reusing/
    overloading RoomMembership or Notification — "who's watching this one
    thread" is orthogonal to "who's in this room".

    Rows are created automatically (not by explicit user action) for the
    thread root's author and for anyone who posts a reply — see
    MessageListCreateView.perform_create's `_handle_thread_reply` — and can
    additionally be toggled by hand via
    POST/DELETE /api/v1/rooms/<room_pk>/messages/<id>/follow/ for someone who
    wants updates without having posted yet. `root_message` is always the
    thread's root (never a reply) — enforced by the view layer that creates
    these rows resolving to the root first, same as `Message.reply_to` does.
    """

    root_message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="thread_follows")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="followed_threads")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("root_message", "user")
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.user.username} follows thread {self.root_message_id}"


class Reaction(models.Model):
    """One user's emoji reaction to a message. `emoji` stores the literal
    emoji character (e.g. "👍") sent by the frontend — simpler than a
    shortcode table since there's a small curated picker on the frontend
    (see MessageActions.tsx), not free-form emoji entry needing shortcode
    normalization. unique_together enforces "one reaction per emoji per
    user" so POSTing the same emoji again toggles it off."""

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="reactions")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="reactions")
    emoji = models.CharField(max_length=16)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("message", "user", "emoji")
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.user.username} reacted {self.emoji} to message {self.message_id}"


def _attachment_upload_to(instance, filename):
    return f"attachments/{filename}"


# Matches path separators, control/newline characters, and quotes — the
# characters that would either let a stored filename escape its directory
# (path traversal) or break out of the Content-Disposition header value it's
# later interpolated into (AttachmentDownloadView). Applied once when the
# filename is first stored (api_views.py) and defensively again wherever
# it's put into a header, so a bad value can never sneak in through either
# path.
_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/\r\n"\x00-\x1f]')


def sanitize_attachment_filename(name):
    """Reduce an untrusted uploaded filename to something safe to store and
    to later echo back verbatim in a Content-Disposition header. Strips any
    directory component (os.path.basename-style, via rsplit on both
    separators) and replaces unsafe characters rather than rejecting the
    upload outright, since the file's content — not its filename — is what
    actually matters."""
    name = (name or "").strip()
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = _UNSAFE_FILENAME_CHARS.sub("_", name)
    name = name[:255]
    return name or "attachment"


# Message attachments live in their OWN storage location, ATTACHMENTS_ROOT
# (settings.py), deliberately outside MEDIA_ROOT — MEDIA_ROOT is still
# served directly at /media/... (see playground/urls.py), which is fine for
# avatars but would be exactly the unauthenticated-access gap this closes
# if attachments lived there too. The only way to read an attachment file is
# through AttachmentDownloadView (api_views.py): signed URL + auth + room
# membership check. See playground/signing.py and the ATTACHMENTS_ROOT
# comment in fishofisho/settings.py for the full rationale.
attachment_storage = FileSystemStorage(location=settings.ATTACHMENTS_ROOT)


class MessageAttachment(models.Model):
    """Basic multipart file attachment on a message. Size/content-type are
    validated server-side in api_views.py before this row is created; the
    file itself is only ever readable through a signed, permission-checked
    download URL (AttachmentDownloadView) — never a raw, guessable media
    path. See `attachment_storage` above."""

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to=_attachment_upload_to, storage=attachment_storage)
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size_bytes = models.PositiveIntegerField()
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]

    def __str__(self):
        return self.original_filename

class UserProfile(models.Model):
    ROLE_OWNER = "owner"
    ROLE_ADMIN = "admin"
    ROLE_MEMBER = "member"
    ROLE_GUEST = "guest"
    ROLE_CHOICES = [
        (ROLE_OWNER, "Owner"),
        (ROLE_ADMIN, "Admin"),
        (ROLE_MEMBER, "Member"),
        (ROLE_GUEST, "Guest"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    bio = models.TextField(max_length=500, blank=True)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_MEMBER)
    # Minimal presence tracking (see StreamConsumer.connect/disconnect + the
    # "presence.ping" message type): updated on WS connect, on each
    # heartbeat ping the client already sends to keep the socket alive, and
    # on disconnect. "Online" is derived (see `is_online` below) rather than
    # stored as a boolean, since a DB-backed timestamp survives worker
    # restarts/crashes (an ungraceful disconnect never fires disconnect())
    # in a way a bare in-memory flag couldn't.
    last_seen_at = models.DateTimeField(null=True, blank=True)

    # How long after the last heartbeat a user still counts as "online" —
    # roughly 1.5x the ~30s heartbeat interval the frontend's connectStream
    # already uses, so a couple of missed beats doesn't flicker someone
    # offline.
    ONLINE_WINDOW_SECONDS = 45

    # --- moderation slice: derived ban/timeout state ---
    # Both are set directly by the view that creates the corresponding
    # ModerationAction (ModerationActionView/UnbanView in api_views.py)
    # rather than via a signal — simpler or a `ModerationAction.save()`
    # override, since only one code path ever creates these rows and the
    # view already has both the profile and the action in hand.
    is_banned = models.BooleanField(default=False)
    timed_out_until = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.username}'s Profile"

    @property
    def is_admin_or_owner(self):
        return self.role in (self.ROLE_OWNER, self.ROLE_ADMIN)

    @property
    def is_online(self):
        if not self.last_seen_at:
            return False
        from django.utils import timezone

        return (timezone.now() - self.last_seen_at).total_seconds() <= self.ONLINE_WINDOW_SECONDS


def _default_invitation_expiry():
    return timezone.now() + timezone.timedelta(days=7)


class Invitation(models.Model):
    """A single-use, unguessable link an admin/owner can generate to bring
    a new person into the (single, implicit) workspace with a pre-assigned
    role. This app doesn't collect email at registration (RegisterView uses
    plain UserCreationForm — username + password only) and has no existing
    "invite someone by address" concept anywhere, so there's no real target
    identity to bind the invite to ahead of time. `note` is therefore just
    an optional free-text label the inviter can leave themselves (e.g. "for
    Sam") — purely a reminder, never checked against the accepting user."""

    note = models.CharField(max_length=200, blank=True)
    token = models.CharField(max_length=64, unique=True, editable=False)
    invited_by = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="sent_invitations"
    )
    role = models.CharField(
        max_length=10, choices=UserProfile.ROLE_CHOICES, default=UserProfile.ROLE_MEMBER
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=_default_invitation_expiry)
    accepted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="accepted_invitations",
    )
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        status = "accepted" if self.accepted_by_id else "pending"
        return f"Invitation by {self.invited_by.username} ({self.role}, {status})"

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def is_accepted(self):
        return self.accepted_by_id is not None

    @property
    def is_valid(self):
        return not self.is_accepted and not self.is_expired

    def accept(self, user):
        self.accepted_by = user
        self.accepted_at = timezone.now()
        self.save(update_fields=["accepted_by", "accepted_at"])

AI_BOT_USERNAME = "FishoAI"
class PrivateConversation(models.Model):
    """Conversation between two users"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    participants = models.ManyToManyField(User, related_name='private_conversations')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-updated_at']
    
    def __str__(self):
        usernames = [user.username for user in self.participants.all()]
        return f"Chat between {', '.join(usernames)}"
    
    def get_other_user(self, current_user):
        """Get the other user in the conversation. Only meaningful for
        exactly-2-participant (1:1) conversations — for groups, use
        `other_participants`/`display_name_for` instead."""
        return self.participants.exclude(id=current_user.id).first()

    def other_participants(self, current_user):
        """Every participant except `current_user` — the group-aware
        generalization of `get_other_user`."""
        return self.participants.exclude(id=current_user.id)

    def display_name_for(self, current_user):
        """Slack-style derived name: the other participant's username for a
        1:1 conversation, or a comma-joined list of usernames for a group.
        Falls back to all participants if `current_user` isn't one of them
        (shouldn't normally happen given view-level permission checks)."""
        others = list(self.other_participants(current_user)) if current_user else []
        if not others:
            others = list(self.participants.all())
        return ", ".join(u.username for u in others)

    def get_last_message(self):
        """Get the last message in this conversation"""
        return self.messages.last()

    def unread_count_for(self, user):
        """Real per-participant unread count (see ConversationRead below):
        messages in this conversation, not sent by `user`, created after
        `user`'s last recorded read point. No ConversationRead row yet means
        nothing has ever been marked read, so every non-own message counts."""
        qs = self.messages.exclude(sender=user)
        read_state = self.read_states.filter(user=user).first()
        if read_state and read_state.last_read_at:
            qs = qs.filter(created_at__gt=read_state.last_read_at)
        return qs.count()

    def mark_read_for(self, user, when=None):
        """Record that `user` has read this conversation up to `when`
        (defaults to now)."""
        from django.utils import timezone

        ConversationRead.objects.update_or_create(
            conversation=self, user=user, defaults={"last_read_at": when or timezone.now()}
        )

class PrivateMessage(models.Model):
    """Private message. Belongs to a conversation; the set of recipients is
    derived from `conversation.participants` (minus the sender) rather than
    a single `receiver` FK, so this model works for both 1:1 DMs and group
    conversations. Per-participant read state lives on ConversationRead, not
    here (a single message-level is_read boolean doesn't make sense once a
    message can have more than one recipient)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(PrivateConversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_private_messages')
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    # Same idempotency-key mechanism as Message.client_id above — see that
    # field's docstring.
    client_id = models.CharField(max_length=64, null=True, blank=True, db_index=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.sender.username}: {self.content[:50]}"


class ConversationRead(models.Model):
    """Per-participant read tracking for PrivateConversation. One row per
    (conversation, user) holding the timestamp up to which that user has
    read the conversation — chosen over a `PrivateMessage.read_by` M2M
    because "how many unread messages does user X have in conversation Y"
    only needs a single indexed timestamp comparison (`created_at__gt`) per
    conversation instead of an M2M row per (message, reader) pair, and this
    is a chat app where unread *counts* (not exact per-message read
    receipts/checkmarks) are the actual UI need — see
    PrivateConversation.unread_count_for/mark_read_for above."""

    conversation = models.ForeignKey(PrivateConversation, on_delete=models.CASCADE, related_name='read_states')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='conversation_read_states')
    last_read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [('conversation', 'user')]

    def __str__(self):
        return f"{self.user.username} read {self.conversation_id} up to {self.last_read_at}"


# ---------------------------------------------------------------------------
# Admin/moderation slice
# ---------------------------------------------------------------------------


class AuditEvent(models.Model):
    """Immutable trail of every admin/moderation action taken in the
    workspace — who did what, to whom/what, and when. Written via
    `playground.audit.log_audit_event` from the existing admin-gated views
    (RoomMemberDetailView, RoomViewSet, MessageDetailView, InvitationViewSet)
    plus the new moderation/blocked-keyword views below, rather than each
    view hand-rolling its own AuditEvent.objects.create() call.

    All three target FKs are SET_NULL: deleting the room/user/message a past
    action referred to must never delete (or cascade-break) the audit row
    itself — `detail` carries a short human-readable summary (e.g. "role:
    member -> admin", "room 'General' deleted") specifically so the event
    still reads sensibly even after its target is long gone.
    """

    ACTION_ROLE_CHANGED = "role_changed"
    ACTION_MEMBER_REMOVED = "member_removed"
    ACTION_ROOM_ARCHIVED = "room_archived"
    ACTION_ROOM_DELETED = "room_deleted"
    ACTION_MESSAGE_REMOVED = "message_removed"
    ACTION_USER_WARNED = "user_warned"
    ACTION_USER_TIMED_OUT = "user_timed_out"
    ACTION_USER_BANNED = "user_banned"
    ACTION_USER_UNBANNED = "user_unbanned"
    ACTION_INVITATION_CREATED = "invitation_created"
    ACTION_INVITATION_REVOKED = "invitation_revoked"
    ACTION_KEYWORD_ADDED = "keyword_added"
    ACTION_KEYWORD_REMOVED = "keyword_removed"
    ACTION_CHOICES = [
        (ACTION_ROLE_CHANGED, "Role changed"),
        (ACTION_MEMBER_REMOVED, "Member removed"),
        (ACTION_ROOM_ARCHIVED, "Room archived"),
        (ACTION_ROOM_DELETED, "Room deleted"),
        (ACTION_MESSAGE_REMOVED, "Message removed"),
        (ACTION_USER_WARNED, "User warned"),
        (ACTION_USER_TIMED_OUT, "User timed out"),
        (ACTION_USER_BANNED, "User banned"),
        (ACTION_USER_UNBANNED, "User unbanned"),
        (ACTION_INVITATION_CREATED, "Invitation created"),
        (ACTION_INVITATION_REVOKED, "Invitation revoked"),
        (ACTION_KEYWORD_ADDED, "Blocked keyword added"),
        (ACTION_KEYWORD_REMOVED, "Blocked keyword removed"),
    ]

    actor = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_events_performed"
    )
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    target_user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_events_targeting"
    )
    target_room = models.ForeignKey(
        Room, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_events"
    )
    target_message = models.ForeignKey(
        Message, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_events"
    )
    detail = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} by {self.actor_id} @ {self.created_at}"


class ModerationAction(models.Model):
    """One warning/timeout/ban/unban issued against a user. `expires_at` is
    only meaningful for "timeout" (when the timeout lifts — mirrored onto
    UserProfile.timed_out_until at issue time, see ModerationActionView);
    warnings and bans are left permanent/non-expiring here (a ban is lifted
    by a separate explicit "unban" row, not by this expiring)."""

    WARNING = "warning"
    TIMEOUT = "timeout"
    BAN = "ban"
    UNBAN = "unban"
    ACTION_TYPE_CHOICES = [
        (WARNING, "Warning"),
        (TIMEOUT, "Timeout"),
        (BAN, "Ban"),
        (UNBAN, "Unban"),
    ]

    target_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="moderation_actions_received")
    action_type = models.CharField(max_length=10, choices=ACTION_TYPE_CHOICES)
    issued_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="moderation_actions_issued"
    )
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action_type} for {self.target_user.username}"


class BlockedKeyword(models.Model):
    """Workspace-wide substring filter: any message whose body contains
    `phrase` (case-insensitive) is rejected outright at creation time — see
    MessageListCreateView.perform_create. Deliberately just a flat phrase
    list with plain substring matching, not a real spam-detection system
    (see task notes) — admin/owner manageable via
    /api/v1/admin/blocked-keywords/."""

    phrase = models.CharField(max_length=200)
    added_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.phrase


class AIInteraction(models.Model):
    """Audit trail for every FishoAI invocation (playground/ai_helper.py),
    successful or not — the AI slice's equivalent of AuditEvent for
    admin/moderation actions. Written by
    MessageListCreateView._maybe_trigger_ai_reply on every trigger (mention,
    the pre-existing 20% random reply, or the new "summarize" command), so
    an admin can answer "who has been invoking the AI, in which rooms, how
    often, and did it actually work" via GET /api/v1/admin/ai-interactions/.

    Deliberately does NOT store the full prompt/raw context text sent to
    Gemini or the full response body (that's already preserved verbatim as
    `response_message.body` for successful replies, and re-storing it here
    would just bloat this table with a second copy) — instead it records
    `prompt_context_message_count`, an auditable *measure* of what the AI
    was shown (how many of this room's own messages were included as
    context) without duplicating the content itself. `trigger_message` and
    `response_message` are SET_NULL so this audit row survives message
    deletion, matching AuditEvent's target-FK pattern.
    """

    TRIGGER_MENTION = "mention"
    TRIGGER_RANDOM = "random"
    TRIGGER_SUMMARIZE = "summarize"
    TRIGGER_TYPE_CHOICES = [
        (TRIGGER_MENTION, "Mention"),
        (TRIGGER_RANDOM, "Random"),
        (TRIGGER_SUMMARIZE, "Summarize"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="ai_interactions")
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="ai_interactions")
    trigger_message = models.ForeignKey(
        Message, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    trigger_type = models.CharField(max_length=10, choices=TRIGGER_TYPE_CHOICES)
    prompt_context_message_count = models.PositiveIntegerField(default=0)
    response_message = models.ForeignKey(
        Message, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    succeeded = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.trigger_type} by {self.user.username} in {self.room.name} ({'ok' if self.succeeded else 'failed'})"


class Webhook(models.Model):
    """The generic integration boundary this slice adds: a room-scoped
    primitive covering BOTH directions of webhook traffic, so a future
    GitHub/Jira/calendar-style integration can plug into it later instead
    of each one hand-rolling its own event dispatch/auth from scratch. One
    row does double duty:

      - OUTGOING: if `target_url` is set and `is_active` is True, an HTTP
        POST fires to `target_url` (HMAC-SHA256 signed with `secret`, see
        signals.py's dispatch_outgoing_webhooks) whenever one of this
        room's events in `event_types` happens. Only "message.created" is
        actually wired up today (hooked into the existing Message post_save
        signal — see signals.py's push_message_to_room_group), but
        `event_types` is stored as a list specifically so more event types
        can be added later without a schema migration.
      - INCOMING: `incoming_token` is a unique unguessable token an
        external service (no FishoFisho login) POSTs to at
        /api/v1/webhooks/incoming/<token>/ to create a message in this
        room — see api_views.py's IncomingWebhookView.

    A room can have several Webhook rows (e.g. one per external service).
    `secret` is generated once and never returned by the API after the
    initial create response — the same "shown once" pattern real platforms
    (Stripe, GitHub) use for signing secrets, though enforced here only at
    the serializer layer (see WebhookSerializer), not the DB.
    """

    EVENT_MESSAGE_CREATED = "message.created"
    KNOWN_EVENT_TYPES = [EVENT_MESSAGE_CREATED]

    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="webhooks")
    target_url = models.URLField(blank=True)
    secret = models.CharField(max_length=64, editable=False)
    event_types = models.JSONField(default=list, blank=True)
    incoming_token = models.CharField(max_length=64, unique=True, editable=False)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_webhooks"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.secret:
            self.secret = secrets.token_urlsafe(32)
        if not self.incoming_token:
            self.incoming_token = secrets.token_urlsafe(24)
        if not self.event_types:
            self.event_types = [self.EVENT_MESSAGE_CREATED]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Webhook(room={self.room_id}, url={self.target_url or '(incoming only)'})"


class WebhookDelivery(models.Model):
    """One row per outgoing delivery ATTEMPT (success or failure) — the
    auditability/observability half of the outgoing-webhook feature. Written
    by signals.py's dispatch_outgoing_webhooks for every attempt, wrapped in
    a try/except there so a delivery-recording bug can never be the reason a
    delivery attempt goes unaudited."""

    webhook = models.ForeignKey(Webhook, on_delete=models.CASCADE, related_name="deliveries")
    event_type = models.CharField(max_length=64)
    payload = models.JSONField()
    response_status = models.IntegerField(null=True, blank=True)
    succeeded = models.BooleanField(default=False)
    error = models.TextField(blank=True)
    attempted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-attempted_at"]

    def __str__(self):
        return f"Delivery({self.event_type}, {'ok' if self.succeeded else 'failed'})"

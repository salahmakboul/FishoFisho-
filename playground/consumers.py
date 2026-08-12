from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.utils import timezone

from .models import Room, UserProfile
from .permissions import user_can_access_room


class StreamConsumer(AsyncJsonWebsocketConsumer):
    """Single WebSocket connection per authenticated user.

    On connect the socket joins a personal group ``user_<id>`` so
    notifications and @mentions (pushed via signals.py using the channel
    layer) reach the user regardless of which room they're viewing.

    Clients additionally join/leave per-room groups (``room_<id>``) to
    receive live messages for whatever room(s) they currently have open.

    Rooms can now be private (`Room.is_private`). Public rooms still work
    exactly as before — any authenticated user may join their group.
    Private rooms require the joining user to have a RoomMembership row for
    that room; anyone else's join attempt gets a "Room not found" error,
    matching the 404 the REST API returns for the same case (not 403) so a
    non-member can't distinguish "doesn't exist" from "exists but private").
    """

    async def connect(self):
        user = self.scope.get("user")
        if user is None or user.is_anonymous:
            await self.close()
            return

        # Moderation slice: a banned user's session cookie may still be
        # valid (Django doesn't invalidate other sessions on a flag flip —
        # see UserProfile.is_banned/IsAuthenticatedNotBanned in
        # permissions.py for the REST-layer half of this same enforcement),
        # so re-check here too rather than only relying on DRF permissions,
        # which don't gate WebSocket connections at all. Does not forcibly
        # close an ALREADY-connected banned user's existing socket — only
        # new connect attempts are rejected.
        if await self._is_banned(user.id):
            await self.close()
            return

        self.user_group = f"user_{user.id}"
        self.joined_rooms = set()
        self.user_id = user.id

        await self.channel_layer.group_add(self.user_group, self.channel_name)
        await self.accept()
        await self._touch_presence()

    async def disconnect(self, close_code):
        if getattr(self, "user_group", None):
            await self.channel_layer.group_discard(self.user_group, self.channel_name)
        for room_group in getattr(self, "joined_rooms", set()):
            await self.channel_layer.group_discard(room_group, self.channel_name)
        # Best-effort: clear last_seen_at so this user reads as offline
        # immediately on a clean disconnect, rather than waiting out the
        # ONLINE_WINDOW_SECONDS grace period. An ungraceful drop (process
        # kill, network blackhole) may not fire this at all — in that case
        # presence just falls back to the timestamp aging out naturally.
        if getattr(self, "user_id", None) is not None:
            await self._clear_presence()

    async def receive_json(self, content, **kwargs):
        msg_type = content.get("type")

        if msg_type == "ping":
            # Application-level heartbeat (see frontend lib/api.ts's
            # connectStream): browsers don't surface low-level WS ping/pong
            # to JS, and some proxies/NATs silently drop idle connections
            # without ever sending a close frame, so the client sends these
            # periodically and expects a pong back to know the connection is
            # still alive. Reused here to also refresh presence — see
            # UserProfile.last_seen_at/is_online.
            await self.send_json({"type": "pong"})
            await self._touch_presence()
        elif msg_type == "room.join":
            await self._handle_room_join(content)
        elif msg_type == "room.leave":
            await self._handle_room_leave(content)
        else:
            await self.send_json({"type": "error", "detail": f"Unknown type '{msg_type}'"})

    async def _handle_room_join(self, content):
        room_id = content.get("room_id")
        if room_id is None:
            await self.send_json({"type": "error", "detail": "room_id is required"})
            return

        allowed = await self._room_joinable(room_id, self.scope.get("user"))
        if not allowed:
            await self.send_json({"type": "error", "detail": "Room not found"})
            return

        room_group = f"room_{room_id}"
        await self.channel_layer.group_add(room_group, self.channel_name)
        self.joined_rooms.add(room_group)
        await self.send_json({"type": "room.joined", "room_id": room_id})

    async def _handle_room_leave(self, content):
        room_id = content.get("room_id")
        if room_id is None:
            await self.send_json({"type": "error", "detail": "room_id is required"})
            return

        room_group = f"room_{room_id}"
        await self.channel_layer.group_discard(room_group, self.channel_name)
        self.joined_rooms.discard(room_group)
        await self.send_json({"type": "room.left", "room_id": room_id})

    @database_sync_to_async
    def _is_banned(self, user_id):
        profile = UserProfile.objects.filter(user_id=user_id).first()
        return bool(profile and profile.is_banned)

    @database_sync_to_async
    def _touch_presence(self):
        UserProfile.objects.filter(user_id=self.user_id).update(last_seen_at=timezone.now())

    @database_sync_to_async
    def _clear_presence(self):
        UserProfile.objects.filter(user_id=self.user_id).update(last_seen_at=None)

    @database_sync_to_async
    def _room_joinable(self, room_id, user):
        room = Room.objects.filter(pk=room_id).first()
        if room is None:
            return False
        # Same access rule the REST API applies (see
        # permissions.user_can_access_room): private rooms require
        # membership, and guests additionally can't join a public room's
        # live group unless they've been explicitly added to it either —
        # otherwise a guest blocked from a public room over REST could
        # still get its live messages over the socket.
        return user_can_access_room(user, room)

    # ---- channel-layer event handlers (dispatched by group_send "type") ----

    async def message_new(self, event):
        """Relay a newly created Message (see signals.py) to the client."""
        await self.send_json({"type": "message.new", "message": event["message"]})

    async def message_updated(self, event):
        """Relay an edited Message (see signals.py) to the client."""
        await self.send_json({"type": "message.updated", "message": event["message"]})

    async def message_deleted(self, event):
        """Relay a soft-deleted Message (see signals.py) to the client."""
        await self.send_json({"type": "message.deleted", "message": event["message"]})

    async def message_reactions_changed(self, event):
        """Relay an updated reaction summary for a message (see signals.py)."""
        await self.send_json(
            {
                "type": "message.reactions_changed",
                "message_id": event["message_id"],
                "reactions": event["reactions"],
            }
        )

    async def notification_new(self, event):
        """Relay a newly created Notification (see signals.py) to the client."""
        await self.send_json(
            {"type": "notification.new", "notification": event["notification"]}
        )

    async def private_message_new(self, event):
        """Relay a newly created PrivateMessage (see signals.py) to the client.

        No explicit join is needed: sender and receiver both already sit in
        their own "user_<id>" group from connect(), which is where
        signals.py sends this event.
        """
        await self.send_json(
            {
                "type": "private_message.new",
                "conversation_id": event["conversation_id"],
                "message": event["message"],
            }
        )

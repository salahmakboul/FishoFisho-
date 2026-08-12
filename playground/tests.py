"""Automated tests for the DRF + Channels API layer that replaced the old
server-rendered chat UI.

These both prove the new stack (playground/serializers.py, api_views.py,
api_urls.py, consumers.py, routing.py, fishofisho/asgi.py) actually works
end-to-end, and act as permanent regression coverage going forward.
"""

import os
import random
import re
from unittest import mock

from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from django.core.files.uploadedfile import SimpleUploadedFile

from . import routing as playground_routing
from .models import (
    AIInteraction,
    AuditEvent,
    BlockedKeyword,
    Invitation,
    Message,
    MessageAttachment,
    ModerationAction,
    Notification,
    Reaction,
    Room,
    RoomMembership,
    Topic,
    UserProfile,
    Webhook,
    WebhookDelivery,
)
from .signing import ATTACHMENT_URL_MAX_AGE_SECONDS, sign_attachment_url


# ---------------------------------------------------------------------------
# REST API tests
# ---------------------------------------------------------------------------


class RoomAndMessageAPITests(TestCase):
    """Room creation, message posting, and the @mention -> Notification path
    that Message.save() already provides (existing model behavior — this
    just confirms the new API path actually triggers it)."""

    def setUp(self):
        self.client = APIClient()
        self.alice = User.objects.create_user(username="alice", password="pw12345")
        self.bob = User.objects.create_user(username="bob", password="pw12345")
        self.topic = Topic.objects.create(name="General")
        self.client.force_login(self.alice)

    def test_create_room(self):
        response = self.client.post(
            "/api/v1/rooms/",
            {"name": "Test Room", "description": "desc", "topic_id": self.topic.id},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Room.objects.count(), 1)
        room = Room.objects.get()
        self.assertEqual(room.name, "Test Room")
        # host is set server-side from the authenticated user (perform_create)
        self.assertEqual(room.host, self.alice)

    def test_room_requires_authentication(self):
        anon_client = APIClient()
        response = anon_client.post(
            "/api/v1/rooms/",
            {"name": "Nope", "topic_id": self.topic.id},
        )
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_post_and_list_messages(self):
        room = Room.objects.create(host=self.alice, topic=self.topic, name="Room")

        create_resp = self.client.post(
            f"/api/v1/rooms/{room.id}/messages/", {"body": "hello world"}
        )
        self.assertEqual(create_resp.status_code, status.HTTP_201_CREATED, create_resp.data)

        list_resp = self.client.get(f"/api/v1/rooms/{room.id}/messages/")
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        # Cursor-paginated (see pagination.py) -> {"results": [...], "next": ...}
        bodies = [m["body"] for m in list_resp.data["results"]]
        self.assertIn("hello world", bodies)

        # posting adds the poster as a room member via RoomMembership
        # (perform_create) — the source of truth for room membership since
        # the private-rooms slice replaced the legacy `participants` M2M.
        self.assertTrue(RoomMembership.objects.filter(room=room, user=self.alice).exists())

    def test_mention_creates_notification(self):
        room = Room.objects.create(host=self.alice, topic=self.topic, name="Room")

        response = self.client.post(
            f"/api/v1/rooms/{room.id}/messages/", {"body": f"hey @{self.bob.username}!"}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        notification = Notification.objects.get(recipient=self.bob)
        self.assertEqual(notification.sender, self.alice)
        self.assertEqual(notification.notification_type, "mention")
        self.assertFalse(notification.is_read)

    def test_self_mention_does_not_notify(self):
        room = Room.objects.create(host=self.alice, topic=self.topic, name="Room")
        self.client.post(
            f"/api/v1/rooms/{room.id}/messages/", {"body": f"hey @{self.alice.username}"}
        )
        self.assertFalse(Notification.objects.filter(recipient=self.alice).exists())


class NotificationAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.alice = User.objects.create_user(username="alice", password="pw12345")
        self.bob = User.objects.create_user(username="bob", password="pw12345")
        self.client.force_login(self.bob)
        self.n1 = Notification.objects.create(
            recipient=self.bob,
            sender=self.alice,
            notification_type="mention",
            message="alice mentioned you",
        )
        self.n2 = Notification.objects.create(
            recipient=self.bob,
            sender=self.alice,
            notification_type="mention",
            message="alice mentioned you again",
        )

    def test_list_notifications(self):
        response = self.client.get("/api/v1/notifications/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_unread_count(self):
        response = self.client.get("/api/v1/notifications/unread-count/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)

    def test_mark_read(self):
        response = self.client.post(f"/api/v1/notifications/{self.n1.id}/mark-read/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.n1.refresh_from_db()
        self.assertTrue(self.n1.is_read)
        self.n2.refresh_from_db()
        self.assertFalse(self.n2.is_read)

    def test_mark_all_read(self):
        response = self.client.post("/api/v1/notifications/mark-all-read/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            Notification.objects.filter(recipient=self.bob, is_read=False).count(), 0
        )

    def test_notifications_scoped_to_recipient(self):
        # alice has no notifications addressed to her in this setup
        self.client.force_login(self.alice)
        response = self.client.get("/api/v1/notifications/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)


class ProfileAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.alice = User.objects.create_user(username="alice", password="pw12345")
        self.client.force_login(self.alice)

    def test_users_me(self):
        response = self.client.get("/api/v1/users/me/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["username"], "alice")
        # UserProfile is auto-created via the post_save signal on User
        self.assertTrue(UserProfile.objects.filter(user=self.alice).exists())

    def test_profile_me_update(self):
        response = self.client.patch("/api/v1/profile/me/", {"bio": "hello there"})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["bio"], "hello there")


# ---------------------------------------------------------------------------
# Auth API tests
#
# Replaces the old server-rendered login/register/logout view coverage
# (there was none for those views previously — they predate this test
# module) with coverage for the new /api/v1/auth/ endpoints that replaced
# them (see api_views.py's LoginView/RegisterView/LogoutView/CsrfView).
# ---------------------------------------------------------------------------


class AuthAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_csrf_endpoint_sets_cookie(self):
        response = self.client.get("/api/v1/auth/csrf/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("csrftoken", response.cookies)

    def test_login_success(self):
        User.objects.create_user(username="alice", password="pw12345")
        response = self.client.post(
            "/api/v1/auth/login/",
            {"username": "alice", "password": "pw12345"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["username"], "alice")
        # session is now authenticated
        me = self.client.get("/api/v1/users/me/")
        self.assertEqual(me.status_code, status.HTTP_200_OK)

    def test_login_lowercases_username(self):
        User.objects.create_user(username="alice", password="pw12345")
        response = self.client.post(
            "/api/v1/auth/login/",
            {"username": "ALICE", "password": "pw12345"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["username"], "alice")

    def test_login_bad_credentials(self):
        User.objects.create_user(username="alice", password="pw12345")
        response = self.client.post(
            "/api/v1/auth/login/",
            {"username": "alice", "password": "wrong"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.data)

    def test_register_success_logs_in_and_lowercases(self):
        response = self.client.post(
            "/api/v1/auth/register/",
            {"username": "Bob", "password1": "a-strong-pw-123", "password2": "a-strong-pw-123"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["username"], "bob")
        self.assertTrue(User.objects.filter(username="bob").exists())
        me = self.client.get("/api/v1/users/me/")
        self.assertEqual(me.status_code, status.HTTP_200_OK)

    def test_register_password_mismatch_returns_field_errors(self):
        response = self.client.post(
            "/api/v1/auth/register/",
            {"username": "bob", "password1": "a-strong-pw-123", "password2": "different-pw-456"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password2", response.data)

    def test_register_duplicate_username_returns_field_error(self):
        User.objects.create_user(username="bob", password="pw12345")
        response = self.client.post(
            "/api/v1/auth/register/",
            {"username": "bob", "password1": "a-strong-pw-123", "password2": "a-strong-pw-123"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("username", response.data)

    def test_logout_clears_session(self):
        User.objects.create_user(username="alice", password="pw12345")
        self.client.post(
            "/api/v1/auth/login/",
            {"username": "alice", "password": "pw12345"},
            format="json",
        )
        response = self.client.post("/api/v1/auth/logout/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        me = self.client.get("/api/v1/users/me/")
        self.assertIn(me.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_logout_when_already_anonymous_is_a_no_op(self):
        response = self.client.post("/api/v1/auth/logout/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_login_remember_false_expires_on_browser_close(self):
        User.objects.create_user(username="alice", password="pw12345")
        response = self.client.post(
            "/api/v1/auth/login/",
            {"username": "alice", "password": "pw12345", "remember": False},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        # get_expiry_age() reflects None/0 expiry (browser-session cookie) as
        # SESSION_COOKIE_AGE only when set_expiry(0) was NOT called; Django
        # represents "expire on browser close" via
        # session.get_expire_at_browser_close() being True.
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_login_remember_true_uses_sliding_session_cookie_age(self):
        User.objects.create_user(username="alice", password="pw12345")
        response = self.client.post(
            "/api/v1/auth/login/",
            {"username": "alice", "password": "pw12345", "remember": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertFalse(self.client.session.get_expire_at_browser_close())
        self.assertEqual(self.client.session.get_expiry_age(), settings.SESSION_COOKIE_AGE)

    def test_login_remember_omitted_defaults_to_sliding_session(self):
        User.objects.create_user(username="alice", password="pw12345")
        response = self.client.post(
            "/api/v1/auth/login/",
            {"username": "alice", "password": "pw12345"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertFalse(self.client.session.get_expire_at_browser_close())


class PasswordResetAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.alice = User.objects.create_user(username="alice", password="oldpassword123")

    def test_reset_request_for_existing_user_returns_generic_message(self):
        response = self.client.post(
            "/api/v1/auth/password-reset/", {"username": "alice"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("detail", response.data)
        # the "email" was actually sent via the console backend
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("reset-password", mail.outbox[0].body)

    def test_reset_request_for_nonexistent_username_returns_same_message(self):
        existing = self.client.post(
            "/api/v1/auth/password-reset/", {"username": "alice"}, format="json"
        )
        missing = self.client.post(
            "/api/v1/auth/password-reset/", {"username": "nobody"}, format="json"
        )
        self.assertEqual(missing.status_code, status.HTTP_200_OK)
        self.assertEqual(existing.data, missing.data)
        # no email sent for the nonexistent user, only the one from `alice`
        self.assertEqual(len(mail.outbox), 1)

    def _extract_uid_token(self):
        self.client.post(
            "/api/v1/auth/password-reset/", {"username": "alice"}, format="json"
        )
        body = mail.outbox[-1].body
        match = re.search(r"reset-password\?uid=([^&]+)&token=([^\s]+)", body)
        self.assertIsNotNone(match)
        return match.group(1), match.group(2)

    def test_reset_confirm_with_valid_token_changes_password_and_logs_in(self):
        uid, token = self._extract_uid_token()
        response = self.client.post(
            "/api/v1/auth/password-reset-confirm/",
            {
                "uid": uid,
                "token": token,
                "password1": "brand-new-pw-456",
                "password2": "brand-new-pw-456",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["username"], "alice")

        # logged in immediately as part of the confirm response
        me = self.client.get("/api/v1/users/me/")
        self.assertEqual(me.status_code, status.HTTP_200_OK)

        # new password actually works for a fresh login
        fresh_client = APIClient()
        login_resp = fresh_client.post(
            "/api/v1/auth/login/",
            {"username": "alice", "password": "brand-new-pw-456"},
            format="json",
        )
        self.assertEqual(login_resp.status_code, status.HTTP_200_OK, login_resp.data)

    def test_reset_confirm_with_tampered_token_is_rejected(self):
        uid, token = self._extract_uid_token()
        response = self.client.post(
            "/api/v1/auth/password-reset-confirm/",
            {
                "uid": uid,
                "token": token[:-1] + ("a" if token[-1] != "a" else "b"),
                "password1": "brand-new-pw-456",
                "password2": "brand-new-pw-456",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.alice.refresh_from_db()
        self.assertTrue(self.alice.check_password("oldpassword123"))

    def test_reset_confirm_with_bad_uid_is_rejected(self):
        response = self.client.post(
            "/api/v1/auth/password-reset-confirm/",
            {
                "uid": "not-a-real-uid",
                "token": "whatever",
                "password1": "brand-new-pw-456",
                "password2": "brand-new-pw-456",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reset_confirm_password_mismatch_returns_field_error(self):
        uid, token = self._extract_uid_token()
        response = self.client.post(
            "/api/v1/auth/password-reset-confirm/",
            {
                "uid": uid,
                "token": token,
                "password1": "brand-new-pw-456",
                "password2": "different-pw-789",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password2", response.data)

    def test_reset_confirm_weak_password_rejected_by_validators(self):
        uid, token = self._extract_uid_token()
        response = self.client.post(
            "/api/v1/auth/password-reset-confirm/",
            {"uid": uid, "token": token, "password1": "1234", "password2": "1234"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password1", response.data)


# ---------------------------------------------------------------------------
# AI-assistant auto-reply tests (task 3)
# ---------------------------------------------------------------------------


class AIReplyTests(TestCase):
    """Confirms the AI-assistant trigger logic ported from the old `room`
    view's POST handler into MessageListCreateView actually fires: a
    "@fishoai" mention, or the ~20% random roll, produces a second Message
    authored by the FishoAI bot user, delivered through the same API path.

    AIAssistant.__init__ (ai_helper.py) may attempt a real Gemini network
    call if GEMINI_API_KEY is configured (it is, in this repo's .env) — that
    would make these tests slow/flaky/non-hermetic, so AIAssistant is
    patched out entirely. ai_helper.py already has a documented mock-response
    fallback path (`_get_mock_response`) for when no/invalid key is present,
    which is exactly the behavior being stood in for here.
    """

    def setUp(self):
        self.client = APIClient()
        self.alice = User.objects.create_user(username="alice", password="pw12345")
        self.ai_user = User.objects.create_user(username="FishoAI")
        self.topic = Topic.objects.create(name="General")
        self.room = Room.objects.create(host=self.alice, topic=self.topic, name="Room")
        self.client.force_login(self.alice)

    def _fake_ai(self, mention_reply="mention reply", regular_reply="regular reply"):
        fake = mock.Mock()
        fake.ai_user = self.ai_user
        fake.handle_mention.return_value = mention_reply
        fake.respond_to_message.return_value = regular_reply
        return fake

    @mock.patch("playground.api_views.AIAssistant")
    def test_mention_triggers_ai_reply(self, mock_ai_cls):
        mock_ai_cls.return_value = self._fake_ai(mention_reply="Hi Alice!")

        response = self.client.post(
            f"/api/v1/rooms/{self.room.id}/messages/", {"body": "hey @FishoAI help me"}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        bot_messages = Message.objects.filter(user=self.ai_user, room=self.room)
        self.assertEqual(bot_messages.count(), 1)
        self.assertEqual(bot_messages.first().body, "Hi Alice!")
        mock_ai_cls.return_value.handle_mention.assert_called_once()

    @mock.patch("playground.api_views.random.random", return_value=0.1)
    @mock.patch("playground.api_views.AIAssistant")
    def test_random_chance_triggers_ai_reply(self, mock_ai_cls, mock_random):
        mock_ai_cls.return_value = self._fake_ai(regular_reply="Nice to hear!")

        response = self.client.post(
            f"/api/v1/rooms/{self.room.id}/messages/", {"body": "just chatting here"}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        bot_messages = Message.objects.filter(user=self.ai_user, room=self.room)
        self.assertEqual(bot_messages.count(), 1)
        self.assertEqual(bot_messages.first().body, "Nice to hear!")

    @mock.patch("playground.api_views.random.random", return_value=0.9)
    @mock.patch("playground.api_views.AIAssistant")
    def test_no_ai_reply_when_not_triggered(self, mock_ai_cls, mock_random):
        mock_ai_cls.return_value = self._fake_ai()

        response = self.client.post(
            f"/api/v1/rooms/{self.room.id}/messages/", {"body": "just chatting here"}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertFalse(Message.objects.filter(user=self.ai_user, room=self.room).exists())
        mock_ai_cls.assert_not_called()

    @mock.patch("playground.api_views.random.random", return_value=0.1)
    @mock.patch("playground.api_views.AIAssistant")
    def test_no_ai_reply_when_assistant_declines(self, mock_ai_cls, mock_random):
        # respond_to_message() has its own internal chance check and can
        # return None; the API view must not create a body=None Message in
        # that case (that would crash Message.save()'s check_mentions()).
        mock_ai_cls.return_value = self._fake_ai(regular_reply=None)

        response = self.client.post(
            f"/api/v1/rooms/{self.room.id}/messages/", {"body": "just chatting here"}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertFalse(Message.objects.filter(user=self.ai_user, room=self.room).exists())

    @mock.patch("playground.api_views.AIAssistant")
    def test_ai_error_does_not_break_user_message(self, mock_ai_cls):
        mock_ai_cls.side_effect = RuntimeError("Gemini is down")

        response = self.client.post(
            f"/api/v1/rooms/{self.room.id}/messages/", {"body": "hey @FishoAI"}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertTrue(
            Message.objects.filter(user=self.alice, room=self.room, body="hey @FishoAI").exists()
        )
        self.assertFalse(Message.objects.filter(user=self.ai_user, room=self.room).exists())


# ---------------------------------------------------------------------------
# WebSocket (Channels) tests
# ---------------------------------------------------------------------------


class StreamConsumerTests(TransactionTestCase):
    """Channels tests need TransactionTestCase (not TestCase) because the
    consumer runs its DB access in a different thread/connection via
    database_sync_to_async.

    The communicator is built directly against
    URLRouter(playground.routing.websocket_urlpatterns) with the
    authenticated user injected straight into the connection scope, rather
    than going through fishofisho.asgi.application's
    channels.auth.AuthMiddlewareStack (which authenticates via a session
    cookie read from scope["headers"]). That's the "inject scope['user']
    directly" option flagged as acceptable in the task brief — it's the
    simplest reliable way to drive Channels 4.x tests with
    InMemoryChannelLayer, and it still exercises the real StreamConsumer
    (connect/receive_json/group broadcast) end to end; only
    AuthMiddlewareStack's own session-cookie parsing (framework code, not
    ours) goes untested.
    """

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pw12345")
        self.topic = Topic.objects.create(name="General")
        self.room = Room.objects.create(host=self.alice, topic=self.topic, name="Room")
        self.application = URLRouter(playground_routing.websocket_urlpatterns)

    async def _connected_communicator(self, user):
        communicator = WebsocketCommunicator(self.application, "/ws/stream/")
        communicator.scope["user"] = user
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        return communicator

    async def test_authenticated_connection_accepted(self):
        communicator = await self._connected_communicator(self.alice)
        await communicator.disconnect()

    async def test_room_join_acks(self):
        communicator = await self._connected_communicator(self.alice)
        try:
            await communicator.send_json_to({"type": "room.join", "room_id": self.room.id})
            response = await communicator.receive_json_from()
            self.assertEqual(response, {"type": "room.joined", "room_id": self.room.id})
        finally:
            await communicator.disconnect()

    async def test_ping_gets_pong(self):
        communicator = await self._connected_communicator(self.alice)
        try:
            await communicator.send_json_to({"type": "ping"})
            response = await communicator.receive_json_from()
            self.assertEqual(response, {"type": "pong"})
        finally:
            await communicator.disconnect()

    async def test_room_join_unknown_room_errors(self):
        communicator = await self._connected_communicator(self.alice)
        try:
            await communicator.send_json_to({"type": "room.join", "room_id": 999999})
            response = await communicator.receive_json_from()
            self.assertEqual(response["type"], "error")
        finally:
            await communicator.disconnect()

    async def test_new_message_is_broadcast_to_joined_room(self):
        from channels.db import database_sync_to_async

        communicator = await self._connected_communicator(self.alice)
        try:
            await communicator.send_json_to({"type": "room.join", "room_id": self.room.id})
            await communicator.receive_json_from()  # room.joined ack

            # Simulate another request creating a Message via the ORM (e.g.
            # the REST API's MessageListCreateView.perform_create) — this
            # should fire signals.py's post_save receiver, which pushes a
            # "message.new" event onto the room's channel-layer group.
            create_message = database_sync_to_async(Message.objects.create)
            message = await create_message(user=self.alice, room=self.room, body="hi there")

            event = await communicator.receive_json_from(timeout=5)
            self.assertEqual(event["type"], "message.new")
            self.assertEqual(event["message"]["id"], message.id)
            self.assertEqual(event["message"]["body"], "hi there")
        finally:
            await communicator.disconnect()

    async def test_unauthenticated_connection_rejected(self):
        from django.contrib.auth.models import AnonymousUser

        communicator = WebsocketCommunicator(self.application, "/ws/stream/")
        communicator.scope["user"] = AnonymousUser()
        connected, _ = await communicator.connect()
        self.assertFalse(connected)


# ---------------------------------------------------------------------------
# Roles / RBAC
# ---------------------------------------------------------------------------


class RoleTests(TestCase):
    def test_new_user_defaults_to_member(self):
        user = User.objects.create_user(username="carol", password="pw12345")
        self.assertEqual(user.userprofile.role, UserProfile.ROLE_MEMBER)

    def test_superuser_backfill_migration_logic_promotes_to_owner(self):
        # The data migration (0011) already ran against this test DB as
        # part of migrate; this test instead exercises the same rule
        # directly, since re-running a migration mid-test-suite isn't
        # practical. A superuser created after the fact should still be
        # sensible to promote by hand via the same logic the migration
        # uses - confirm the role field + defaults behave as expected.
        admin_user = User.objects.create_superuser(
            username="root", password="pw12345", email="root@example.com"
        )
        # Signal sets default role="member" on creation; the migration's
        # promotion logic (mirrored here) is what actually flips supers.
        admin_user.userprofile.role = UserProfile.ROLE_OWNER
        admin_user.userprofile.save()
        admin_user.refresh_from_db()
        self.assertEqual(admin_user.userprofile.role, UserProfile.ROLE_OWNER)

    def test_is_admin_or_owner_property(self):
        user = User.objects.create_user(username="dave", password="pw12345")
        user.userprofile.role = UserProfile.ROLE_MEMBER
        self.assertFalse(user.userprofile.is_admin_or_owner)
        user.userprofile.role = UserProfile.ROLE_ADMIN
        self.assertTrue(user.userprofile.is_admin_or_owner)
        user.userprofile.role = UserProfile.ROLE_OWNER
        self.assertTrue(user.userprofile.is_admin_or_owner)


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


class InvitationAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(username="owner1", password="pw12345")
        self.owner.userprofile.role = UserProfile.ROLE_OWNER
        self.owner.userprofile.save()
        self.member = User.objects.create_user(username="member1", password="pw12345")

    def test_admin_or_owner_can_create_invitation(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            "/api/v1/invitations/", {"role": "admin", "note": "for sam"}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Invitation.objects.count(), 1)
        invite = Invitation.objects.get()
        self.assertEqual(invite.invited_by, self.owner)
        self.assertEqual(invite.role, "admin")
        self.assertIn(invite.token, response.data["invite_link"])
        self.assertTrue(response.data["invite_link"].startswith(settings.FRONTEND_URL))

    def test_member_cannot_create_invitation(self):
        self.client.force_login(self.member)
        response = self.client.post("/api/v1/invitations/", {"role": "member"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_member_cannot_list_invitations(self):
        self.client.force_login(self.member)
        response = self.client.get("/api/v1/invitations/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_can_list_pending_invitations(self):
        Invitation.objects.create(invited_by=self.owner, role="member")
        self.client.force_login(self.owner)
        response = self.client.get("/api/v1/invitations/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_owner_can_revoke_unaccepted_invitation(self):
        invite = Invitation.objects.create(invited_by=self.owner, role="member")
        self.client.force_login(self.owner)
        response = self.client.delete(f"/api/v1/invitations/{invite.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Invitation.objects.filter(id=invite.id).exists())

    def test_cannot_revoke_accepted_invitation(self):
        invite = Invitation.objects.create(invited_by=self.owner, role="member")
        invite.accept(self.member)
        self.client.force_login(self.owner)
        response = self.client.delete(f"/api/v1/invitations/{invite.id}/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class RegisterWithInviteTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(username="owner2", password="pw12345")
        self.owner.userprofile.role = UserProfile.ROLE_OWNER
        self.owner.userprofile.save()

    def test_register_without_token_still_works_and_defaults_to_member(self):
        response = self.client.post(
            "/api/v1/auth/register/",
            {"username": "newperson", "password1": "SuperSecret123", "password2": "SuperSecret123"},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        user = User.objects.get(username="newperson")
        self.assertEqual(user.userprofile.role, UserProfile.ROLE_MEMBER)

    def test_register_with_valid_token_assigns_role_and_marks_accepted(self):
        invite = Invitation.objects.create(invited_by=self.owner, role="admin")
        response = self.client.post(
            "/api/v1/auth/register/",
            {
                "username": "invitee",
                "password1": "SuperSecret123",
                "password2": "SuperSecret123",
                "invite_token": invite.token,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        user = User.objects.get(username="invitee")
        self.assertEqual(user.userprofile.role, "admin")
        invite.refresh_from_db()
        self.assertEqual(invite.accepted_by, user)
        self.assertIsNotNone(invite.accepted_at)

    def test_register_with_invalid_token_returns_400(self):
        response = self.client.post(
            "/api/v1/auth/register/",
            {
                "username": "someone",
                "password1": "SuperSecret123",
                "password2": "SuperSecret123",
                "invite_token": "not-a-real-token",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(username="someone").exists())

    def test_register_with_expired_token_returns_400(self):
        from django.utils import timezone

        invite = Invitation.objects.create(invited_by=self.owner, role="member")
        invite.expires_at = timezone.now() - timezone.timedelta(days=1)
        invite.save()
        response = self.client.post(
            "/api/v1/auth/register/",
            {
                "username": "toolate",
                "password1": "SuperSecret123",
                "password2": "SuperSecret123",
                "invite_token": invite.token,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_register_with_already_accepted_token_returns_400(self):
        invite = Invitation.objects.create(invited_by=self.owner, role="member")
        already = User.objects.create_user(username="already", password="pw12345")
        invite.accept(already)
        response = self.client.post(
            "/api/v1/auth/register/",
            {
                "username": "toolate2",
                "password1": "SuperSecret123",
                "password2": "SuperSecret123",
                "invite_token": invite.token,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# Private rooms
# ---------------------------------------------------------------------------


class PrivateRoomAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.alice = User.objects.create_user(username="alice2", password="pw12345")
        self.bob = User.objects.create_user(username="bob2", password="pw12345")
        self.topic = Topic.objects.create(name="General")
        self.private_room = Room.objects.create(
            host=self.alice, topic=self.topic, name="Secret", is_private=True
        )
        RoomMembership.objects.create(room=self.private_room, user=self.alice, role="admin")
        self.public_room = Room.objects.create(
            host=self.alice, topic=self.topic, name="Public"
        )
        RoomMembership.objects.create(room=self.public_room, user=self.alice, role="admin")

    def test_create_private_room_makes_creator_admin_member(self):
        self.client.force_login(self.alice)
        response = self.client.post(
            "/api/v1/rooms/",
            {"name": "New Private", "topic_id": self.topic.id, "is_private": True},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        room = Room.objects.get(name="New Private")
        self.assertTrue(room.is_private)
        membership = RoomMembership.objects.get(room=room, user=self.alice)
        self.assertEqual(membership.role, "admin")

    def test_private_room_hidden_from_non_member_in_list(self):
        self.client.force_login(self.bob)
        response = self.client.get("/api/v1/rooms/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = [r["name"] for r in response.data]
        self.assertNotIn("Secret", names)
        self.assertIn("Public", names)

    def test_private_room_visible_to_member_in_list(self):
        self.client.force_login(self.alice)
        response = self.client.get("/api/v1/rooms/")
        names = [r["name"] for r in response.data]
        self.assertIn("Secret", names)

    def test_private_room_messages_denied_to_non_member(self):
        self.client.force_login(self.bob)
        response = self.client.get(f"/api/v1/rooms/{self.private_room.id}/messages/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_private_room_message_post_denied_to_non_member(self):
        self.client.force_login(self.bob)
        response = self.client.post(
            f"/api/v1/rooms/{self.private_room.id}/messages/", {"body": "sneaky"}
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_private_room_messages_allowed_to_member(self):
        self.client.force_login(self.alice)
        response = self.client.get(f"/api/v1/rooms/{self.private_room.id}/messages/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_public_room_messages_still_open_to_any_authenticated_user(self):
        self.client.force_login(self.bob)
        response = self.client.get(f"/api/v1/rooms/{self.public_room.id}/messages/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class PrivateRoomWebSocketTests(TransactionTestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice3", password="pw12345")
        self.bob = User.objects.create_user(username="bob3", password="pw12345")
        self.topic = Topic.objects.create(name="General")
        self.private_room = Room.objects.create(
            host=self.alice, topic=self.topic, name="Secret", is_private=True
        )
        RoomMembership.objects.create(room=self.private_room, user=self.alice, role="admin")
        self.application = URLRouter(playground_routing.websocket_urlpatterns)

    async def _connected_communicator(self, user):
        communicator = WebsocketCommunicator(self.application, "/ws/stream/")
        communicator.scope["user"] = user
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        return communicator

    async def test_private_room_join_rejected_for_non_member(self):
        communicator = await self._connected_communicator(self.bob)
        try:
            await communicator.send_json_to(
                {"type": "room.join", "room_id": self.private_room.id}
            )
            response = await communicator.receive_json_from()
            self.assertEqual(response["type"], "error")
        finally:
            await communicator.disconnect()

    async def test_private_room_join_accepted_for_member(self):
        communicator = await self._connected_communicator(self.alice)
        try:
            await communicator.send_json_to(
                {"type": "room.join", "room_id": self.private_room.id}
            )
            response = await communicator.receive_json_from()
            self.assertEqual(
                response, {"type": "room.joined", "room_id": self.private_room.id}
            )
        finally:
            await communicator.disconnect()


# ---------------------------------------------------------------------------
# Room authorization (update/delete), archive, and member management
# ---------------------------------------------------------------------------


class RoomAuthorizationTests(TestCase):
    """Only a room's own admin or a workspace admin/owner may PATCH it; only
    a workspace admin/owner may hard-delete it (not even a room's own
    admin) — see IsRoomAdminOrWorkspaceAdmin's docstring in permissions.py
    for why destroy is split out."""

    def setUp(self):
        self.client = APIClient()
        self.room_admin = User.objects.create_user(username="roomadmin", password="pw12345")
        self.outsider = User.objects.create_user(username="outsider", password="pw12345")
        self.workspace_admin = User.objects.create_user(username="wsadmin", password="pw12345")
        self.workspace_admin.userprofile.role = UserProfile.ROLE_ADMIN
        self.workspace_admin.userprofile.save()
        self.topic = Topic.objects.create(name="General")
        self.room = Room.objects.create(host=self.room_admin, topic=self.topic, name="Team Room")
        RoomMembership.objects.create(room=self.room, user=self.room_admin, role="admin")

    def test_non_member_cannot_patch_room(self):
        self.client.force_login(self.outsider)
        response = self.client.patch(f"/api/v1/rooms/{self.room.id}/", {"name": "Hacked"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.room.refresh_from_db()
        self.assertEqual(self.room.name, "Team Room")

    def test_non_member_cannot_delete_room(self):
        self.client.force_login(self.outsider)
        response = self.client.delete(f"/api/v1/rooms/{self.room.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Room.objects.filter(id=self.room.id).exists())

    def test_room_admin_can_patch_own_room(self):
        self.client.force_login(self.room_admin)
        response = self.client.patch(f"/api/v1/rooms/{self.room.id}/", {"name": "Renamed"})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.room.refresh_from_db()
        self.assertEqual(self.room.name, "Renamed")

    def test_workspace_admin_can_patch_any_room(self):
        self.client.force_login(self.workspace_admin)
        response = self.client.patch(f"/api/v1/rooms/{self.room.id}/", {"name": "Admin Renamed"})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_room_admin_cannot_hard_delete_room(self):
        self.client.force_login(self.room_admin)
        response = self.client.delete(f"/api/v1/rooms/{self.room.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Room.objects.filter(id=self.room.id).exists())

    def test_workspace_admin_can_hard_delete_room(self):
        self.client.force_login(self.workspace_admin)
        response = self.client.delete(f"/api/v1/rooms/{self.room.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Room.objects.filter(id=self.room.id).exists())


class RoomArchiveTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.room_admin = User.objects.create_user(username="archadmin", password="pw12345")
        self.member = User.objects.create_user(username="archmember", password="pw12345")
        self.outsider = User.objects.create_user(username="archoutsider", password="pw12345")
        self.topic = Topic.objects.create(name="General")
        self.room = Room.objects.create(host=self.room_admin, topic=self.topic, name="Archivable")
        RoomMembership.objects.create(room=self.room, user=self.room_admin, role="admin")
        RoomMembership.objects.create(room=self.room, user=self.member, role="member")

    def test_non_admin_cannot_archive(self):
        self.client.force_login(self.member)
        response = self.client.post(f"/api/v1/rooms/{self.room.id}/archive/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_room_admin_can_archive_and_unarchive(self):
        self.client.force_login(self.room_admin)
        response = self.client.post(f"/api/v1/rooms/{self.room.id}/archive/")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.room.refresh_from_db()
        self.assertTrue(self.room.is_archived)

        response = self.client.post(f"/api/v1/rooms/{self.room.id}/unarchive/")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.room.refresh_from_db()
        self.assertFalse(self.room.is_archived)

    def test_archived_room_hidden_from_default_list_but_visible_with_include_archived(self):
        self.room.is_archived = True
        self.room.save(update_fields=["is_archived"])
        self.client.force_login(self.member)

        response = self.client.get("/api/v1/rooms/")
        names = [r["name"] for r in response.data]
        self.assertNotIn("Archivable", names)

        response = self.client.get("/api/v1/rooms/?include_archived=true")
        names = [r["name"] for r in response.data]
        self.assertIn("Archivable", names)

    def test_archived_room_rejects_new_messages(self):
        self.room.is_archived = True
        self.room.save(update_fields=["is_archived"])
        self.client.force_login(self.member)
        response = self.client.post(
            f"/api/v1/rooms/{self.room.id}/messages/", {"body": "still here?"}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_archived_room_still_allows_reading_existing_messages(self):
        Message.objects.create(user=self.room_admin, room=self.room, body="before archive")
        self.room.is_archived = True
        self.room.save(update_fields=["is_archived"])
        self.client.force_login(self.member)
        response = self.client.get(f"/api/v1/rooms/{self.room.id}/messages/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        bodies = [m["body"] for m in response.data["results"]]
        self.assertIn("before archive", bodies)


class RoomMemberManagementTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(username="memadmin", password="pw12345")
        self.member = User.objects.create_user(username="memmember", password="pw12345")
        self.outsider = User.objects.create_user(username="memoutsider", password="pw12345")
        self.topic = Topic.objects.create(name="General")
        self.room = Room.objects.create(host=self.admin, topic=self.topic, name="Managed Room")
        RoomMembership.objects.create(room=self.room, user=self.admin, role="admin")
        RoomMembership.objects.create(room=self.room, user=self.member, role="member")

    def test_list_members_visible_to_room_member(self):
        self.client.force_login(self.member)
        response = self.client.get(f"/api/v1/rooms/{self.room.id}/members/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        usernames = [m["user"]["username"] for m in response.data]
        self.assertIn("memadmin", usernames)
        self.assertIn("memmember", usernames)

    def test_admin_can_add_member(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            f"/api/v1/rooms/{self.room.id}/members/", {"user_id": self.outsider.id}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertTrue(
            RoomMembership.objects.filter(room=self.room, user=self.outsider, role="member").exists()
        )

    def test_non_admin_cannot_add_member(self):
        self.client.force_login(self.member)
        response = self.client.post(
            f"/api/v1/rooms/{self.room.id}/members/", {"user_id": self.outsider.id}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_change_member_role(self):
        self.client.force_login(self.admin)
        response = self.client.patch(
            f"/api/v1/rooms/{self.room.id}/members/{self.member.id}/",
            {"role": "admin"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        membership = RoomMembership.objects.get(room=self.room, user=self.member)
        self.assertEqual(membership.role, "admin")

    def test_non_admin_cannot_change_role(self):
        self.client.force_login(self.member)
        response = self.client.patch(
            f"/api/v1/rooms/{self.room.id}/members/{self.admin.id}/",
            {"role": "member"},
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_remove_member(self):
        self.client.force_login(self.admin)
        response = self.client.delete(f"/api/v1/rooms/{self.room.id}/members/{self.member.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(RoomMembership.objects.filter(room=self.room, user=self.member).exists())

    def test_cannot_remove_last_admin(self):
        self.client.force_login(self.admin)
        response = self.client.delete(f"/api/v1/rooms/{self.room.id}/members/{self.admin.id}/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(RoomMembership.objects.filter(room=self.room, user=self.admin).exists())

    def test_cannot_demote_last_admin(self):
        self.client.force_login(self.admin)
        response = self.client.patch(
            f"/api/v1/rooms/{self.room.id}/members/{self.admin.id}/",
            {"role": "member"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_member_can_remove_self_leave_room(self):
        self.client.force_login(self.member)
        response = self.client.delete(f"/api/v1/rooms/{self.room.id}/members/{self.member.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(RoomMembership.objects.filter(room=self.room, user=self.member).exists())


# ---------------------------------------------------------------------------
# Messaging slice: edit/delete, reactions, inline reply, @channel/@here,
# attachments, cursor pagination.
# ---------------------------------------------------------------------------


class MessageEditDeleteTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.author = User.objects.create_user(username="msgauthor", password="pw12345")
        self.room_admin = User.objects.create_user(username="msgadmin", password="pw12345")
        self.other = User.objects.create_user(username="msgother", password="pw12345")
        self.topic = Topic.objects.create(name="General")
        self.room = Room.objects.create(host=self.room_admin, topic=self.topic, name="Edit Room")
        RoomMembership.objects.create(room=self.room, user=self.room_admin, role="admin")
        RoomMembership.objects.create(room=self.room, user=self.author, role="member")
        self.message = Message.objects.create(user=self.author, room=self.room, body="original text")

    def test_author_can_edit_own_message(self):
        self.client.force_login(self.author)
        response = self.client.patch(
            f"/api/v1/rooms/{self.room.id}/messages/{self.message.id}/",
            {"body": "edited text"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.message.refresh_from_db()
        self.assertEqual(self.message.body, "edited text")
        self.assertIsNotNone(self.message.edited_at)

    def test_non_author_cannot_edit_message(self):
        self.client.force_login(self.other)
        response = self.client.patch(
            f"/api/v1/rooms/{self.room.id}/messages/{self.message.id}/",
            {"body": "hacked"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_room_admin_cannot_edit_someone_elses_message(self):
        # Judgment call: edit is author-only, NOT extended to room admins
        # (unlike delete) — see MessageDetailView's docstring.
        self.client.force_login(self.room_admin)
        response = self.client.patch(
            f"/api/v1/rooms/{self.room.id}/messages/{self.message.id}/",
            {"body": "admin edit"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_author_can_delete_own_message(self):
        self.client.force_login(self.author)
        response = self.client.delete(f"/api/v1/rooms/{self.room.id}/messages/{self.message.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.message.refresh_from_db()
        self.assertTrue(self.message.is_deleted)
        self.assertEqual(self.message.body, "")
        self.assertIsNotNone(self.message.deleted_at)

    def test_room_admin_can_delete_someone_elses_message(self):
        self.client.force_login(self.room_admin)
        response = self.client.delete(f"/api/v1/rooms/{self.room.id}/messages/{self.message.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.message.refresh_from_db()
        self.assertTrue(self.message.is_deleted)

    def test_non_author_non_admin_cannot_delete_message(self):
        self.client.force_login(self.other)
        response = self.client.delete(f"/api/v1/rooms/{self.room.id}/messages/{self.message.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.message.refresh_from_db()
        self.assertFalse(self.message.is_deleted)

    def test_deleted_message_still_listed_with_blanked_body(self):
        self.client.force_login(self.author)
        self.client.delete(f"/api/v1/rooms/{self.room.id}/messages/{self.message.id}/")
        response = self.client.get(f"/api/v1/rooms/{self.room.id}/messages/")
        row = next(m for m in response.data["results"] if m["id"] == self.message.id)
        self.assertTrue(row["is_deleted"])
        self.assertEqual(row["body"], "")

    def test_cannot_edit_a_deleted_message(self):
        self.client.force_login(self.author)
        self.client.delete(f"/api/v1/rooms/{self.room.id}/messages/{self.message.id}/")
        response = self.client.patch(
            f"/api/v1/rooms/{self.room.id}/messages/{self.message.id}/",
            {"body": "too late"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_edit_does_not_re_notify_mentions(self):
        # Message.save() only runs check_mentions() on creation now (see
        # models.py) — editing a message that already mentions someone must
        # not create a second Notification for the same mention.
        self.client.force_login(self.author)
        create_resp = self.client.post(
            f"/api/v1/rooms/{self.room.id}/messages/",
            {"body": f"hi @{self.other.username}"},
        )
        new_msg_id = create_resp.data["id"]
        self.assertEqual(Notification.objects.filter(recipient=self.other).count(), 1)

        self.client.patch(
            f"/api/v1/rooms/{self.room.id}/messages/{new_msg_id}/",
            {"body": f"hi again @{self.other.username}"},
            format="json",
        )
        self.assertEqual(Notification.objects.filter(recipient=self.other).count(), 1)


class ReactionAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.alice = User.objects.create_user(username="reactalice", password="pw12345")
        self.bob = User.objects.create_user(username="reactbob", password="pw12345")
        self.topic = Topic.objects.create(name="General")
        self.room = Room.objects.create(host=self.alice, topic=self.topic, name="React Room")
        RoomMembership.objects.create(room=self.room, user=self.alice, role="admin")
        self.message = Message.objects.create(user=self.alice, room=self.room, body="react to this")

    def test_add_reaction(self):
        self.client.force_login(self.bob)
        response = self.client.post(
            f"/api/v1/rooms/{self.room.id}/messages/{self.message.id}/reactions/",
            {"emoji": "👍"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertTrue(
            Reaction.objects.filter(message=self.message, user=self.bob, emoji="👍").exists()
        )
        summary = response.data["reactions"]
        self.assertEqual(summary, [{"emoji": "👍", "count": 1, "user_ids": [self.bob.id]}])

    def test_toggle_off_same_emoji(self):
        self.client.force_login(self.bob)
        url = f"/api/v1/rooms/{self.room.id}/messages/{self.message.id}/reactions/"
        self.client.post(url, {"emoji": "🔥"}, format="json")
        response = self.client.post(url, {"emoji": "🔥"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Reaction.objects.filter(message=self.message, user=self.bob).exists())
        self.assertEqual(response.data["reactions"], [])

    def test_one_reaction_per_emoji_per_user_but_multiple_emoji_allowed(self):
        self.client.force_login(self.bob)
        url = f"/api/v1/rooms/{self.room.id}/messages/{self.message.id}/reactions/"
        self.client.post(url, {"emoji": "👍"}, format="json")
        response = self.client.post(url, {"emoji": "❤️"}, format="json")
        self.assertEqual(Reaction.objects.filter(message=self.message, user=self.bob).count(), 2)
        emojis = {r["emoji"] for r in response.data["reactions"]}
        self.assertEqual(emojis, {"👍", "❤️"})

    def test_reaction_count_aggregates_across_users(self):
        self.client.force_login(self.alice)
        self.client.post(
            f"/api/v1/rooms/{self.room.id}/messages/{self.message.id}/reactions/",
            {"emoji": "🎉"},
            format="json",
        )
        self.client.force_login(self.bob)
        response = self.client.post(
            f"/api/v1/rooms/{self.room.id}/messages/{self.message.id}/reactions/",
            {"emoji": "🎉"},
            format="json",
        )
        entry = next(r for r in response.data["reactions"] if r["emoji"] == "🎉")
        self.assertEqual(entry["count"], 2)
        self.assertCountEqual(entry["user_ids"], [self.alice.id, self.bob.id])


class ReplyToAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.alice = User.objects.create_user(username="replyalice", password="pw12345")
        self.bob = User.objects.create_user(username="replybob", password="pw12345")
        self.topic = Topic.objects.create(name="General")
        self.room = Room.objects.create(host=self.alice, topic=self.topic, name="Reply Room")
        RoomMembership.objects.create(room=self.room, user=self.alice, role="admin")
        self.original = Message.objects.create(user=self.alice, room=self.room, body="original")

    def test_reply_to_set_on_create(self):
        self.client.force_login(self.bob)
        response = self.client.post(
            f"/api/v1/rooms/{self.room.id}/messages/",
            {"body": "a reply", "reply_to": self.original.id},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["reply_to"], self.original.id)
        self.assertEqual(response.data["reply_to_preview"]["body"], "original")

    def test_reply_to_survives_original_deletion_via_set_null(self):
        reply = Message.objects.create(
            user=self.bob, room=self.room, body="a reply", reply_to=self.original
        )
        self.original.delete()
        reply.refresh_from_db()
        self.assertIsNone(reply.reply_to)

    def test_reply_to_must_be_in_same_room(self):
        other_room = Room.objects.create(host=self.alice, topic=self.topic, name="Other Room")
        other_message = Message.objects.create(user=self.alice, room=other_room, body="elsewhere")
        self.client.force_login(self.bob)
        response = self.client.post(
            f"/api/v1/rooms/{self.room.id}/messages/",
            {"body": "cross-room reply", "reply_to": other_message.id},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ChannelHereMentionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.alice = User.objects.create_user(username="chanalice", password="pw12345")
        self.bob = User.objects.create_user(username="chanbob", password="pw12345")
        self.carol = User.objects.create_user(username="chancarol", password="pw12345")
        self.topic = Topic.objects.create(name="General")
        self.room = Room.objects.create(host=self.alice, topic=self.topic, name="Channel Room")
        RoomMembership.objects.create(room=self.room, user=self.alice, role="admin")
        RoomMembership.objects.create(room=self.room, user=self.bob, role="member")
        RoomMembership.objects.create(room=self.room, user=self.carol, role="member")

    def test_at_channel_notifies_all_members_except_sender(self):
        self.client.force_login(self.alice)
        response = self.client.post(
            f"/api/v1/rooms/{self.room.id}/messages/", {"body": "@channel heads up"}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        recipients = set(Notification.objects.values_list("recipient__username", flat=True))
        self.assertEqual(recipients, {"chanbob", "chancarol"})

    def test_at_here_behaves_identically_to_at_channel_for_now(self):
        # Simplification (see Message.check_mentions docstring): there's no
        # presence/online-tracking yet, so @here fans out to every member
        # exactly like @channel rather than only "currently online" members.
        self.client.force_login(self.alice)
        response = self.client.post(
            f"/api/v1/rooms/{self.room.id}/messages/", {"body": "@here anyone around?"}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        recipients = set(Notification.objects.values_list("recipient__username", flat=True))
        self.assertEqual(recipients, {"chanbob", "chancarol"})


class NotificationPreferenceTests(TestCase):
    """Notification-preferences slice: NotificationPreference (global
    per-category on/off) + RoomNotificationSetting (per-room override)
    gating whether a Notification row actually gets created for mentions,
    @channel/@here, and thread replies. See models.notification_allowed
    for the precedence rules under test here (room override > global,
    direct @mentions bypass a muted/mentions-only room unless mentions are
    ALSO off globally)."""

    def setUp(self):
        from .models import NotificationPreference, RoomNotificationSetting

        self.NotificationPreference = NotificationPreference
        self.RoomNotificationSetting = RoomNotificationSetting
        self.client = APIClient()
        self.alice = User.objects.create_user(username="prefalice", password="pw12345")
        self.bob = User.objects.create_user(username="prefbob", password="pw12345")
        self.carol = User.objects.create_user(username="prefcarol", password="pw12345")
        self.room = Room.objects.create(host=self.alice, name="pref-room")
        RoomMembership.objects.create(room=self.room, user=self.alice, role="admin")
        RoomMembership.objects.create(room=self.room, user=self.bob, role="member")
        RoomMembership.objects.create(room=self.room, user=self.carol, role="member")

    def _post(self, user, body, reply_to=None):
        self.client.force_login(user)
        payload = {"body": body}
        if reply_to is not None:
            payload["reply_to"] = reply_to
        return self.client.post(f"/api/v1/rooms/{self.room.id}/messages/", payload)

    def test_default_preferences_allow_mention_channel_and_thread_notifications(self):
        # No NotificationPreference row exists yet for bob -> lazy defaults
        # (all True) must apply, exactly like before this slice existed.
        self.assertFalse(self.NotificationPreference.objects.filter(user=self.bob).exists())
        self._post(self.alice, f"hey @{self.bob.username}")
        self.assertTrue(
            Notification.objects.filter(recipient=self.bob, notification_type="mention").exists()
        )
        pref = self.NotificationPreference.objects.get(user=self.bob)
        self.assertTrue(pref.mentions)
        self.assertTrue(pref.channel_wide)
        self.assertTrue(pref.thread_replies)

    def test_muting_room_suppresses_channel_wide_but_allows_direct_mention(self):
        self.RoomNotificationSetting.objects.create(
            room=self.room, user=self.bob, setting=self.RoomNotificationSetting.MUTED
        )
        self._post(self.alice, "@channel heads up")
        self.assertFalse(
            Notification.objects.filter(recipient=self.bob, notification_type="mention").exists()
        )
        # carol isn't muted, still gets the @channel notification.
        self.assertTrue(
            Notification.objects.filter(recipient=self.carol, notification_type="mention").exists()
        )

        Notification.objects.all().delete()
        self._post(self.alice, f"hey @{self.bob.username} direct mention")
        self.assertTrue(
            Notification.objects.filter(recipient=self.bob, notification_type="mention").exists()
        )

    def test_globally_disabling_mentions_suppresses_even_in_unmuted_room(self):
        self.NotificationPreference.objects.create(user=self.bob, mentions=False)
        self._post(self.alice, f"hey @{self.bob.username}")
        self.assertFalse(
            Notification.objects.filter(recipient=self.bob, notification_type="mention").exists()
        )

    def test_muted_room_still_blocks_mention_if_globally_disabled_too(self):
        self.NotificationPreference.objects.create(user=self.bob, mentions=False)
        self.RoomNotificationSetting.objects.create(
            room=self.room, user=self.bob, setting=self.RoomNotificationSetting.MUTED
        )
        self._post(self.alice, f"hey @{self.bob.username}")
        self.assertFalse(
            Notification.objects.filter(recipient=self.bob, notification_type="mention").exists()
        )

    def test_per_room_override_all_takes_precedence_over_global_off(self):
        self.NotificationPreference.objects.create(user=self.bob, channel_wide=False)
        self.RoomNotificationSetting.objects.create(
            room=self.room, user=self.bob, setting=self.RoomNotificationSetting.ALL
        )
        self._post(self.alice, "@channel heads up")
        self.assertTrue(
            Notification.objects.filter(recipient=self.bob, notification_type="mention").exists()
        )

    def test_thread_reply_respects_thread_replies_preference(self):
        self.NotificationPreference.objects.create(user=self.bob, thread_replies=False)
        root_id = self._post(self.alice, "root").data["id"]
        self._post(self.bob, "bob's reply", reply_to=root_id)  # auto-follows the thread
        Notification.objects.all().delete()

        self._post(self.carol, "carol's reply", reply_to=root_id)
        self.assertTrue(
            Notification.objects.filter(recipient=self.alice, notification_type="thread_reply").exists()
        )
        self.assertFalse(
            Notification.objects.filter(recipient=self.bob, notification_type="thread_reply").exists()
        )

    def test_mentions_only_room_override_blocks_thread_replies(self):
        self.RoomNotificationSetting.objects.create(
            room=self.room, user=self.bob, setting=self.RoomNotificationSetting.MENTIONS_ONLY
        )
        root_id = self._post(self.alice, "root").data["id"]
        self._post(self.bob, "bob's reply", reply_to=root_id)  # auto-follows the thread
        Notification.objects.all().delete()

        self._post(self.carol, "carol's reply", reply_to=root_id)
        self.assertFalse(
            Notification.objects.filter(recipient=self.bob, notification_type="thread_reply").exists()
        )

    def test_get_and_patch_global_preferences_api(self):
        self.client.force_login(self.bob)
        get_resp = self.client.get("/api/v1/notifications/preferences/")
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            get_resp.data, {"mentions": True, "channel_wide": True, "thread_replies": True}
        )

        patch_resp = self.client.patch(
            "/api/v1/notifications/preferences/", {"channel_wide": False}, format="json"
        )
        self.assertEqual(patch_resp.status_code, status.HTTP_200_OK)
        self.assertFalse(patch_resp.data["channel_wide"])
        pref = self.NotificationPreference.objects.get(user=self.bob)
        self.assertFalse(pref.channel_wide)

    def test_get_and_patch_room_notification_setting_api(self):
        self.client.force_login(self.bob)
        get_resp = self.client.get(f"/api/v1/rooms/{self.room.id}/notification-setting/")
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(get_resp.data["setting"], "default")

        patch_resp = self.client.patch(
            f"/api/v1/rooms/{self.room.id}/notification-setting/", {"setting": "muted"}, format="json"
        )
        self.assertEqual(patch_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(patch_resp.data["setting"], "muted")
        setting = self.RoomNotificationSetting.objects.get(room=self.room, user=self.bob)
        self.assertEqual(setting.setting, "muted")


class AttachmentAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.alice = User.objects.create_user(username="attachalice", password="pw12345")
        self.topic = Topic.objects.create(name="General")
        self.room = Room.objects.create(host=self.alice, topic=self.topic, name="Attach Room")
        self.client.force_login(self.alice)

    def test_valid_image_attachment_upload(self):
        upload = SimpleUploadedFile("pic.png", b"fake-png-bytes", content_type="image/png")
        response = self.client.post(
            f"/api/v1/rooms/{self.room.id}/messages/",
            {"body": "here's a pic", "attachment": upload},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        message = Message.objects.get(pk=response.data["id"])
        self.assertEqual(message.attachments.count(), 1)
        attachment = message.attachments.first()
        self.assertEqual(attachment.original_filename, "pic.png")
        self.assertEqual(attachment.content_type, "image/png")

    def test_oversized_attachment_rejected(self):
        big = SimpleUploadedFile(
            "big.png", b"x" * (11 * 1024 * 1024), content_type="image/png"
        )
        response = self.client.post(
            f"/api/v1/rooms/{self.room.id}/messages/",
            {"body": "too big", "attachment": big},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Message.objects.filter(body="too big").exists())

    def test_disallowed_content_type_rejected(self):
        exe = SimpleUploadedFile(
            "virus.exe", b"MZ...", content_type="application/x-msdownload"
        )
        response = self.client.post(
            f"/api/v1/rooms/{self.room.id}/messages/",
            {"body": "sketchy", "attachment": exe},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Message.objects.filter(body="sketchy").exists())


class AttachmentSignedDownloadTests(TestCase):
    """Slice 10: message attachments are no longer reachable via a raw
    /media/attachments/... URL — the only path is AttachmentDownloadView,
    gated by (1) a signed, time-limited token and (2) room membership."""

    def setUp(self):
        self.client = APIClient()
        self.alice = User.objects.create_user(username="sig_alice", password="pw12345")
        self.bob = User.objects.create_user(username="sig_bob", password="pw12345")
        self.outsider = User.objects.create_user(username="sig_outsider", password="pw12345")
        self.topic = Topic.objects.create(name="General")

        self.private_room = Room.objects.create(
            host=self.alice, topic=self.topic, name="Sig Private Room", is_private=True
        )
        RoomMembership.objects.create(room=self.private_room, user=self.alice, role="admin")
        RoomMembership.objects.create(room=self.private_room, user=self.bob, role="member")

        self.client.force_login(self.alice)
        upload = SimpleUploadedFile("secret plan.png", b"fake-png-bytes", content_type="image/png")
        response = self.client.post(
            f"/api/v1/rooms/{self.private_room.id}/messages/",
            {"body": "confidential", "attachment": upload},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.attachment = MessageAttachment.objects.get(message_id=response.data["id"])
        self.download_url = f"/api/v1/attachments/{self.attachment.id}/download/"

    def test_serializer_exposes_a_signed_download_url_not_a_raw_path(self):
        response = self.client.get(f"/api/v1/rooms/{self.private_room.id}/messages/")
        attachment_data = response.data["results"][0]["attachments"][0]
        self.assertNotIn("file", attachment_data)
        self.assertIn("download_url", attachment_data)
        self.assertIn("sig=", attachment_data["download_url"])
        self.assertIn(f"/api/v1/attachments/{self.attachment.id}/download/", attachment_data["download_url"])

    def test_valid_signature_and_room_member_can_download(self):
        token = sign_attachment_url(self.attachment.id)
        response = self.client.get(self.download_url, {"sig": token})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("secret plan.png", response["Content-Disposition"])
        self.assertEqual(b"".join(response.streaming_content), b"fake-png-bytes")

    def test_bob_is_a_room_member_and_can_also_download(self):
        self.client.force_login(self.bob)
        token = sign_attachment_url(self.attachment.id)
        response = self.client.get(self.download_url, {"sig": token})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_expired_signature_rejected(self):
        import time
        from django.core import signing as django_signing

        old_time = time.time() - (ATTACHMENT_URL_MAX_AGE_SECONDS + 60)
        with mock.patch.object(django_signing.time, "time", return_value=old_time):
            expired_token = sign_attachment_url(self.attachment.id)
        response = self.client.get(self.download_url, {"sig": expired_token})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_tampered_signature_rejected(self):
        token = sign_attachment_url(self.attachment.id)
        tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
        response = self.client.get(self.download_url, {"sig": tampered})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_signature_rejected(self):
        response = self.client.get(self.download_url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_valid_signature_but_non_member_of_private_room_rejected(self):
        self.client.force_login(self.outsider)
        token = sign_attachment_url(self.attachment.id)
        response = self.client.get(self.download_url, {"sig": token})
        # Matches this codebase's existing convention (PermissionsAuditTests)
        # of 404, not 403, for a non-member of a private room — doesn't leak
        # whether the attachment/message/room exists at all.
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_signature_for_a_different_attachment_id_is_not_valid(self):
        other_upload = SimpleUploadedFile("other.png", b"other-bytes", content_type="image/png")
        other_response = self.client.post(
            f"/api/v1/rooms/{self.private_room.id}/messages/",
            {"body": "another", "attachment": other_upload},
            format="multipart",
        )
        other_attachment = MessageAttachment.objects.get(message_id=other_response.data["id"])
        token_for_other = sign_attachment_url(other_attachment.id)
        response = self.client.get(self.download_url, {"sig": token_for_other})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unauthenticated_request_rejected_even_with_valid_signature(self):
        anon_client = APIClient()
        token = sign_attachment_url(self.attachment.id)
        response = anon_client.get(self.download_url, {"sig": token})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_raw_media_attachments_path_is_no_longer_servable(self):
        # Attachment files live in ATTACHMENTS_ROOT now, entirely outside
        # MEDIA_ROOT/MEDIA_URL — so even guessing the old-style path 404s,
        # regardless of auth.
        guessed_path = f"/media/attachments/{self.attachment.file.name.split('/')[-1]}"
        response = self.client.get(guessed_path)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class MessageCursorPaginationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.alice = User.objects.create_user(username="pagealice", password="pw12345")
        self.topic = Topic.objects.create(name="General")
        self.room = Room.objects.create(host=self.alice, topic=self.topic, name="Page Room")
        RoomMembership.objects.create(room=self.room, user=self.alice, role="admin")
        for i in range(50):
            Message.objects.create(user=self.alice, room=self.room, body=f"message {i}")
        self.client.force_login(self.alice)

    def test_first_page_returns_latest_messages_and_a_next_cursor(self):
        response = self.client.get(f"/api/v1/rooms/{self.room.id}/messages/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 40)
        # Newest-first ordering server-side (see pagination.py).
        self.assertEqual(results[0]["body"], "message 49")
        self.assertIsNotNone(response.data["next"])

    def test_next_cursor_returns_the_next_older_page_without_overlap(self):
        first = self.client.get(f"/api/v1/rooms/{self.room.id}/messages/")
        next_url = first.data["next"]
        cursor = next_url.split("cursor=")[1]
        second = self.client.get(
            f"/api/v1/rooms/{self.room.id}/messages/?cursor={cursor}"
        )
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        second_bodies = {m["body"] for m in second.data["results"]}
        first_bodies = {m["body"] for m in first.data["results"]}
        self.assertEqual(len(second_bodies), 10)
        self.assertEqual(second_bodies & first_bodies, set())
        self.assertIn("message 0", second_bodies)


# ---------------------------------------------------------------------------
# Idempotency (real-time infra hardening slice)
# ---------------------------------------------------------------------------


class MessageIdempotencyTests(TestCase):
    """A retried POST with the same client-generated client_id must return
    the already-created message instead of creating a duplicate row — this
    is what makes it safe for the frontend to resend a message create after
    an ambiguous network failure. Scoped per-(user, client_id): the same
    client_id value from a different user is a distinct message."""

    def setUp(self):
        self.client = APIClient()
        self.alice = User.objects.create_user(username="idemalice", password="pw12345")
        self.bob = User.objects.create_user(username="idembob", password="pw12345")
        self.topic = Topic.objects.create(name="General")
        self.room = Room.objects.create(host=self.alice, topic=self.topic, name="Room")

    def test_duplicate_client_id_returns_existing_message_no_duplicate_row(self):
        self.client.force_login(self.alice)
        payload = {"body": "hello", "client_id": "abc-123"}

        first = self.client.post(f"/api/v1/rooms/{self.room.id}/messages/", payload)
        self.assertEqual(first.status_code, status.HTTP_201_CREATED, first.data)
        self.assertEqual(Message.objects.filter(room=self.room).count(), 1)

        second = self.client.post(f"/api/v1/rooms/{self.room.id}/messages/", payload)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data["id"], first.data["id"])
        self.assertEqual(Message.objects.filter(room=self.room).count(), 1)

    def test_same_client_id_different_users_not_deduped(self):
        payload = {"body": "hello", "client_id": "shared-key"}

        self.client.force_login(self.alice)
        alice_resp = self.client.post(f"/api/v1/rooms/{self.room.id}/messages/", payload)
        self.assertEqual(alice_resp.status_code, status.HTTP_201_CREATED)

        self.client.force_login(self.bob)
        bob_resp = self.client.post(f"/api/v1/rooms/{self.room.id}/messages/", payload)
        self.assertEqual(bob_resp.status_code, status.HTTP_201_CREATED)

        self.assertNotEqual(alice_resp.data["id"], bob_resp.data["id"])
        self.assertEqual(Message.objects.filter(room=self.room, client_id="shared-key").count(), 2)

    def test_no_client_id_behaves_as_before(self):
        self.client.force_login(self.alice)
        first = self.client.post(f"/api/v1/rooms/{self.room.id}/messages/", {"body": "hi"})
        second = self.client.post(f"/api/v1/rooms/{self.room.id}/messages/", {"body": "hi"})
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(first.data["id"], second.data["id"])


class PrivateMessageIdempotencyTests(TestCase):
    """Same idempotency contract as MessageIdempotencyTests, for the
    private-message endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.alice = User.objects.create_user(username="pmidemalice", password="pw12345")
        self.bob = User.objects.create_user(username="pmidembob", password="pw12345")
        self.client.force_login(self.alice)
        conv_resp = self.client.post(
            "/api/v1/conversations/", {"participant_id": self.bob.id}
        )
        self.conversation_id = conv_resp.data["id"]

    def test_duplicate_client_id_returns_existing_message_no_duplicate_row(self):
        from .models import PrivateMessage

        payload = {"content": "hey", "client_id": "pm-abc-123"}
        url = f"/api/v1/conversations/{self.conversation_id}/messages/"

        first = self.client.post(url, payload)
        self.assertEqual(first.status_code, status.HTTP_201_CREATED, first.data)
        self.assertEqual(PrivateMessage.objects.filter(conversation_id=self.conversation_id).count(), 1)

        second = self.client.post(url, payload)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data["id"], first.data["id"])


class GroupConversationTests(TestCase):
    """DM/group-messaging slice: group conversation creation with 3+
    participants, broadcast reaching every participant (not just 2),
    per-participant read tracking, 1:1 get-or-create still working, and
    group creation *not* deduping like 1:1 does."""

    def setUp(self):
        from .models import PrivateConversation, PrivateMessage

        self.PrivateConversation = PrivateConversation
        self.PrivateMessage = PrivateMessage
        self.client = APIClient()
        self.alice = User.objects.create_user(username="gcalice", password="pw12345")
        self.bob = User.objects.create_user(username="gcbob", password="pw12345")
        self.carol = User.objects.create_user(username="gccarol", password="pw12345")

    def test_one_to_one_get_or_create_still_works(self):
        self.client.force_login(self.alice)
        first = self.client.post("/api/v1/conversations/", {"participant_id": self.bob.id})
        second = self.client.post("/api/v1/conversations/", {"participant_ids": [self.bob.id]})
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(
            self.PrivateConversation.objects.filter(participants=self.alice).count(), 1
        )

    def test_group_conversation_creation_with_three_plus_participants(self):
        self.client.force_login(self.alice)
        resp = self.client.post(
            "/api/v1/conversations/",
            {"participant_ids": [self.bob.id, self.carol.id]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        conversation = self.PrivateConversation.objects.get(id=resp.data["id"])
        self.assertEqual(
            set(conversation.participants.values_list("id", flat=True)),
            {self.alice.id, self.bob.id, self.carol.id},
        )
        self.assertIn("display_name", resp.data)
        self.assertIn("gcbob", resp.data["display_name"])
        self.assertIn("gccarol", resp.data["display_name"])

    def test_group_creation_does_not_dedupe(self):
        self.client.force_login(self.alice)
        first = self.client.post(
            "/api/v1/conversations/",
            {"participant_ids": [self.bob.id, self.carol.id]},
            format="json",
        )
        second = self.client.post(
            "/api/v1/conversations/",
            {"participant_ids": [self.bob.id, self.carol.id]},
            format="json",
        )
        self.assertNotEqual(first.data["id"], second.data["id"])
        self.assertEqual(
            self.PrivateConversation.objects.filter(participants=self.alice).count(), 2
        )

    def test_message_broadcast_reaches_all_participants(self):
        from unittest.mock import patch

        self.client.force_login(self.alice)
        conv_resp = self.client.post(
            "/api/v1/conversations/",
            {"participant_ids": [self.bob.id, self.carol.id]},
            format="json",
        )
        conversation_id = conv_resp.data["id"]

        with patch("playground.signals.async_to_sync") as mock_async_to_sync:
            post_resp = self.client.post(
                f"/api/v1/conversations/{conversation_id}/messages/",
                {"content": "hello everyone"},
            )
            self.assertEqual(post_resp.status_code, status.HTTP_201_CREATED, post_resp.data)

            sent_groups = {call.args[0] for call in mock_async_to_sync().call_args_list}
        self.assertEqual(
            sent_groups,
            {f"user_{self.alice.id}", f"user_{self.bob.id}", f"user_{self.carol.id}"},
        )

    def test_per_participant_read_tracking_accuracy(self):
        self.client.force_login(self.alice)
        conv_resp = self.client.post("/api/v1/conversations/", {"participant_id": self.bob.id})
        conversation_id = conv_resp.data["id"]
        conversation = self.PrivateConversation.objects.get(id=conversation_id)

        self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages/", {"content": "hi bob"}
        )
        # Bob hasn't viewed the conversation yet: 1 unread message for him,
        # 0 for alice (her own message never counts against herself).
        self.assertEqual(conversation.unread_count_for(self.bob), 1)
        self.assertEqual(conversation.unread_count_for(self.alice), 0)

        # Bob views the conversation's messages -> marks it read for him.
        self.client.force_login(self.bob)
        list_resp = self.client.get(f"/api/v1/conversations/{conversation_id}/messages/")
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(conversation.unread_count_for(self.bob), 0)

        # A second message from alice makes it unread for bob again, but
        # not for alice.
        self.client.force_login(self.alice)
        self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages/", {"content": "you there?"}
        )
        self.assertEqual(conversation.unread_count_for(self.bob), 1)
        self.assertEqual(conversation.unread_count_for(self.alice), 0)


class ThreadingAPITests(TestCase):
    """Threading slice: reply-to-root resolution, reply_count, the thread
    GET endpoint, follow/unfollow, and the thread_reply notification
    fan-out (see api_views.py's MessageListCreateView._handle_thread_reply,
    MessageThreadView, MessageFollowView, and models.py's ThreadFollow)."""

    def setUp(self):
        from .models import ThreadFollow

        self.ThreadFollow = ThreadFollow
        self.client = APIClient()
        self.alice = User.objects.create_user(username="threadalice", password="pw12345")
        self.bob = User.objects.create_user(username="threadbob", password="pw12345")
        self.carol = User.objects.create_user(username="threadcarol", password="pw12345")
        self.room = Room.objects.create(host=self.alice, name="thread-room")

    def _post(self, user, body, reply_to=None):
        self.client.force_login(user)
        payload = {"body": body}
        if reply_to is not None:
            payload["reply_to"] = reply_to
        return self.client.post(f"/api/v1/rooms/{self.room.id}/messages/", payload)

    def test_reply_to_a_reply_resolves_to_thread_root(self):
        root_resp = self._post(self.alice, "root message")
        root_id = root_resp.data["id"]

        reply1_resp = self._post(self.bob, "first reply", reply_to=root_id)
        reply1_id = reply1_resp.data["id"]
        self.assertEqual(reply1_resp.data["reply_to"], root_id)

        # Replying to the reply should still resolve onto the root, not
        # chain onto reply1.
        reply2_resp = self._post(self.carol, "reply to the reply", reply_to=reply1_id)
        self.assertEqual(reply2_resp.data["reply_to"], root_id)

        root = Message.objects.get(pk=root_id)
        self.assertEqual(set(root.replies.values_list("id", flat=True)), {reply1_id, reply2_resp.data["id"]})

    def test_reply_count_accurate(self):
        root_resp = self._post(self.alice, "root")
        root_id = root_resp.data["id"]
        self._post(self.bob, "reply one", reply_to=root_id)
        self._post(self.carol, "reply two", reply_to=root_id)

        list_resp = self.client.get(f"/api/v1/rooms/{self.room.id}/messages/")
        root_row = next(m for m in list_resp.data["results"] if m["id"] == root_id)
        self.assertEqual(root_row["reply_count"], 2)
        self.assertTrue(root_row["is_thread_root"])

        reply_row = next(m for m in list_resp.data["results"] if m["id"] != root_id)
        self.assertEqual(reply_row["reply_count"], 0)
        self.assertFalse(reply_row["is_thread_root"])

    def test_thread_get_returns_root_and_replies_in_order(self):
        root_resp = self._post(self.alice, "root")
        root_id = root_resp.data["id"]
        r1 = self._post(self.bob, "reply 1", reply_to=root_id).data["id"]
        r2 = self._post(self.carol, "reply 2", reply_to=root_id).data["id"]

        self.client.force_login(self.alice)
        resp = self.client.get(f"/api/v1/rooms/{self.room.id}/messages/{root_id}/thread/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [m["id"] for m in resp.data]
        self.assertEqual(ids, [root_id, r1, r2])

        # Also reachable via a reply's own id (resolves to the same root).
        resp2 = self.client.get(f"/api/v1/rooms/{self.room.id}/messages/{r1}/thread/")
        self.assertEqual([m["id"] for m in resp2.data], [root_id, r1, r2])

    def test_follow_unfollow_toggles(self):
        root_id = self._post(self.alice, "root").data["id"]

        self.client.force_login(self.bob)
        follow_resp = self.client.post(f"/api/v1/rooms/{self.room.id}/messages/{root_id}/follow/")
        self.assertEqual(follow_resp.status_code, status.HTTP_200_OK)
        self.assertTrue(follow_resp.data["following"])
        self.assertTrue(
            self.ThreadFollow.objects.filter(root_message_id=root_id, user=self.bob).exists()
        )

        unfollow_resp = self.client.delete(f"/api/v1/rooms/{self.room.id}/messages/{root_id}/follow/")
        self.assertEqual(unfollow_resp.status_code, status.HTTP_200_OK)
        self.assertFalse(unfollow_resp.data["following"])
        self.assertFalse(
            self.ThreadFollow.objects.filter(root_message_id=root_id, user=self.bob).exists()
        )

    def test_reply_notifies_existing_followers_not_the_replier(self):
        root_id = self._post(self.alice, "root").data["id"]
        # bob replies -> auto-follows the thread.
        self._post(self.bob, "bob's reply", reply_to=root_id)
        Notification.objects.all().delete()  # clear mention/thread noise from setup

        # carol now replies: alice (root author) and bob (existing replier)
        # should each get a thread_reply notification; carol should not.
        self._post(self.carol, "carol's reply", reply_to=root_id)

        alice_notif = Notification.objects.get(recipient=self.alice, notification_type="thread_reply")
        self.assertEqual(alice_notif.sender, self.carol)
        bob_notif = Notification.objects.get(recipient=self.bob, notification_type="thread_reply")
        self.assertEqual(bob_notif.sender, self.carol)
        self.assertFalse(
            Notification.objects.filter(recipient=self.carol, notification_type="thread_reply").exists()
        )

    def test_root_author_auto_follows_own_thread_on_first_reply(self):
        root_id = self._post(self.alice, "root").data["id"]
        self.assertFalse(
            self.ThreadFollow.objects.filter(root_message_id=root_id, user=self.alice).exists()
        )

        self._post(self.bob, "first reply", reply_to=root_id)

        self.assertTrue(
            self.ThreadFollow.objects.filter(root_message_id=root_id, user=self.alice).exists()
        )


# ---------------------------------------------------------------------------
# Permissions audit (RBAC/authorization hardening slice)
# ---------------------------------------------------------------------------


class PermissionsAuditTests(TestCase):
    """Proves the gaps found in the permissions audit are closed:

    - a non-member of a private room can't reach a message's thread/
      reactions/follow endpoints by ID, even though those don't go through
      RoomViewSet's own (correctly-filtered) listing;
    - a non-member can't fetch/patch another room's notification setting
      by guessing its room id (RoomNotificationSettingView had no
      membership check at all before this slice);
    - the "guest" workspace role now has real restrictions: can't create
      rooms, can't see/join a public room they weren't explicitly added
      to, can't be promoted to a room's admin.
    """

    def setUp(self):
        self.client = APIClient()
        self.alice = User.objects.create_user(username="paudit_alice", password="pw12345")
        self.bob = User.objects.create_user(username="paudit_bob", password="pw12345")
        self.outsider = User.objects.create_user(username="paudit_outsider", password="pw12345")
        self.topic = Topic.objects.create(name="General")

        self.private_room = Room.objects.create(
            host=self.alice, topic=self.topic, name="Private Audit Room", is_private=True
        )
        RoomMembership.objects.create(room=self.private_room, user=self.alice, role="admin")
        RoomMembership.objects.create(room=self.private_room, user=self.bob, role="member")

        self.client.force_login(self.alice)
        self.root_message = Message.objects.create(
            user=self.alice, room=self.private_room, body="root message"
        )
        self.reply_message = Message.objects.create(
            user=self.bob, room=self.private_room, body="a reply", reply_to=self.root_message
        )

    # -- nested-by-id resources must re-verify private-room membership --

    def test_non_member_cannot_fetch_thread_by_id(self):
        self.client.force_login(self.outsider)
        response = self.client.get(
            f"/api/v1/rooms/{self.private_room.id}/messages/{self.root_message.id}/thread/"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_non_member_cannot_toggle_reaction_by_id(self):
        self.client.force_login(self.outsider)
        response = self.client.post(
            f"/api/v1/rooms/{self.private_room.id}/messages/{self.root_message.id}/reactions/",
            {"emoji": "👍"},
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_non_member_cannot_follow_thread_by_id(self):
        self.client.force_login(self.outsider)
        response = self.client.post(
            f"/api/v1/rooms/{self.private_room.id}/messages/{self.root_message.id}/follow/"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_member_can_still_fetch_thread_and_react(self):
        self.client.force_login(self.bob)
        response = self.client.get(
            f"/api/v1/rooms/{self.private_room.id}/messages/{self.root_message.id}/thread/"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

        response = self.client.post(
            f"/api/v1/rooms/{self.private_room.id}/messages/{self.root_message.id}/reactions/",
            {"emoji": "🔥"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    # -- self-scoped personal resource: RoomNotificationSettingView --

    def test_non_member_cannot_get_room_notification_setting(self):
        self.client.force_login(self.outsider)
        response = self.client.get(
            f"/api/v1/rooms/{self.private_room.id}/notification-setting/"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_non_member_cannot_patch_room_notification_setting(self):
        self.client.force_login(self.outsider)
        response = self.client.patch(
            f"/api/v1/rooms/{self.private_room.id}/notification-setting/",
            {"setting": "muted"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_member_can_read_own_room_notification_setting(self):
        self.client.force_login(self.bob)
        response = self.client.get(
            f"/api/v1/rooms/{self.private_room.id}/notification-setting/"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    # -- guest role restrictions --

    def _make_guest(self, user):
        # Mutate the SAME UserProfile instance already cached on
        # `user.userprofile` (created by signals.py's create_user_profile
        # when the User was saved above), not a separately-queried one —
        # see RegisterView's docstring for exactly this footgun:
        # signals.py's save_user_profile re-saves whatever object is
        # cached on `user.userprofile` every time `user.save()` runs (e.g.
        # the update_last_login save force_login() below triggers), which
        # would silently overwrite a role set via a separate query back to
        # the stale cached default.
        profile = user.userprofile
        profile.role = UserProfile.ROLE_GUEST
        profile.save(update_fields=["role"])
        return profile

    def test_guest_cannot_create_room(self):
        guest = User.objects.create_user(username="paudit_guest1", password="pw12345")
        self._make_guest(guest)
        self.client.force_login(guest)
        response = self.client.post(
            "/api/v1/rooms/", {"name": "Guest Room", "topic_id": self.topic.id}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Room.objects.filter(name="Guest Room").exists())

    def test_guest_does_not_see_public_room_they_were_not_added_to(self):
        public_room = Room.objects.create(
            host=self.alice, topic=self.topic, name="Public Audit Room"
        )
        RoomMembership.objects.create(room=public_room, user=self.alice, role="admin")

        guest = User.objects.create_user(username="paudit_guest2", password="pw12345")
        self._make_guest(guest)
        self.client.force_login(guest)

        list_response = self.client.get("/api/v1/rooms/")
        names = [r["name"] for r in list_response.data]
        self.assertNotIn("Public Audit Room", names)

        detail_response = self.client.get(f"/api/v1/rooms/{public_room.id}/")
        self.assertEqual(detail_response.status_code, status.HTTP_404_NOT_FOUND)

        messages_response = self.client.get(f"/api/v1/rooms/{public_room.id}/messages/")
        self.assertEqual(messages_response.status_code, status.HTTP_404_NOT_FOUND)

    def test_guest_sees_room_they_were_explicitly_added_to(self):
        public_room = Room.objects.create(
            host=self.alice, topic=self.topic, name="Public Audit Room 2"
        )
        RoomMembership.objects.create(room=public_room, user=self.alice, role="admin")

        guest = User.objects.create_user(username="paudit_guest3", password="pw12345")
        self._make_guest(guest)
        RoomMembership.objects.create(room=public_room, user=guest, role="member")
        self.client.force_login(guest)

        list_response = self.client.get("/api/v1/rooms/")
        names = [r["name"] for r in list_response.data]
        self.assertIn("Public Audit Room 2", names)

        detail_response = self.client.get(f"/api/v1/rooms/{public_room.id}/")
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)

    def test_guest_cannot_be_promoted_to_room_admin(self):
        public_room = Room.objects.create(
            host=self.alice, topic=self.topic, name="Promotion Test Room"
        )
        RoomMembership.objects.create(room=public_room, user=self.alice, role="admin")

        guest = User.objects.create_user(username="paudit_guest4", password="pw12345")
        self._make_guest(guest)
        RoomMembership.objects.create(room=public_room, user=guest, role="member")

        self.client.force_login(self.alice)
        response = self.client.patch(
            f"/api/v1/rooms/{public_room.id}/members/{guest.id}/",
            {"role": "admin"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        membership = RoomMembership.objects.get(room=public_room, user=guest)
        self.assertEqual(membership.role, "member")

    def test_guest_cannot_be_added_directly_as_room_admin(self):
        public_room = Room.objects.create(
            host=self.alice, topic=self.topic, name="Direct Add Admin Room"
        )
        RoomMembership.objects.create(room=public_room, user=self.alice, role="admin")

        guest = User.objects.create_user(username="paudit_guest5", password="pw12345")
        self._make_guest(guest)

        self.client.force_login(self.alice)
        response = self.client.post(
            f"/api/v1/rooms/{public_room.id}/members/",
            {"user_id": guest.id, "role": "admin"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(RoomMembership.objects.filter(room=public_room, user=guest).exists())


# ---------------------------------------------------------------------------
# Search (GET /api/v1/search/)
# ---------------------------------------------------------------------------


class SearchAPITests(TestCase):
    """Search must reuse the exact same room-visibility rule the rest of the
    app enforces (see permissions.visible_rooms_for) — the most important
    thing to prove here is that a private room's content is NEVER
    discoverable through search by someone who isn't a member, since that's
    directly the kind of leak the last two slices worked to close off."""

    def setUp(self):
        self.client = APIClient()
        self.alice = User.objects.create_user(username="search_alice", password="pw12345")
        self.bob = User.objects.create_user(username="search_bob", password="pw12345")
        self.outsider = User.objects.create_user(username="search_outsider", password="pw12345")
        self.topic = Topic.objects.create(name="General")

        self.private_room = Room.objects.create(
            host=self.alice, topic=self.topic, name="Search Secret Room", is_private=True
        )
        RoomMembership.objects.create(room=self.private_room, user=self.alice, role="admin")
        RoomMembership.objects.create(room=self.private_room, user=self.bob, role="member")

        self.public_room = Room.objects.create(
            host=self.alice, topic=self.topic, name="Search Public Room", description="open to all"
        )
        RoomMembership.objects.create(room=self.public_room, user=self.alice, role="admin")

        self.secret_message = Message.objects.create(
            user=self.alice, room=self.private_room, body="the launch codes are hidden here"
        )
        self.public_message = Message.objects.create(
            user=self.alice, room=self.public_room, body="launch codes are public knowledge"
        )

    # -- messages: the privacy-critical case --

    def test_message_search_excludes_private_room_content_for_non_member(self):
        self.client.force_login(self.outsider)
        response = self.client.get("/api/v1/search/", {"q": "launch codes", "type": "all"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        bodies = [m["body"] for m in response.data["messages"]]
        self.assertNotIn(self.secret_message.body, bodies)
        self.assertIn(self.public_message.body, bodies)

    def test_message_search_includes_private_room_content_for_member(self):
        self.client.force_login(self.bob)
        response = self.client.get("/api/v1/search/", {"q": "launch codes", "type": "all"})
        bodies = [m["body"] for m in response.data["messages"]]
        self.assertIn(self.secret_message.body, bodies)
        self.assertIn(self.public_message.body, bodies)

    def test_message_search_excludes_soft_deleted_messages(self):
        deleted = Message.objects.create(
            user=self.alice, room=self.public_room, body="launch codes to be deleted"
        )
        deleted.is_deleted = True
        deleted.deleted_at = timezone.now()
        deleted.body = ""
        deleted.save(update_fields=["is_deleted", "deleted_at", "body"])

        self.client.force_login(self.alice)
        response = self.client.get("/api/v1/search/", {"q": "launch codes", "type": "all"})
        ids = [m["id"] for m in response.data["messages"]]
        self.assertNotIn(deleted.id, ids)

    def test_message_search_includes_archived_room_messages(self):
        # Archiving only blocks new posts, not read access to history (see
        # RoomViewSet.get_queryset — archived rooms are excluded only from
        # the default `list`, still directly retrievable/readable) — search
        # follows that same "still readable" story rather than treating
        # archiving as a visibility cutoff.
        self.public_room.is_archived = True
        self.public_room.save(update_fields=["is_archived"])

        self.client.force_login(self.alice)
        response = self.client.get("/api/v1/search/", {"q": "launch codes", "type": "all"})
        bodies = [m["body"] for m in response.data["messages"]]
        self.assertIn(self.public_message.body, bodies)

    def test_message_search_result_includes_room_context(self):
        self.client.force_login(self.alice)
        response = self.client.get("/api/v1/search/", {"q": "launch codes", "type": "all"})
        hit = next(m for m in response.data["messages"] if m["id"] == self.public_message.id)
        self.assertEqual(hit["room"]["id"], self.public_room.id)
        self.assertEqual(hit["room"]["name"], self.public_room.name)

    # -- rooms: same visibility + guest restrictions --

    def test_room_search_hidden_from_non_member(self):
        self.client.force_login(self.outsider)
        response = self.client.get("/api/v1/search/", {"q": "Search Secret", "type": "all"})
        names = [r["name"] for r in response.data["rooms"]]
        self.assertNotIn(self.private_room.name, names)

    def test_room_search_visible_to_member(self):
        self.client.force_login(self.bob)
        response = self.client.get("/api/v1/search/", {"q": "Search Secret", "type": "all"})
        names = [r["name"] for r in response.data["rooms"]]
        self.assertIn(self.private_room.name, names)

    def test_room_search_matches_description(self):
        self.client.force_login(self.alice)
        response = self.client.get("/api/v1/search/", {"q": "open to all", "type": "all"})
        names = [r["name"] for r in response.data["rooms"]]
        self.assertIn(self.public_room.name, names)

    def test_room_search_respects_guest_restrictions(self):
        # A guest can't see a public room they weren't explicitly added to
        # (mirrors RoomViewSet.get_queryset's guest carve-out) — search must
        # not leak it into results either, since it reuses the same
        # visible_rooms_for() helper.
        guest = User.objects.create_user(username="search_guest", password="pw12345")
        guest.userprofile.role = UserProfile.ROLE_GUEST
        guest.userprofile.save()

        self.client.force_login(guest)
        response = self.client.get("/api/v1/search/", {"q": "Search Public", "type": "all"})
        names = [r["name"] for r in response.data["rooms"]]
        self.assertNotIn(self.public_room.name, names)

    # -- users: existing directory baseline, no extra scoping --

    def test_user_search_matches_username(self):
        self.client.force_login(self.outsider)
        response = self.client.get("/api/v1/search/", {"q": "search_bob", "type": "all"})
        usernames = [u["username"] for u in response.data["users"]]
        self.assertIn(self.bob.username, usernames)

    def test_user_search_matches_bio(self):
        self.bob.userprofile.bio = "loves distributed systems"
        self.bob.userprofile.save()
        self.client.force_login(self.outsider)
        response = self.client.get("/api/v1/search/", {"q": "distributed systems", "type": "all"})
        usernames = [u["username"] for u in response.data["users"]]
        self.assertIn(self.bob.username, usernames)

    # -- capping / paginated single-type views --

    def test_preview_caps_results_per_category(self):
        for i in range(25):
            Message.objects.create(user=self.alice, room=self.public_room, body=f"cap-test message {i}")
        self.client.force_login(self.alice)
        response = self.client.get("/api/v1/search/", {"q": "cap-test", "type": "all"})
        self.assertLessEqual(len(response.data["messages"]), 20)

    def test_single_type_messages_view_is_paginated(self):
        # Reuses RoomMessageCursorPagination (page_size=40 — see
        # pagination.py) rather than the ?type=all preview's 20-per-category
        # cap, so this creates more than 40 matches to prove paging kicks in.
        for i in range(50):
            Message.objects.create(user=self.alice, room=self.public_room, body=f"page-test message {i}")
        self.client.force_login(self.alice)
        response = self.client.get("/api/v1/search/", {"q": "page-test", "type": "messages"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)
        self.assertIn("next", response.data)
        self.assertEqual(len(response.data["results"]), 40)
        self.assertIsNotNone(response.data["next"])

    def test_single_type_rooms_view_is_paginated(self):
        self.client.force_login(self.alice)
        response = self.client.get("/api/v1/search/", {"q": "Search", "type": "rooms"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

    def test_single_type_users_view_is_paginated(self):
        self.client.force_login(self.alice)
        response = self.client.get("/api/v1/search/", {"q": "search_", "type": "users"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

    # -- misc --

    def test_search_requires_authentication(self):
        response = self.client.get("/api/v1/search/", {"q": "launch"})
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_empty_query_returns_empty_results(self):
        self.client.force_login(self.alice)
        response = self.client.get("/api/v1/search/", {"q": "", "type": "all"})
        self.assertEqual(response.data, {"messages": [], "rooms": [], "users": []})

    def test_invalid_type_rejected(self):
        self.client.force_login(self.alice)
        response = self.client.get("/api/v1/search/", {"q": "launch", "type": "bogus"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# Admin / moderation slice
# ---------------------------------------------------------------------------


class AdminModerationTests(TestCase):
    """Covers the admin/moderation slice: AuditEvent rows written for the
    existing admin-gated actions, ban enforcement at login + the DRF
    permission layer, timeout blocking posts but not reads, the blocked-
    keyword hard-reject, and the workspace-vs-room-admin ban split.
    """

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(username="modtest_owner", password="pw12345")
        self.owner.userprofile.role = UserProfile.ROLE_OWNER
        self.owner.userprofile.save(update_fields=["role"])

        self.room_admin = User.objects.create_user(username="modtest_roomadmin", password="pw12345")
        self.member = User.objects.create_user(username="modtest_member", password="pw12345")
        self.target = User.objects.create_user(username="modtest_target", password="pw12345")

        self.topic = Topic.objects.create(name="ModTest")
        self.room = Room.objects.create(host=self.room_admin, topic=self.topic, name="Mod Room")
        RoomMembership.objects.create(room=self.room, user=self.room_admin, role="admin")
        RoomMembership.objects.create(room=self.room, user=self.member, role="member")
        RoomMembership.objects.create(room=self.room, user=self.target, role="member")

    # -- audit log: role change / member removal (RoomMemberDetailView) --

    def test_role_change_writes_audit_event(self):
        self.client.force_login(self.room_admin)
        response = self.client.patch(
            f"/api/v1/rooms/{self.room.id}/members/{self.member.id}/",
            {"role": "admin"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        event = AuditEvent.objects.filter(action=AuditEvent.ACTION_ROLE_CHANGED, target_user=self.member).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.actor, self.room_admin)
        self.assertEqual(event.target_room, self.room)

    def test_member_removal_by_admin_writes_audit_event(self):
        self.client.force_login(self.room_admin)
        response = self.client.delete(f"/api/v1/rooms/{self.room.id}/members/{self.target.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertTrue(
            AuditEvent.objects.filter(action=AuditEvent.ACTION_MEMBER_REMOVED, target_user=self.target).exists()
        )

    def test_self_leave_does_not_write_audit_event(self):
        self.client.force_login(self.target)
        response = self.client.delete(f"/api/v1/rooms/{self.room.id}/members/{self.target.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            AuditEvent.objects.filter(action=AuditEvent.ACTION_MEMBER_REMOVED, target_user=self.target).exists()
        )

    # -- audit log: room archive / delete (RoomViewSet) --

    def test_room_archive_writes_audit_event(self):
        self.client.force_login(self.room_admin)
        response = self.client.post(f"/api/v1/rooms/{self.room.id}/archive/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            AuditEvent.objects.filter(action=AuditEvent.ACTION_ROOM_ARCHIVED, target_room=self.room).exists()
        )

    def test_room_delete_writes_audit_event_surviving_room_deletion(self):
        room_id = self.room.id
        self.client.force_login(self.owner)
        response = self.client.delete(f"/api/v1/rooms/{room_id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        event = AuditEvent.objects.filter(action=AuditEvent.ACTION_ROOM_DELETED).first()
        self.assertIsNotNone(event)
        # SET_NULL: the room row is gone, but the event survives with the
        # name preserved in `detail`.
        self.assertIsNone(event.target_room)
        self.assertIn("Mod Room", event.detail)

    # -- audit log: message removal by admin, not self-delete --

    def test_message_removed_by_admin_writes_audit_event(self):
        message = Message.objects.create(user=self.target, room=self.room, body="bad message")
        self.client.force_login(self.room_admin)
        response = self.client.delete(f"/api/v1/rooms/{self.room.id}/messages/{message.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            AuditEvent.objects.filter(action=AuditEvent.ACTION_MESSAGE_REMOVED, target_message=message).exists()
        )

    def test_self_delete_message_does_not_write_audit_event(self):
        message = Message.objects.create(user=self.member, room=self.room, body="my own message")
        self.client.force_login(self.member)
        response = self.client.delete(f"/api/v1/rooms/{self.room.id}/messages/{message.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            AuditEvent.objects.filter(action=AuditEvent.ACTION_MESSAGE_REMOVED, target_message=message).exists()
        )

    # -- audit log: invitation create/revoke --

    def test_invitation_create_and_revoke_write_audit_events(self):
        self.client.force_login(self.owner)
        create_resp = self.client.post("/api/v1/invitations/", {"role": "member"}, format="json")
        self.assertEqual(create_resp.status_code, status.HTTP_201_CREATED, create_resp.data)
        self.assertTrue(AuditEvent.objects.filter(action=AuditEvent.ACTION_INVITATION_CREATED).exists())

        invite_id = create_resp.data["id"]
        revoke_resp = self.client.delete(f"/api/v1/invitations/{invite_id}/")
        self.assertEqual(revoke_resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertTrue(AuditEvent.objects.filter(action=AuditEvent.ACTION_INVITATION_REVOKED).exists())

    # -- ban enforcement: login layer + DRF permission layer --

    def test_banned_user_rejected_at_login(self):
        self.target.userprofile.is_banned = True
        self.target.userprofile.save(update_fields=["is_banned"])
        response = self.client.post(
            "/api/v1/auth/login/",
            {"username": "modtest_target", "password": "pw12345"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_banned_user_with_existing_session_rejected_at_permission_layer(self):
        # Log in FIRST (while not yet banned) — this is the "already has a
        # valid session cookie" case the permission-layer check exists for.
        self.client.force_login(self.target)
        ok = self.client.get("/api/v1/rooms/")
        self.assertEqual(ok.status_code, status.HTTP_200_OK)

        self.target.userprofile.is_banned = True
        self.target.userprofile.save(update_fields=["is_banned"])

        blocked = self.client.get("/api/v1/rooms/")
        self.assertEqual(blocked.status_code, status.HTTP_403_FORBIDDEN)

    def test_room_admin_cannot_issue_workspace_ban(self):
        self.client.force_login(self.room_admin)
        response = self.client.post(
            "/api/v1/admin/moderation/",
            {"target_user": self.target.id, "action_type": "ban"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.target.userprofile.refresh_from_db()
        self.assertFalse(self.target.userprofile.is_banned)

    def test_owner_can_ban_and_unban(self):
        self.client.force_login(self.owner)
        ban_resp = self.client.post(
            "/api/v1/admin/moderation/",
            {"target_user": self.target.id, "action_type": "ban", "reason": "spamming"},
            format="json",
        )
        self.assertEqual(ban_resp.status_code, status.HTTP_201_CREATED, ban_resp.data)
        self.target.userprofile.refresh_from_db()
        self.assertTrue(self.target.userprofile.is_banned)
        self.assertTrue(AuditEvent.objects.filter(action=AuditEvent.ACTION_USER_BANNED, target_user=self.target).exists())

        unban_resp = self.client.post(
            "/api/v1/admin/moderation/unban/",
            {"target_user": self.target.id},
            format="json",
        )
        self.assertEqual(unban_resp.status_code, status.HTTP_200_OK)
        self.target.userprofile.refresh_from_db()
        self.assertFalse(self.target.userprofile.is_banned)
        self.assertTrue(
            AuditEvent.objects.filter(action=AuditEvent.ACTION_USER_UNBANNED, target_user=self.target).exists()
        )

    # -- timeout: blocks posting, not reading --

    def test_timed_out_user_blocked_from_posting_but_can_read(self):
        self.client.force_login(self.owner)
        timeout_resp = self.client.post(
            "/api/v1/admin/moderation/",
            {
                "target_user": self.target.id,
                "action_type": "timeout",
                "expires_at": (timezone.now() + timezone.timedelta(hours=1)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(timeout_resp.status_code, status.HTTP_201_CREATED, timeout_resp.data)

        self.client.force_login(self.target)
        read_resp = self.client.get(f"/api/v1/rooms/{self.room.id}/messages/")
        self.assertEqual(read_resp.status_code, status.HTTP_200_OK)

        post_resp = self.client.post(
            f"/api/v1/rooms/{self.room.id}/messages/",
            {"body": "let me in"},
            format="json",
        )
        self.assertEqual(post_resp.status_code, status.HTTP_403_FORBIDDEN)

    # -- keyword filter: hard reject --

    def test_blocked_keyword_rejects_matching_message(self):
        self.client.force_login(self.owner)
        create_resp = self.client.post(
            "/api/v1/admin/blocked-keywords/", {"phrase": "forbiddenword"}, format="json"
        )
        self.assertEqual(create_resp.status_code, status.HTTP_201_CREATED, create_resp.data)
        self.assertTrue(AuditEvent.objects.filter(action=AuditEvent.ACTION_KEYWORD_ADDED).exists())

        self.client.force_login(self.member)
        post_resp = self.client.post(
            f"/api/v1/rooms/{self.room.id}/messages/",
            {"body": "this has a ForbiddenWord in it"},
            format="json",
        )
        self.assertEqual(post_resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Message.objects.filter(room=self.room, body__icontains="ForbiddenWord").exists())

        clean_resp = self.client.post(
            f"/api/v1/rooms/{self.room.id}/messages/",
            {"body": "this one is clean"},
            format="json",
        )
        self.assertEqual(clean_resp.status_code, status.HTTP_201_CREATED)

    def test_room_admin_cannot_reach_admin_endpoints(self):
        # Sanity check the split: a room admin (not workspace admin/owner)
        # is blocked from ALL /api/v1/admin/* endpoints, not just moderation.
        self.client.force_login(self.room_admin)
        self.assertEqual(
            self.client.get("/api/v1/admin/audit-log/").status_code, status.HTTP_403_FORBIDDEN
        )
        self.assertEqual(
            self.client.get("/api/v1/admin/blocked-keywords/").status_code, status.HTTP_403_FORBIDDEN
        )


def _throttled_rest_framework():
    """A copy of settings.REST_FRAMEWORK with a much stricter 'messages'/
    'user' throttle rate, for RateLimitTests below. django.test.override_settings
    on the REST_FRAMEWORK key triggers DRF's own setting_changed listener
    (rest_framework.settings.reload_api_settings), which correctly resets
    its cached, parsed rate — no manual cache-busting needed."""
    cfg = dict(settings.REST_FRAMEWORK)
    cfg["DEFAULT_THROTTLE_RATES"] = {"user": "2/min", "messages": "2/min"}
    return cfg


class RateLimitTests(TestCase):
    """DRF throttling (settings.py's REST_FRAMEWORK DEFAULT_THROTTLE_*):
    proves message creation actually returns 429 once its scoped rate is
    exceeded. Uses override_settings to drop the 'messages' scope's rate to
    something a handful of quick requests can exceed, rather than the real
    30/min — throttling counts requests within a time WINDOW using a cache
    counter, not real elapsed time, so this doesn't need to sleep."""

    def setUp(self):
        self.client = APIClient()
        self.alice = User.objects.create_user(username="ratelimit_alice", password="pw12345")
        self.topic = Topic.objects.create(name="RateLimit")
        self.room = Room.objects.create(host=self.alice, topic=self.topic, name="Rate Room")
        RoomMembership.objects.create(room=self.room, user=self.alice, role="admin")
        self.client.force_login(self.alice)
        # Throttle counters live in Django's cache; clear so an earlier
        # test's counter (same ident: this user) can't bleed into this one.
        from django.core.cache import cache

        cache.clear()
        self.addCleanup(cache.clear)

    @override_settings(REST_FRAMEWORK=_throttled_rest_framework())
    def test_message_creation_throttled_returns_429(self):
        statuses = []
        for i in range(4):
            resp = self.client.post(
                f"/api/v1/rooms/{self.room.id}/messages/",
                {"body": f"msg {i}"},
                format="json",
            )
            statuses.append(resp.status_code)
        self.assertIn(status.HTTP_429_TOO_MANY_REQUESTS, statuses)


# ---------------------------------------------------------------------------
# AI slice: FishoAI mention/summarize/random-reply trigger, context
# boundaries, usage limit, and AIInteraction auditability.
# ---------------------------------------------------------------------------


class AISliceTests(TestCase):
    """Covers the safety-critical and functional requirements of the AI
    slice (playground/ai_helper.py, MessageListCreateView._maybe_trigger_ai_reply,
    playground/models.py's AIInteraction):

      - AI context (AIAssistant.build_room_context) never crosses rooms.
      - Soft-deleted messages are excluded from that context.
      - The per-user hourly usage limit actually stops further triggering.
      - AIInteraction rows are written on both the success and failure path.
      - The "@fishoai summarize" command is recognized as trigger_type
        "summarize" rather than falling through to the generic mention path.

    Blanks out GEMINI_API_KEY/OPENAI_API_KEY for the duration of every test
    here so AIAssistant() always takes its mock-response fallback instead of
    calling the real Gemini API over the network (a real key is present in
    this repo's .env for the live deploy) — keeps these tests fast and
    deterministic without touching ai_helper.py's own fallback logic.
    """

    def setUp(self):
        from .ai_helper import AIAssistant

        self.AIAssistant = AIAssistant
        self.env_patch = mock.patch.dict(
            os.environ, {"GEMINI_API_KEY": "", "OPENAI_API_KEY": ""}
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

        self.client = APIClient()
        self.alice = User.objects.create_user(username="aialice", password="pw12345")
        self.bob = User.objects.create_user(username="aibob", password="pw12345")
        self.topic = Topic.objects.create(name="General")
        self.room_a = Room.objects.create(host=self.alice, topic=self.topic, name="Room A")
        self.room_b = Room.objects.create(host=self.bob, topic=self.topic, name="Room B")
        RoomMembership.objects.create(room=self.room_a, user=self.alice, role="admin")
        RoomMembership.objects.create(room=self.room_b, user=self.bob, role="admin")

    def test_context_never_crosses_rooms(self):
        Message.objects.create(user=self.alice, room=self.room_a, body="hello from room A")
        Message.objects.create(user=self.bob, room=self.room_b, body="secret room B content")
        Message.objects.create(user=self.bob, room=self.room_b, body="more room B secrets")

        ai = self.AIAssistant()
        context = ai.build_room_context(self.room_a)

        self.assertEqual(len(context), 1)
        self.assertEqual(context[0], ("aialice", "hello from room A"))
        bodies = [body for _, body in context]
        self.assertNotIn("secret room B content", bodies)
        self.assertNotIn("more room B secrets", bodies)

    def test_soft_deleted_messages_excluded_from_context(self):
        kept = Message.objects.create(user=self.alice, room=self.room_a, body="keep me")
        deleted = Message.objects.create(user=self.alice, room=self.room_a, body="admin removed this")
        deleted.is_deleted = True
        deleted.save(update_fields=["is_deleted"])

        ai = self.AIAssistant()
        context = ai.build_room_context(self.room_a)

        bodies = [body for _, body in context]
        self.assertIn("keep me", bodies)
        self.assertNotIn("admin removed this", bodies)

    def test_summarize_command_recognized(self):
        Message.objects.create(user=self.alice, room=self.room_a, body="earlier message")
        self.client.force_login(self.alice)
        response = self.client.post(
            f"/api/v1/rooms/{self.room_a.id}/messages/",
            {"body": "@fishoai summarize this thread"},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        interaction = AIInteraction.objects.filter(user=self.alice, room=self.room_a).latest("created_at")
        self.assertEqual(interaction.trigger_type, AIInteraction.TRIGGER_SUMMARIZE)
        self.assertTrue(interaction.succeeded)
        self.assertGreaterEqual(interaction.prompt_context_message_count, 1)

    def test_plain_mention_recorded_as_mention_type_and_creates_bot_reply(self):
        self.client.force_login(self.alice)
        response = self.client.post(
            f"/api/v1/rooms/{self.room_a.id}/messages/",
            {"body": "@fishoai how are you?"},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        interaction = AIInteraction.objects.filter(user=self.alice, room=self.room_a).latest("created_at")
        self.assertEqual(interaction.trigger_type, AIInteraction.TRIGGER_MENTION)
        self.assertTrue(interaction.succeeded)
        self.assertIsNotNone(interaction.response_message)
        self.assertEqual(interaction.response_message.user.username, "FishoAI")

    def test_usage_limit_stops_further_triggering(self):
        from .api_views import MessageListCreateView

        limit = MessageListCreateView.AI_HOURLY_LIMIT
        now = timezone.now()
        for _ in range(limit):
            AIInteraction.objects.create(
                user=self.alice,
                room=self.room_a,
                trigger_type=AIInteraction.TRIGGER_MENTION,
                succeeded=True,
                created_at=now,
            )
        # created_at is auto_now_add, so force it back to "now" (well within
        # the last hour) after creation.
        AIInteraction.objects.filter(user=self.alice).update(created_at=now)

        self.client.force_login(self.alice)
        before_count = AIInteraction.objects.filter(user=self.alice).count()
        response = self.client.post(
            f"/api/v1/rooms/{self.room_a.id}/messages/",
            {"body": "@fishoai are you still there?"},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        after_count = AIInteraction.objects.filter(user=self.alice).count()
        self.assertEqual(before_count, limit)
        # No new AIInteraction row (skip is silent — no call was made) and
        # no bot reply message.
        self.assertEqual(after_count, limit)
        self.assertFalse(
            Message.objects.filter(room=self.room_a, user__username="FishoAI").exists()
        )

    def test_ai_interaction_recorded_on_failure_path(self):
        self.client.force_login(self.alice)
        with mock.patch.object(self.AIAssistant, "handle_mention", side_effect=RuntimeError("gemini down")):
            response = self.client.post(
                f"/api/v1/rooms/{self.room_a.id}/messages/",
                {"body": "@fishoai are you there?"},
            )
        # The user's own message still posts successfully even though the
        # AI call blew up.
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        interaction = AIInteraction.objects.filter(user=self.alice, room=self.room_a).latest("created_at")
        self.assertEqual(interaction.trigger_type, AIInteraction.TRIGGER_MENTION)
        self.assertFalse(interaction.succeeded)
        self.assertIsNone(interaction.response_message)
        self.assertFalse(
            Message.objects.filter(room=self.room_a, user__username="FishoAI").exists()
        )


class _SyncThread:
    """Drop-in stand-in for threading.Thread used by WebhookTests below:
    runs the target function synchronously on .start() instead of spawning
    a real background thread, so tests can assert on WebhookDelivery rows
    (written by signals.py's _deliver_webhook, the thread's target)
    deterministically without a sleep/poll loop."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


class _FakeThreadingModule:
    """Stand-in for the `threading` module name inside playground.signals
    ONLY (patched via mock.patch("playground.signals.threading", ...)) —
    NOT a patch of the real threading.Thread attribute, which would also
    break asgiref's own internal thread pool (used by
    async_to_sync(channel_layer.group_send) a few lines earlier in the same
    signal handler) since `import threading` everywhere binds to the same
    module object."""

    Thread = _SyncThread


class WebhookTests(TestCase):
    """Integrations/webhooks slice: outgoing delivery (signature + audit
    trail) hooked into the existing Message post_save signal, incoming
    delivery (external POST -> Message via a bot identity), rate limiting,
    and management-endpoint authorization."""

    def setUp(self):
        import hashlib
        import hmac
        import json

        self.hashlib = hashlib
        self.hmac = hmac
        self.json = json

        self.client = APIClient()
        self.alice = User.objects.create_user(username="webhook_alice", password="pw12345")
        self.bob = User.objects.create_user(username="webhook_bob", password="pw12345")
        self.topic = Topic.objects.create(name="Webhooks")
        self.room = Room.objects.create(host=self.alice, topic=self.topic, name="Webhook Room")
        RoomMembership.objects.create(room=self.room, user=self.alice, role="admin")
        RoomMembership.objects.create(room=self.room, user=self.bob, role="member")

        from django.core.cache import cache

        cache.clear()
        self.addCleanup(cache.clear)

    def _create_webhook(self, **overrides):
        defaults = dict(
            room=self.room,
            target_url="https://example.com/hooks/fishofisho",
            event_types=[Webhook.EVENT_MESSAGE_CREATED],
            created_by=self.alice,
            is_active=True,
        )
        defaults.update(overrides)
        return Webhook.objects.create(**defaults)

    # ---- outgoing ----

    def test_outgoing_webhook_fires_signs_and_records_delivery_on_message_create(self):
        webhook = self._create_webhook()

        mock_response = mock.Mock(status_code=200)
        with mock.patch("playground.signals.threading", _FakeThreadingModule), \
             mock.patch("playground.signals.requests.post", return_value=mock_response) as mock_post:
            self.client.force_login(self.alice)
            response = self.client.post(
                f"/api/v1/rooms/{self.room.id}/messages/", {"body": "hello world"}
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args.kwargs
        self.assertEqual(mock_post.call_args.args[0], webhook.target_url)
        sent_body = call_kwargs["data"]
        expected_signature = self.hmac.new(
            webhook.secret.encode("utf-8"), sent_body, self.hashlib.sha256
        ).hexdigest()
        self.assertEqual(call_kwargs["headers"]["X-FishoFisho-Signature"], expected_signature)
        self.assertEqual(call_kwargs["headers"]["X-FishoFisho-Event"], Webhook.EVENT_MESSAGE_CREATED)

        payload = self.json.loads(sent_body)
        self.assertEqual(payload["event"], Webhook.EVENT_MESSAGE_CREATED)
        self.assertEqual(payload["message"]["body"], "hello world")
        self.assertEqual(payload["message"]["sender"], "webhook_alice")

        delivery = WebhookDelivery.objects.get(webhook=webhook)
        self.assertTrue(delivery.succeeded)
        self.assertEqual(delivery.response_status, 200)
        self.assertEqual(delivery.event_type, Webhook.EVENT_MESSAGE_CREATED)

    def test_inactive_webhook_does_not_fire(self):
        self._create_webhook(is_active=False)

        with mock.patch("playground.signals.threading", _FakeThreadingModule), \
             mock.patch("playground.signals.requests.post") as mock_post:
            self.client.force_login(self.alice)
            response = self.client.post(
                f"/api/v1/rooms/{self.room.id}/messages/", {"body": "hello again"}
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        mock_post.assert_not_called()
        self.assertEqual(WebhookDelivery.objects.count(), 0)

    # ---- incoming ----

    def test_incoming_webhook_valid_token_creates_message(self):
        webhook = self._create_webhook(target_url="")
        response = self.client.post(
            f"/api/v1/webhooks/incoming/{webhook.incoming_token}/",
            {"text": "posted from an external service"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        message = Message.objects.get(room=self.room, body="posted from an external service")
        self.assertEqual(message.user.username, "Webhook")

    def test_incoming_webhook_invalid_token_rejected(self):
        response = self.client.post(
            "/api/v1/webhooks/incoming/not-a-real-token/", {"text": "hi"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_incoming_webhook_archived_room_rejected(self):
        self.room.is_archived = True
        self.room.save(update_fields=["is_archived"])
        webhook = self._create_webhook(target_url="")
        response = self.client.post(
            f"/api/v1/webhooks/incoming/{webhook.incoming_token}/", {"text": "hi"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_incoming_webhook_blocked_keyword_rejected(self):
        BlockedKeyword.objects.create(phrase="spammylink", added_by=self.alice)
        webhook = self._create_webhook(target_url="")
        response = self.client.post(
            f"/api/v1/webhooks/incoming/{webhook.incoming_token}/",
            {"text": "check out this spammylink now"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_incoming_webhook_rate_limited(self):
        from .api_views import INCOMING_WEBHOOK_RATE_LIMIT

        webhook = self._create_webhook(target_url="")
        statuses = []
        for i in range(INCOMING_WEBHOOK_RATE_LIMIT + 1):
            response = self.client.post(
                f"/api/v1/webhooks/incoming/{webhook.incoming_token}/",
                {"text": f"msg {i}"},
                format="json",
            )
            statuses.append(response.status_code)
        self.assertIn(status.HTTP_429_TOO_MANY_REQUESTS, statuses)

    # ---- management authorization ----

    def test_only_room_admin_can_list_create_delete_webhooks(self):
        webhook = self._create_webhook()

        self.client.force_login(self.bob)  # plain member, not room admin
        list_resp = self.client.get(f"/api/v1/rooms/{self.room.id}/webhooks/")
        self.assertEqual(list_resp.status_code, status.HTTP_403_FORBIDDEN)

        create_resp = self.client.post(
            f"/api/v1/rooms/{self.room.id}/webhooks/",
            {"target_url": "https://example.com/other-hook"},
            format="json",
        )
        self.assertEqual(create_resp.status_code, status.HTTP_403_FORBIDDEN)

        delete_resp = self.client.delete(f"/api/v1/rooms/{self.room.id}/webhooks/{webhook.id}/")
        self.assertEqual(delete_resp.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_login(self.alice)  # room admin
        list_resp = self.client.get(f"/api/v1/rooms/{self.room.id}/webhooks/")
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)

        create_resp = self.client.post(
            f"/api/v1/rooms/{self.room.id}/webhooks/",
            {"target_url": "https://example.com/other-hook"},
            format="json",
        )
        self.assertEqual(create_resp.status_code, status.HTTP_201_CREATED, create_resp.data)
        self.assertIn("secret", create_resp.data)
        # A subsequent list never re-exposes the secret.
        list_resp = self.client.get(f"/api/v1/rooms/{self.room.id}/webhooks/")
        self.assertTrue(all("secret" not in row for row in list_resp.data))

        delete_resp = self.client.delete(f"/api/v1/rooms/{self.room.id}/webhooks/{webhook.id}/")
        self.assertEqual(delete_resp.status_code, status.HTTP_204_NO_CONTENT)

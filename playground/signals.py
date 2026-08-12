import hashlib
import hmac
import json
import logging
import threading

import requests
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.renderers import JSONRenderer

from .models import Message, Notification, PrivateMessage, Reaction, UserProfile, Webhook, WebhookDelivery

logger = logging.getLogger("playground")

# Outbound HTTP timeout for webhook delivery — short on purpose so a slow or
# dead external endpoint can't tie up a background thread (and therefore a
# worker's thread pool / process resources) indefinitely. See
# dispatch_outgoing_webhooks below.
WEBHOOK_TIMEOUT_SECONDS = 5

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    # Use try-except to handle cases where profile might not exist yet
    try:
        instance.userprofile.save()
    except UserProfile.DoesNotExist:
        UserProfile.objects.create(user=instance)


def _to_json_safe(data):
    """Render DRF serializer .data through JSONRenderer so UUIDs/datetimes
    become plain JSON-safe primitives before handing them to the channel
    layer (keeps this forward-compatible with channels_redis, whose
    serializer can't handle UUID/datetime objects directly)."""
    return json.loads(JSONRenderer().render(data))


@receiver(post_save, sender=Message)
def push_message_to_room_group(sender, instance, created, **kwargs):
    # Broadcast type depends on *why* this save happened:
    #   - created            -> "message.new"
    #   - PATCH edit         -> "message.updated" (api_views.py's edit path
    #                           saves with update_fields=[..., "edited_at", ...])
    #   - soft delete        -> "message.deleted" (save with update_fields
    #                           including "is_deleted" — a flip, not an
    #                           actual post_delete, since deletes are soft)
    # Any other bare .save() (e.g. Message.objects.create() for the AI bot
    # reply, which goes through created=True above) doesn't match any of
    # these and is simply not re-broadcast.
    update_fields = kwargs.get("update_fields")
    if created:
        event_type = "message.new"
    elif update_fields and "is_deleted" in update_fields:
        event_type = "message.deleted"
    elif update_fields and "edited_at" in update_fields:
        event_type = "message.updated"
    else:
        return

    # Imported lazily to avoid any import-order issues at app startup.
    from .serializers import MessageSerializer

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    payload = _to_json_safe(MessageSerializer(instance).data)
    async_to_sync(channel_layer.group_send)(
        f"room_{instance.room_id}",
        {"type": event_type, "message": payload},
    )

    # Integrations/webhooks slice: hook into this SAME signal point (rather
    # than inventing a second, parallel "event system") to fan the message
    # out to any outgoing Webhook rows subscribed to it. Only wired up for
    # "message.created" (created=True, i.e. event_type == "message.new")
    # for now — edits/deletes don't fire an outgoing webhook yet, matching
    # the task's scope of "the one event type you actually need to wire
    # up". Deliberately AFTER the WS broadcast above so a slow/misbehaving
    # webhook dispatch can never delay the real-time in-app broadcast.
    if event_type == "message.new":
        dispatch_outgoing_webhooks(instance)


def _build_webhook_payload(message):
    return {
        "event": Webhook.EVENT_MESSAGE_CREATED,
        "room": {"id": message.room_id, "name": message.room.name},
        "message": {
            "id": message.id,
            "body": message.body,
            "sender": message.user.username if message.user_id else None,
            "created_at": message.created.isoformat(),
        },
        "timestamp": timezone.now().isoformat(),
    }


def _deliver_webhook(webhook_id, event_type, payload):
    """Runs on a background thread (see dispatch_outgoing_webhooks) — does
    the actual outbound HTTP POST plus records exactly one WebhookDelivery
    row for the attempt, success or failure. Never raises back to its
    caller (it IS the thread target, so nothing would catch it anyway) —
    every failure mode (network error, timeout, non-2xx response, even a
    bug in this function itself) is swallowed and turned into a recorded
    delivery / log line instead of a crashed background thread.

    PRODUCTION TODO: this whole dispatch is a `threading.Thread(daemon=True)`
    fire-and-forget rather than a real task queue (Celery/RQ) with retries,
    backoff, and a durable job store — deliberately, since no task-queue
    infrastructure exists anywhere else in this project yet (see
    CHANNEL_LAYERS'/EMAIL_BACKEND's own "swap for the real thing in
    production" comments in settings.py for the same reasoning). A daemon
    thread is good enough to not block the request and to not leak
    resources on a dead endpoint (WEBHOOK_TIMEOUT_SECONDS), but it does NOT
    retry a failed delivery and a delivery started right as the process
    exits can be lost. Celery/RQ + a durable broker is the natural upgrade
    path once a real integration needs at-least-once delivery guarantees.
    """
    body = json.dumps(payload).encode("utf-8")
    response_status = None
    succeeded = False
    error = ""
    try:
        webhook = Webhook.objects.filter(pk=webhook_id, is_active=True).first()
        if webhook is None:
            return  # Deactivated/deleted between dispatch and delivery.

        signature = hmac.new(webhook.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        try:
            response = requests.post(
                webhook.target_url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-FishoFisho-Signature": signature,
                    "X-FishoFisho-Event": event_type,
                },
                timeout=WEBHOOK_TIMEOUT_SECONDS,
            )
            response_status = response.status_code
            succeeded = 200 <= response.status_code < 300
            if not succeeded:
                error = f"Non-2xx response: {response.status_code}"
        except requests.RequestException as exc:
            error = str(exc)

        WebhookDelivery.objects.create(
            webhook=webhook,
            event_type=event_type,
            payload=payload,
            response_status=response_status,
            succeeded=succeeded,
            error=error,
        )
    except Exception:
        # Last-resort guard: even a bug in the bookkeeping above (e.g. a DB
        # error) must not crash this background thread silently unnoticed.
        logger.exception("Webhook delivery bookkeeping failed for webhook_id=%s", webhook_id)


def dispatch_outgoing_webhooks(message):
    """Looks up every active Webhook subscribed to "message.created" for
    `message.room`, and fires each one off on its own daemon background
    thread — see _deliver_webhook for the actual HTTP call + delivery
    bookkeeping. Kept synchronous-but-cheap here (just a DB query + thread
    spawn per webhook); the potentially slow part (the network call) always
    happens off the request thread.
    """
    webhooks = Webhook.objects.filter(
        room_id=message.room_id, is_active=True, target_url__gt=""
    )
    matching = [w for w in webhooks if Webhook.EVENT_MESSAGE_CREATED in (w.event_types or [])]
    if not matching:
        return

    payload = _build_webhook_payload(message)
    for webhook in matching:
        threading.Thread(
            target=_deliver_webhook,
            args=(webhook.id, Webhook.EVENT_MESSAGE_CREATED, payload),
            daemon=True,
        ).start()


def _broadcast_reaction_change(message):
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    payload = _to_json_safe(message.reaction_summary())
    async_to_sync(channel_layer.group_send)(
        f"room_{message.room_id}",
        {
            "type": "message.reactions_changed",
            "message_id": message.id,
            "reactions": payload,
        },
    )


@receiver(post_save, sender=Reaction)
def push_reaction_added(sender, instance, **kwargs):
    _broadcast_reaction_change(instance.message)


@receiver(post_delete, sender=Reaction)
def push_reaction_removed(sender, instance, **kwargs):
    _broadcast_reaction_change(instance.message)


@receiver(post_save, sender=Notification)
def push_notification_to_user_group(sender, instance, created, **kwargs):
    # Only broadcast newly created notifications (event type is
    # "notification.new"); later updates (e.g. is_read toggles) aren't
    # re-broadcast as "new".
    if not created:
        return

    from .serializers import NotificationSerializer

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    payload = _to_json_safe(NotificationSerializer(instance).data)
    async_to_sync(channel_layer.group_send)(
        f"user_{instance.recipient_id}",
        {"type": "notification.new", "notification": payload},
    )


@receiver(post_save, sender=PrivateMessage)
def push_private_message_to_participants(sender, instance, created, **kwargs):
    # Only broadcast newly created DMs. Every participant (not just sender +
    # a single receiver — conversations can now have 3+ people) already
    # auto-joins their own "user_<id>" group on connect (see consumers.py),
    # so no separate join/leave step is needed the way rooms require one.
    if not created:
        return

    from .serializers import PrivateMessageSerializer

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    payload = _to_json_safe(PrivateMessageSerializer(instance).data)
    event = {
        "type": "private_message.new",
        "conversation_id": str(instance.conversation_id),
        "message": payload,
    }
    participant_ids = instance.conversation.participants.values_list("id", flat=True)
    for user_id in participant_ids:
        async_to_sync(channel_layer.group_send)(f"user_{user_id}", event)
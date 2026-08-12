"""Admin/moderation surface — workspace admin/owner only. Split out of
api_views.py (already large) into its own module: these endpoints are all
new in this slice and don't share request-lifecycle logic with the
room/message/conversation views, so there's no strong reason to interleave
them into that file.
"""

from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .audit import log_audit_event
from .models import AIInteraction, AuditEvent, BlockedKeyword, ModerationAction, UserProfile
from .pagination import AIInteractionCursorPagination, AuditLogCursorPagination
from .permissions import IsAuthenticatedNotBanned as IsAuthenticated, IsWorkspaceAdminOrOwner
from .serializers import (
    AIInteractionSerializer,
    AuditEventSerializer,
    BlockedKeywordSerializer,
    ModerationActionSerializer,
)


class AuditLogView(generics.ListAPIView):
    """GET /api/v1/admin/audit-log/ — paginated, newest first. Workspace
    admin/owner only (IsWorkspaceAdminOrOwner already does its own auth
    check, but IsAuthenticated is listed first for consistency with every
    other admin-gated view in this codebase — see InvitationViewSet).

    Filterable by:
      - ``?action=<AuditEvent.ACTION_* value>``
      - ``?actor=<user id>`` — who performed the action
      - ``?target_user=<user id>`` — who/what it was done to
    """

    serializer_class = AuditEventSerializer
    permission_classes = [IsAuthenticated, IsWorkspaceAdminOrOwner]
    pagination_class = AuditLogCursorPagination

    def get_queryset(self):
        qs = AuditEvent.objects.select_related(
            "actor", "actor__userprofile", "target_user", "target_user__userprofile", "target_room", "target_message"
        ).all()
        action = self.request.query_params.get("action")
        actor = self.request.query_params.get("actor")
        target_user = self.request.query_params.get("target_user")
        if action:
            qs = qs.filter(action=action)
        if actor:
            qs = qs.filter(actor_id=actor)
        if target_user:
            qs = qs.filter(target_user_id=target_user)
        return qs


class ModerationActionView(APIView):
    """/api/v1/admin/moderation/ — workspace admin/owner only.

    - ``GET ?target_user=<id>`` — that user's moderation history (all
      warnings/timeouts/bans/unbans, newest first). ``target_user`` omitted
      returns the most recent actions across everyone (capped at 200 rows —
      this is a simple history view, not a paginated feed).
    - ``POST {"target_user": <id>, "action_type": "warning"|"timeout"|"ban",
      "reason"?: str, "expires_at"?: iso datetime}`` — issues the action.
      ``expires_at`` is REQUIRED for "timeout" (it's what
      UserProfile.timed_out_until gets set to) and ignored for
      warning/ban. Room admins cannot reach this at all —
      IsWorkspaceAdminOrOwner is workspace-scoped only, deliberately NOT
      IsRoomAdminOrWorkspaceAdmin: a room admin should never be able to
      ban/timeout someone workspace-wide just because they administer one
      room.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceAdminOrOwner]

    def get(self, request):
        qs = ModerationAction.objects.select_related("target_user", "issued_by").all()
        target_user = request.query_params.get("target_user")
        if target_user:
            qs = qs.filter(target_user_id=target_user)
        qs = qs.order_by("-created_at")[:200]
        return Response(ModerationActionSerializer(qs, many=True).data)

    def post(self, request):
        action_type = request.data.get("action_type")
        if action_type not in (ModerationAction.WARNING, ModerationAction.TIMEOUT, ModerationAction.BAN):
            raise ValidationError({"action_type": ["Must be one of: warning, timeout, ban."]})

        target_user_id = request.data.get("target_user")
        target_user = get_object_or_404(User, pk=target_user_id)
        reason = (request.data.get("reason") or "").strip()

        expires_at = None
        if action_type == ModerationAction.TIMEOUT:
            raw_expires = request.data.get("expires_at")
            expires_at = parse_datetime(raw_expires) if raw_expires else None
            if expires_at is None:
                raise ValidationError(
                    {"expires_at": ["A future ISO datetime is required for a timeout."]}
                )
            if timezone.is_naive(expires_at):
                expires_at = timezone.make_aware(expires_at)
            if expires_at <= timezone.now():
                raise ValidationError({"expires_at": ["Must be in the future."]})

        profile, _ = UserProfile.objects.get_or_create(user=target_user)
        action = ModerationAction.objects.create(
            target_user=target_user,
            action_type=action_type,
            issued_by=request.user,
            reason=reason,
            expires_at=expires_at,
        )

        # UserProfile.is_banned/timed_out_until are set directly here
        # (rather than via a signal on ModerationAction.save()) — this is
        # the only place ModerationAction rows of these types are ever
        # created, so a signal would just be indirection with no other
        # caller to justify it.
        if action_type == ModerationAction.BAN:
            profile.is_banned = True
            profile.save(update_fields=["is_banned"])
            log_audit_event(
                request.user,
                AuditEvent.ACTION_USER_BANNED,
                target_user=target_user,
                detail=reason or "banned",
            )
        elif action_type == ModerationAction.TIMEOUT:
            profile.timed_out_until = expires_at
            profile.save(update_fields=["timed_out_until"])
            log_audit_event(
                request.user,
                AuditEvent.ACTION_USER_TIMED_OUT,
                target_user=target_user,
                detail=f"until {expires_at.isoformat()}" + (f" — {reason}" if reason else ""),
            )
        else:  # warning
            log_audit_event(
                request.user,
                AuditEvent.ACTION_USER_WARNED,
                target_user=target_user,
                detail=reason or "warned",
            )

        return Response(ModerationActionSerializer(action).data, status=status.HTTP_201_CREATED)


class UnbanView(APIView):
    """POST /api/v1/admin/moderation/unban/ ``{"target_user": <id>, "reason"?}``
    — workspace admin/owner only. Separate endpoint (rather than
    action_type="unban" on ModerationActionView.post) since unbanning isn't
    "issue a new restriction", it's "lift an existing one" — a distinct
    enough operation to warrant its own URL rather than overloading the
    issue-action endpoint's request shape.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceAdminOrOwner]

    def post(self, request):
        target_user_id = request.data.get("target_user")
        target_user = get_object_or_404(User, pk=target_user_id)
        reason = (request.data.get("reason") or "").strip()

        profile, _ = UserProfile.objects.get_or_create(user=target_user)
        profile.is_banned = False
        profile.save(update_fields=["is_banned"])

        action = ModerationAction.objects.create(
            target_user=target_user,
            action_type=ModerationAction.UNBAN,
            issued_by=request.user,
            reason=reason,
        )
        log_audit_event(
            request.user, AuditEvent.ACTION_USER_UNBANNED, target_user=target_user, detail=reason or "unbanned"
        )
        return Response(ModerationActionSerializer(action).data)


class BlockedKeywordListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/admin/blocked-keywords/ — workspace admin/owner only."""

    serializer_class = BlockedKeywordSerializer
    permission_classes = [IsAuthenticated, IsWorkspaceAdminOrOwner]
    queryset = BlockedKeyword.objects.all()

    def perform_create(self, serializer):
        keyword = serializer.save(added_by=self.request.user)
        log_audit_event(
            self.request.user, AuditEvent.ACTION_KEYWORD_ADDED, detail=f"phrase: {keyword.phrase}"
        )


class BlockedKeywordDetailView(generics.DestroyAPIView):
    """DELETE /api/v1/admin/blocked-keywords/<pk>/ — workspace admin/owner only."""

    queryset = BlockedKeyword.objects.all()
    permission_classes = [IsAuthenticated, IsWorkspaceAdminOrOwner]

    def perform_destroy(self, instance):
        log_audit_event(
            self.request.user, AuditEvent.ACTION_KEYWORD_REMOVED, detail=f"phrase: {instance.phrase}"
        )
        instance.delete()


class AIInteractionListView(generics.ListAPIView):
    """GET /api/v1/admin/ai-interactions/ — workspace admin/owner only,
    cursor-paginated, newest first. Auditability surface for the AI slice
    (playground/ai_helper.py, MessageListCreateView._maybe_trigger_ai_reply):
    every AI trigger (mention/random/summarize, success or failure) writes
    one AIInteraction row, this just exposes them the same way
    AuditLogView exposes AuditEvent.

    Filterable by:
      - ``?user=<user id>`` — who triggered it
      - ``?room=<room id>`` — which room
      - ``?trigger_type=<mention|random|summarize>``
    """

    serializer_class = AIInteractionSerializer
    permission_classes = [IsAuthenticated, IsWorkspaceAdminOrOwner]
    pagination_class = AIInteractionCursorPagination

    def get_queryset(self):
        qs = AIInteraction.objects.select_related("user", "room").all()
        user = self.request.query_params.get("user")
        room = self.request.query_params.get("room")
        trigger_type = self.request.query_params.get("trigger_type")
        if user:
            qs = qs.filter(user_id=user)
        if room:
            qs = qs.filter(room_id=room)
        if trigger_type:
            qs = qs.filter(trigger_type=trigger_type)
        return qs

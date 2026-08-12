from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import admin_views, api_views

router = DefaultRouter()
router.register(r"rooms", api_views.RoomViewSet, basename="api-room")
router.register(r"topics", api_views.TopicViewSet, basename="api-topic")
router.register(r"users", api_views.UserViewSet, basename="api-user")
router.register(
    r"notifications", api_views.NotificationViewSet, basename="api-notification"
)
router.register(
    r"conversations",
    api_views.PrivateConversationViewSet,
    basename="api-conversation",
)
router.register(
    r"invitations", api_views.InvitationViewSet, basename="api-invitation"
)

urlpatterns = [
    path("", include(router.urls)),
    path("auth/login/", api_views.LoginView.as_view(), name="api-auth-login"),
    path("auth/register/", api_views.RegisterView.as_view(), name="api-auth-register"),
    path("auth/logout/", api_views.LogoutView.as_view(), name="api-auth-logout"),
    path("auth/csrf/", api_views.CsrfView.as_view(), name="api-auth-csrf"),
    path(
        "auth/password-reset/",
        api_views.PasswordResetRequestView.as_view(),
        name="api-auth-password-reset",
    ),
    path(
        "auth/password-reset-confirm/",
        api_views.PasswordResetConfirmView.as_view(),
        name="api-auth-password-reset-confirm",
    ),
    path(
        "rooms/<int:room_pk>/messages/",
        api_views.MessageListCreateView.as_view(),
        name="api-room-messages",
    ),
    path(
        "rooms/<int:room_pk>/messages/<int:pk>/",
        api_views.MessageDetailView.as_view(),
        name="api-room-message-detail",
    ),
    path(
        "rooms/<int:room_pk>/messages/<int:pk>/reactions/",
        api_views.MessageReactionView.as_view(),
        name="api-room-message-reactions",
    ),
    path(
        "rooms/<int:room_pk>/messages/<int:pk>/thread/",
        api_views.MessageThreadView.as_view(),
        name="api-room-message-thread",
    ),
    path(
        "rooms/<int:room_pk>/messages/<int:pk>/follow/",
        api_views.MessageFollowView.as_view(),
        name="api-room-message-follow",
    ),
    path(
        "rooms/<int:room_pk>/notification-setting/",
        api_views.RoomNotificationSettingView.as_view(),
        name="api-room-notification-setting",
    ),
    path(
        "rooms/<int:room_pk>/members/",
        api_views.RoomMemberListCreateView.as_view(),
        name="api-room-members",
    ),
    path(
        "rooms/<int:room_pk>/members/<int:user_id>/",
        api_views.RoomMemberDetailView.as_view(),
        name="api-room-member-detail",
    ),
    path(
        "conversations/<uuid:conversation_pk>/messages/",
        api_views.PrivateMessageListCreateView.as_view(),
        name="api-conversation-messages",
    ),
    path(
        "profile/me/",
        api_views.ProfileView.as_view(),
        name="api-profile-me",
    ),
    path(
        "attachments/<int:pk>/download/",
        api_views.AttachmentDownloadView.as_view(),
        name="api-attachment-download",
    ),
    path("search/", api_views.SearchView.as_view(), name="api-search"),
    # --- admin/moderation slice ---
    path("admin/audit-log/", admin_views.AuditLogView.as_view(), name="api-admin-audit-log"),
    path("admin/moderation/", admin_views.ModerationActionView.as_view(), name="api-admin-moderation"),
    path(
        "admin/moderation/unban/",
        admin_views.UnbanView.as_view(),
        name="api-admin-moderation-unban",
    ),
    path(
        "admin/blocked-keywords/",
        admin_views.BlockedKeywordListCreateView.as_view(),
        name="api-admin-blocked-keywords",
    ),
    path(
        "admin/blocked-keywords/<int:pk>/",
        admin_views.BlockedKeywordDetailView.as_view(),
        name="api-admin-blocked-keyword-detail",
    ),
    # --- AI slice ---
    path(
        "admin/ai-interactions/",
        admin_views.AIInteractionListView.as_view(),
        name="api-admin-ai-interactions",
    ),
    # --- integrations/webhooks slice ---
    path(
        "rooms/<int:room_pk>/webhooks/",
        api_views.WebhookListCreateView.as_view(),
        name="api-room-webhooks",
    ),
    path(
        "rooms/<int:room_pk>/webhooks/<int:pk>/",
        api_views.WebhookDetailView.as_view(),
        name="api-room-webhook-detail",
    ),
    path(
        "webhooks/incoming/<str:token>/",
        api_views.IncomingWebhookView.as_view(),
        name="api-webhook-incoming",
    ),
]

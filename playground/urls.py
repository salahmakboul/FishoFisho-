from django.urls import path
from . import views
from django.conf import settings
from django.conf.urls.static import static
urlpatterns = [
path('login/',views.loginPage,name="login"),
path('logout/',views.logoutUSER,name="logout"),
path('register/',views.registerUser,name="register"),
path('hello/', views.hello),
path('room/<int:pk>/',views.room,name="room"),
path('edit-profile/', views.edit_profile, name='edit-profile'),
path('profile/<int:pk>/',views.userProfile,name="user-profile"),
 path('notifications/', views.notifications, name='notifications'),
path('notifications/read/<uuid:notification_id>/', views.mark_notification_read, name='mark-notification-read'),
path('notifications/read-all/', views.mark_all_read, name='mark-all-read'),
path('notifications/unread-count/', views.get_unread_count, name='unread-count'),
path('api/users/', views.get_users, name='get-users'),
path('',views.home ,name="home"),
path('create-room/',views.createRoom,name="create-room"),
path('update-room/<int:pk>/',views.updateRoom,name="update-room"),
path('delete-room/<int:pk>/',views.deleteRoom,name="delete-room"),
 path('inbox/', views.inbox, name='inbox'),
path('messages/<str:user_id>/', views.private_chat, name='private-chat'),
path('start-chat/<str:user_id>/', views.start_chat, name='start-chat'),
path('delete-conversation/<uuid:conversation_id>/', views.delete_conversation, name='delete-conversation'),
path('users/', views.users_directory, name='users-directory'),
path('delete-message/<int:pk>/',views.deleteMessage,name="delete-message")
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

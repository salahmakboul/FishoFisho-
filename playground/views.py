from urllib import request
from django.contrib import messages
from django.shortcuts import render,redirect
from django.contrib.auth import authenticate,login,logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from .models import Notification
from django.http import HttpRequest, HttpResponse
from django.db.models import Q
from .models import Room,Message,Topic
from .forms import RoomForm,ProfileForm
from django.contrib.auth.models import User
from django.http import JsonResponse
from .ai_helper import AIAssistant
from .models import PrivateConversation, PrivateMessage, User


def hello(request):
    return render(request,'hello.html',{'name':'dexter'})



#view for displaying a specific chat room and handling message posting within that room.
def room(request, pk):
    try:
        room = Room.objects.get(id=pk)
    except Room.DoesNotExist:
        return render(request, '404.html', status=404)
    
    room_messages = room.message_set.all().order_by('created')
    participants = room.participants.all()
    
    if request.method == "POST":
        body = request.POST.get('body', '').strip()
        
        if not body:
            messages.error(request, "Message cannot be empty")
            return redirect('room', pk=room.id)
        
        # === OPTION 1: AI responds to @mentions ===
        if '@fishoai' in body.lower():
            try:
                from .ai_helper import AIAssistant
                ai = AIAssistant()
                ai_response = ai.handle_mention(body, room, request.user)
                
                Message.objects.create(
                    user=ai.ai_user,
                    room=room,
                    body=ai_response
                )
                
                print(f"✅ AI Response created: {ai_response[:50]}...")
                
            except Exception as e:
                # Error handling...
                pass
        
        # === OPTION 2: AI sometimes responds to regular messages (20% chance) ===
        elif random.random() < 0.2:  # 20% chance
            try:
                from .ai_helper import AIAssistant
                ai = AIAssistant()
                
                # Don't respond to very short messages
                if len(body) > 5:
                    ai_response = ai.respond_to_message(body, room, request.user)
                    
                    Message.objects.create(
                        user=ai.ai_user,
                        room=room,
                        body=ai_response
                    )
                    
                    print(f"🤖 AI joined conversation: {ai_response[:50]}...")
            except:
                pass
        
        # Always create user's message
        Message.objects.create(
            user=request.user,
            room=room,
            body=body
        )
        
        if request.user not in participants:
            room.participants.add(request.user)
        
        return redirect('room', pk=room.id)
    
    context = {
        'room': room,
        'room_messages': room_messages,
        'participants': participants
    }
    return render(request, 'room.html', context)

def loginPage(request):
    page = 'login'
    if request.user.is_authenticated:
        return redirect('home')

    if request.method == 'POST':
        username = request.POST.get('username').lower()
        password = request.POST.get('password')

        # authenticate returns the user object if credentials are correct
        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            return redirect('home')
        else:
            messages.error(request, "Username or password does not exist.")

    context = {'page': page}
    return render(request, 'login_register.html', context)
def logoutUSER(request):
    logout(request)
    return redirect('home')

def registerUser(request):
    page = 'register'
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.username = user.username.lower()
            user.save()
            login(request, user)
            return redirect('home')
    else:
        form = UserCreationForm()

    return render(request, 'login_register.html', {'form': form, 'page': page})

def home(request):
    q = request.GET.get('q')if request.GET.get('q')!=None else ''#Reads the search value from the URL.
    rooms=Room.objects.filter(
        Q(topic__name__icontains=q) | 
        Q(name__icontains=q) |
        Q(description__icontains=q)
        )#Without Q, Django only allows AND. With Q, we can use OR conditions.
    topics =Topic.objects.all()#get all topics
    room_count=rooms.count()#count of filtered rooms
    room_messages=Message.objects.filter(Q(room__topic__name__icontains=q))#filter messages based on topic name
    context={'rooms':rooms,'topics':topics, 'room_count':room_count,'room_messages':room_messages}
    return render(request,'home.html',context)

def userProfile(request,pk):
    user=User.objects.get(id=pk)
    rooms=user.room_set.all()
    room_messages=user.message_set.all()
    topics=Topic.objects.all()
    context={
    'user':user,
     'rooms':rooms,
     'room_messages':room_messages,
     'topics':topics
     }
    return render(request,'profile.html',context)

@login_required
def edit_profile(request):
    # Get the current user's profile
    profile = request.user.userprofile
    
    if request.method == 'POST':
        form = ProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, 'Profile updated successfully!')
            return redirect('user-profile', pk=request.user.id)
    else:
        form = ProfileForm(instance=profile)
    
    return render(request, 'edit_profile.html', {'form': form})


@login_required(login_url='login')
def createRoom(request):
    form=RoomForm()
    if request.method == 'POST':
        form = RoomForm(request.POST)  # Bind form to POST data
        if form.is_valid():
            room= form.save(commit=False)
            room.host = request.user
            room.save()
            return redirect('home')
        
    context={'form':form}  
    return render(request,'room_form.html',context)


@login_required(login_url='login')
def updateRoom(request,pk):
    room= Room.objects.get(id=pk)
    form=RoomForm(instance=room)
    if request.user != room.host :
        return HttpResponse('you are not allowed here')
    if request.method =='POST':
        form=RoomForm(request.POST, instance=room)
        if form.is_valid():
            form.save()
            return redirect('home')
    
    context={'form':form}
    return render(request,'room_form.html',context)

@login_required(login_url='login')
def deleteRoom(request,pk):
    room=Room.objects.get(id=pk)
    if request.user != room.host :#if the user is not the host of the room(the owner)
        return HttpResponse('you are not allowed here')
    if request.method == 'POST':
        room.delete()
        return redirect('home')
    
    return render(request,'delete.html',{'obj':room})
@login_required(login_url='login')
def deleteMessage(request,pk):
    message=Message.objects.get(id=pk)
    if request.user != message.user :
        return HttpResponse('you are not allowed here')
    if request.method == 'POST':
        message.delete()
        return redirect('home')
    
    return render(request,'delete.html',{'obj':message})
@login_required
def notifications(request):
    """View all notifications"""
    notifications = request.user.notifications.all()
    unread_count = request.user.notifications.filter(is_read=False).count()
    
    return render(request, 'notifications.html', {
        'notifications': notifications,
        'unread_count': unread_count,
    })

@login_required
def mark_notification_read(request, notification_id):
    """Mark a notification as read"""
    notification = get_object_or_404(Notification, id=notification_id, recipient=request.user)
    notification.is_read = True
    notification.save()
    
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'success': True})
    
    return redirect('notifications')

@login_required
def mark_all_read(request):
    """Mark all notifications as read"""
    request.user.notifications.filter(is_read=False).update(is_read=True)
    
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'success': True})
    
    return redirect('notifications')

@login_required
def get_unread_count(request):
    """API endpoint for unread notification count"""
    count = request.user.notifications.filter(is_read=False).count()
    return JsonResponse({'count': count})

@login_required
def get_users(request):

    """API endpoint to get users for mention suggestions"""
    users = User.objects.all()
    data = []
    
    for user in users:
        data.append({
            'id': user.id,
            'username': user.username,
            'avatar': user.userprofile.avatar.url if user.userprofile.avatar else None,
        })
    
    return JsonResponse(data, safe=False)
    

@login_required
def users_directory(request):
    """List all users to start a chat"""
    users = User.objects.exclude(id=request.user.id).order_by('username')
    
    # Filter by search
    search = request.GET.get('search', '')
    if search:
        users = users.filter(
            Q(username__icontains=search) |
            Q(first_name__icontains=search) |
            Q(last_name__icontains=search)
        )
    
    context = {
        'users': users,
        'search': search,
        'page': 'users_directory'
    }
    return render(request, 'users_directory.html', context)

@login_required
def inbox(request):
    """View all conversations"""
    # Get all conversations where user is a participant
    conversations = PrivateConversation.objects.filter(
        participants=request.user
    ).prefetch_related('participants', 'messages')
    
    # Get unread count for each conversation
    conversations_with_unread = []
    for conv in conversations:
        unread_count = PrivateMessage.objects.filter(
            conversation=conv,
            receiver=request.user,
            is_read=False
        ).count()
        
        other_user = conv.get_other_user(request.user)
        last_message = conv.get_last_message()
        
        conversations_with_unread.append({
            'conversation': conv,
            'other_user': other_user,
            'last_message': last_message,
            'unread_count': unread_count,
            'updated_at': conv.updated_at
        })
    
    context = {
        'conversations': conversations_with_unread,
        'page': 'inbox'
    }
    return render(request, 'inbox.html', context)

@login_required
def private_chat(request, user_id):
    """Private chat with specific user"""
    other_user = get_object_or_404(User, id=user_id)
    
    # Get or create conversation
    conversation = PrivateConversation.objects.filter(
        participants=request.user
    ).filter(
        participants=other_user
    ).distinct().first()
    
    if not conversation:
        conversation = PrivateConversation.objects.create()
        conversation.participants.add(request.user, other_user)
    
    # Get messages for this conversation
    messages = PrivateMessage.objects.filter(
        conversation=conversation
    ).order_by('created_at')
    
    # Mark messages as read when viewing
    unread_messages = messages.filter(receiver=request.user, is_read=False)
    for message in unread_messages:
        message.mark_as_read()
    
    # Handle message sending
    if request.method == 'POST':
        content = request.POST.get('content', '').strip()
        if content:
            PrivateMessage.objects.create(
                conversation=conversation,
                sender=request.user,
                receiver=other_user,
                content=content
            )
            conversation.save()  # Update updated_at
            return redirect('private-chat', user_id=user_id)
    
    context = {
        'other_user': other_user,
        'conversation': conversation,
        'messages': messages,
        'page': 'private_chat'
    }
    return render(request, 'private_chat.html', context)

@login_required
def start_chat(request, user_id):
    """Start a new chat with a user"""
    other_user = get_object_or_404(User, id=user_id)
    
    # Get or create conversation
    conversation = PrivateConversation.objects.filter(
        participants=request.user
    ).filter(
        participants=other_user
    ).distinct().first()
    
    if not conversation:
        conversation = PrivateConversation.objects.create()
        conversation.participants.add(request.user, other_user)
    
    return redirect('private-chat', user_id=user_id)

@login_required
def delete_conversation(request, conversation_id):
    """Delete a conversation"""
    conversation = get_object_or_404(PrivateConversation, id=conversation_id, participants=request.user)
    
    if request.method == 'POST':
        conversation.delete()
        return redirect('inbox')
    
    return render(request, 'delete_conversation.html', {'conversation': conversation})

def health_check(request):
    from django.http import HttpResponse
    return HttpResponse('OK', status=200)

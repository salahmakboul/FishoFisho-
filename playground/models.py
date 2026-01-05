from django.db import models
import uuid
from django.contrib.auth.models import User
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
    participants=models.ManyToManyField(User,
     related_name="participants" ,
     blank=True)
    update=models.DateTimeField(auto_now=True)
    created=models.DateTimeField(auto_now_add=True)
    class Meta:#metadata
        ordering=['-update','-created']#order by update and created time descending

    def __str__(self):#string representation
        return self.name#return the room name    
    
# MOVE NOTIFICATION MODEL BEFORE MESSAGE MODEL

class Notification(models.Model):
    NOTIFICATION_TYPES = (
        ('mention', 'Mention'),
        ('message', 'Message'),
        ('follow', 'Follow'),
        ('like', 'Like'),
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

    class Meta:
        ordering=['-update','-created']
    
    def __str__(self):#string representation
        return self.body[:50]#return first 50 characters of the message body

    def save(self, *args, **kwargs):
        # First save the message to get an ID
        super().save(*args, **kwargs)
        
        # Then check for mentions
        self.check_mentions()
    
    def check_mentions(self):
        """Extract @mentions from message body and create notifications"""
        # Find all @username patterns
        mentions = re.findall(r'@(\w+)', self.body)
        
        # Clear existing mentions
        self.mentions.clear()
        
        for username in mentions:
            try:
                user = User.objects.get(username=username)
                if user != self.user:  # Don't notify self-mentions
                    # Add to mentions
                    self.mentions.add(user)
                    
                    # Create notification
                    Notification.objects.create(
                        recipient=user,
                        sender=self.user,
                        notification_type='mention',
                        message=f"{self.user.username} mentioned you in a message",
                        room=self.room,
                        is_read=False
                    )
            except User.DoesNotExist:
                pass

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    bio = models.TextField(max_length=500, blank=True)
    
    def __str__(self):
        return f"{self.user.username}'s Profile"

# models.py - Add Chatbot model
class Chatbot(models.Model):
    name = models.CharField(max_length=100, default="FishoAI")
    avatar = models.ImageField(upload_to='chatbots/', blank=True, null=True)
    is_active = models.BooleanField(default=True)
    system_prompt = models.TextField(default="You are FishoAI, a helpful assistant for the FishoFisho community. Be friendly, concise, and helpful.")
    
    def __str__(self):
        return self.name

# OR simpler: Just use a constant
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
        """Get the other user in the conversation"""
        return self.participants.exclude(id=current_user.id).first()
    
    def get_last_message(self):
        """Get the last message in this conversation"""
        return self.messages.last()

class PrivateMessage(models.Model):
    """Private message between users"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(PrivateConversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_private_messages')
    receiver = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_private_messages')
    content = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['created_at']
    
    def __str__(self):
        return f"{self.sender.username} → {self.receiver.username}: {self.content[:50]}"
    
    def mark_as_read(self):
        """Mark message as read"""
        if not self.is_read:
            self.is_read = True
            self.save()

# ai_helper.py - UPDATED VERSION WITH REGULAR MESSAGE RESPONSES
import random
from django.contrib.auth.models import User
from django.conf import settings

class AIAssistant:
    def __init__(self):
        """Initialize the AI Assistant with fallback to mock responses"""
        print("🤖 Initializing FishoAI Assistant...")
        
        # Get or create AI user
        self.ai_user, created = User.objects.get_or_create(
            username="FishoAI",
            defaults={
                'email': 'ai@fishofisho.com',
                'first_name': 'Fisho',
                'last_name': 'AI',
                'is_active': True,
                'is_staff': False,
                'is_superuser': False
            }
        )
        
        if created:
            print(f"✅ Created new AI user: {self.ai_user.username}")
        else:
            print(f"✅ Using existing AI user: {self.ai_user.username}")
        
        # Try to use real Gemini AI if configured
        self.use_real_ai = False
        try:
            api_key = getattr(settings, 'GEMINI_API_KEY', None)
            if api_key and len(api_key) > 30:  # Basic validation
                import google.generativeai as genai
                genai.configure(api_key=api_key)
                self.model = genai.GenerativeModel('gemini-pro')
                self.use_real_ai = True
                print("✅ Using Google Gemini AI (Real)")
            else:
                print("⚠️ No valid API key found. Using mock AI.")
        except Exception as e:
            print(f"⚠️ Could not initialize Gemini: {e}. Using mock AI.")
    
    def _get_mock_response(self, question, user=None, room=None, is_mention=True):
        """Generate friendly mock responses"""
        greetings = [
            f"Hello {user.username if user else 'there'}! 👋 How can I help you today?",
            f"Hi {user.username if user else 'friend'}! Nice to see you in {room.name if room else 'the chat'}!",
            f"Hey {user.username if user else 'there'}! 😊 What's on your mind?",
        ]
        
        responses = [
            f"That's an interesting question about '{question[:30]}'!",
            f"I'm FishoAI, your helpful assistant for {room.name if room else 'this chat'}!",
            "Thanks for sharing! I'm here to help with anything you need.",
            f"Great point, {user.username if user else 'friend'}! Let me think about that...",
            "I'm excited to chat with you! What else would you like to know?",
            f"In {room.name if room else 'this room'}, we love discussing interesting topics like this!",
            f"That's a thoughtful message, {user.username if user else 'friend'}!",
            "Thanks for including me in the conversation!",
            f"I appreciate you sharing that with me, {user.username if user else 'everyone'}!",
        ]
        
        # Special conversation starters
        conversation_starters = [
            f"So {user.username if user else 'there'}, what's on your mind today?",
            f"Tell me more about that, {user.username if user else 'friend'}!",
            f"That's interesting! What else would you like to discuss?",
            f"I'd love to hear more about your thoughts on this!",
        ]
        
        if not question or len(question.strip()) < 2:
            return random.choice(greetings)
        
        # Special responses for common questions
        question_lower = question.lower()
        
        if 'hello' in question_lower or 'hi' in question_lower or 'hey' in question_lower:
            return random.choice(greetings)
        
        if 'how are you' in question_lower:
            return "I'm doing great, thanks for asking! Ready to help you with anything."
        
        if 'name' in question_lower:
            return f"I'm FishoAI, {user.username if user else 'your friendly'} AI assistant!"
        
        if 'help' in question_lower:
            return f"I can help you with questions, chat, or just keep you company, {user.username if user else 'friend'}!"
        
        if 'weather' in question_lower:
            return "I'm not connected to weather services, but I hope it's nice where you are! ☀️"
        
        if '?' in question:  # If it's a question
            return f"Good question! Let me think about '{question[:40]}'..."
        
        # For regular messages (not questions), respond conversationally
        if not is_mention and '?' not in question:
            return random.choice(conversation_starters)
        
        return random.choice(responses)
    
    def _get_real_ai_response(self, question, context="", room=None, is_mention=True):
        """Get response from real Gemini AI"""
        try:
            if is_mention:
                prompt = f"""You are FishoAI, a friendly AI assistant in a chat room.
                Context: {context}
                Chat room: {room.name if room else 'General Chat'}
                
                User asks: {question}
                
                Respond in 1-2 friendly sentences."""
            else:
                prompt = f"""You are FishoAI, a friendly AI assistant participating in a chat conversation.
                Context: {context}
                Chat room: {room.name if room else 'General Chat'}
                
                User said: {question}
                
                Respond naturally as if you're part of the conversation. Keep it brief and engaging."""
            
            response = self.model.generate_content(
                prompt,
                generation_config={
                    'max_output_tokens': 100,
                    'temperature': 0.7
                }
            )
            
            if response and hasattr(response, 'text'):
                return response.text.strip()
            else:
                return self._get_mock_response(question, is_mention=is_mention)
                
        except Exception as e:
            print(f"❌ Gemini API error: {e}")
            return self._get_mock_response(question, is_mention=is_mention)
    
    def generate_response(self, message, context="", room=None, user=None, is_mention=True):
        """Generate AI response (real or mock)"""
        if self.use_real_ai:
            return self._get_real_ai_response(message, context, room, is_mention)
        else:
            return self._get_mock_response(message, user, room, is_mention)
    
    def handle_mention(self, message_text, room, mentioned_by):
        """Handle @FishoAI mentions"""
        print(f"🔍 Handling mention from @{mentioned_by.username}: {message_text[:50]}...")
        
        # Extract the question (remove @fishoai mentions)
        question = message_text
        for mention in ['@FishoAI', '@fishoai']:
            question = question.replace(mention, '')
        question = question.strip()
        
        # Generate response
        response = self.generate_response(
            message=question,
            context=f"User @{mentioned_by.username} mentioned you",
            room=room,
            user=mentioned_by,
            is_mention=True
        )
        
        print(f"✅ AI Response: {response[:50]}...")
        return response
    
    def respond_to_message(self, message_text, room, sender):
        """Respond to regular messages (not @mentions)"""
        print(f"🔍 Responding to message from @{sender.username}: {message_text[:50]}...")
        
        # Determine if this is likely a question
        is_question = '?' in message_text
        
        # Generate response
        response = self.generate_response(
            message=message_text,
            context=f"User @{sender.username} sent a message",
            room=room,
            user=sender,
            is_mention=False
        )
        
        print(f"✅ AI Conversation Response: {response[:50]}...")
        return response
# ai_helper.py - UPDATED FOR GOOGLE GEMINI API
import os
import random
from pathlib import Path
from django.contrib.auth.models import User
from django.conf import settings
from dotenv import load_dotenv
import logging

logger = logging.getLogger(__name__)

# Load .env file from project root
env_path = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(env_path)

class AIAssistant:
    def __init__(self):
        """Initialize the AI Assistant for Google Gemini"""
        print("=" * 50)
        print("🤖 Initializing FishoAI Assistant (Gemini)...")
        print("=" * 50)
        
        # Debug info
        print(f"📁 .env path: {env_path}")
        print(f"✅ .env exists: {env_path.exists()}")
        
        # Get API key from environment
        api_key = os.environ.get('OPENAI_API_KEY') or os.environ.get('GEMINI_API_KEY')
        print(f"🔑 API Key loaded: {'YES' if api_key else 'NO'}")
        if api_key:
            print(f"🔑 Key starts with: {api_key[:15]}...")
            print(f"🔑 Key length: {len(api_key)} characters")
        
        # Get or create AI user
        try:
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
                print(f"👤 Created new AI user: {self.ai_user.username}")
            else:
                print(f"👤 Using existing AI user: {self.ai_user.username}")
        except Exception as e:
            print(f"⚠️ Could not create AI user: {e}")
            self.ai_user = None
        
        # Initialize Google Gemini AI
        self.use_real_ai = False
        self.model = None
        
        if api_key and len(api_key.strip()) > 20:
            try:
                import google.generativeai as genai
                
                # Configure Gemini
                genai.configure(api_key=api_key)
                
                # List available models
                # for model in genai.list_models():
                #     if 'generateContent' in model.supported_generation_methods:
                #         print(f"📋 Available: {model.name}")
                
                # Use Gemini Pro
                self.model = genai.GenerativeModel('gemini-pro')
                self.use_real_ai = True
                
                # Test the model
                print("🧪 Testing Gemini connection...")
                test_response = self.model.generate_content("Say 'Hello' in 2 words")
                if test_response and test_response.text:
                    print(f"✅ Gemini connected! Test: {test_response.text}")
                else:
                    print("⚠️ Gemini test failed")
                    self.use_real_ai = False
                    
            except ImportError:
                print("❌ google-generativeai not installed!")
                print("💡 Run: pip install google-generativeai")
            except Exception as e:
                print(f"❌ Gemini initialization error: {e}")
                print("💡 Your API key might be invalid or have no quota")
        else:
            print("⚠️ No valid API key found in .env")
            print("💡 Add: OPENAI_API_KEY=your_google_gemini_api_key")
        
        if not self.use_real_ai:
            print("ℹ️ Using mock AI responses")
            print("💡 Get a free Gemini API key from: https://makersuite.google.com/app/apikey")
        
        print("=" * 50)
    
    def _get_mock_response(self, question, user=None, room=None, is_mention=True):
        """Generate friendly mock responses when Gemini is unavailable"""
        
        username = user.username if user and hasattr(user, 'username') else 'friend'
        room_name = room.name if room and hasattr(room, 'name') else 'this chat'
        
        # Greetings
        if not question or len(question.strip()) < 2:
            greetings = [
                f"Hello {username}! 👋 I'm FishoAI!",
                f"Hi {username}! Ready to chat in {room_name}!",
                f"Hey {username}! 😊 How can I help?",
                f"Greetings {username}! I'm your AI assistant.",
            ]
            return random.choice(greetings)
        
        question_lower = question.lower()
        
        # Common questions
        if any(word in question_lower for word in ['hello', 'hi', 'hey', 'greetings']):
            return f"Hello {username}! Nice to meet you! 😊"
        
        if 'how are you' in question_lower:
            return f"I'm doing great, thanks for asking {username}! How about you?"
        
        if 'your name' in question_lower or 'who are you' in question_lower:
            return f"I'm FishoAI, your friendly AI assistant! Pleased to meet you, {username}!"
        
        if 'help' in question_lower:
            return f"I can answer questions, chat with you, or help with {room_name if room else 'anything'}!"
        
        if 'weather' in question_lower:
            return f"I can't check weather, but I hope it's beautiful where you are, {username}! 🌤️"
        
        if '?' in question_lower:
            responses = [
                f"Interesting question about '{question[:30]}'!",
                f"Great question, {username}! Let me think...",
                f"I love curious minds like yours, {username}!",
                f"That's a thoughtful question!",
            ]
            return random.choice(responses)
        
        # Regular messages
        responses = [
            f"Thanks for sharing that, {username}!",
            f"I appreciate your message, {username}!",
            f"Got it, {username}! Thanks for keeping me in the loop.",
            f"Noted! What else would you like to discuss, {username}?",
            f"Interesting point, {username}! Tell me more.",
        ]
        return random.choice(responses)
    
    def _get_gemini_response(self, message, context="", room=None, is_mention=True):
        """Get response from Google Gemini AI"""
        try:
            if not self.model:
                return self._get_mock_response(message, is_mention=is_mention)
            
            username = context.split('@')[-1].split()[0] if '@' in context else "User"
            room_name = room.name if room and hasattr(room, 'name') else 'General Chat'
            
            # Create a prompt for Gemini
            if is_mention:
                prompt = f"""You are FishoAI, a friendly and enthusiastic AI assistant in a chat application.
                
                Context: {context}
                Chat Room: "{room_name}"
                User @{username} mentioned you and said: "{message}"
                
                Respond in 1-2 friendly, conversational sentences. Be helpful and engaging."""
            else:
                prompt = f"""You are FishoAI participating in a chat conversation.
                
                Context: {context}
                Chat Room: "{room_name}"
                User @{username} said: "{message}"
                
                Respond naturally as if you're chatting with friends. Keep it to 1 sentence."""
            
            # Generate response
            response = self.model.generate_content(
                prompt,
                generation_config={
                    'max_output_tokens': 150,
                    'temperature': 0.8,
                    'top_p': 0.9,
                }
            )
            
            if response and hasattr(response, 'text'):
                return response.text.strip()
            else:
                return self._get_mock_response(message, is_mention=is_mention)
                
        except Exception as e:
            print(f"❌ Gemini API error: {e}")
            logger.error(f"Gemini error: {e}")
            return self._get_mock_response(message, is_mention=is_mention)
    
    def generate_response(self, message, context="", room=None, user=None, is_mention=True):
        """Generate AI response (Gemini or mock)"""
        if not message or not isinstance(message, str):
            return "Hello! 👋 How can I help you today?"
        
        message = message.strip()
        if not message:
            return f"Hi {user.username if user else 'there'}! I'm FishoAI! 😊"
        
        try:
            if self.use_real_ai and self.model:
                return self._get_gemini_response(message, context, room, is_mention)
            else:
                return self._get_mock_response(message, user, room, is_mention)
                
        except Exception as e:
            print(f"❌ Error generating response: {e}")
            return "Thanks for your message! I'm here to help."
    
    def handle_mention(self, message_text, room, mentioned_by):
        """Handle @FishoAI mentions"""
        print(f"\n🔔 Mention from @{mentioned_by.username if mentioned_by else 'unknown'}")
        print(f"   Message: {message_text[:60]}...")
        
        # Clean the message
        question = message_text
        for mention in ['@FishoAI', '@fishoai', '@Fishoai', '@FISHOAI']:
            question = question.replace(mention, '')
        question = question.strip()
        
        context = f"User @{mentioned_by.username if mentioned_by else 'someone'} mentioned FishoAI"
        response = self.generate_response(
            message=question,
            context=context,
            room=room,
            user=mentioned_by,
            is_mention=True
        )
        
        print(f"   🤖 Response: {response[:60]}...")
        return response
    
    def respond_to_message(self, message_text, room, sender):
        """Respond to regular messages (30% chance)"""
        # Only respond sometimes to avoid spamming
        if random.random() < 0.3:  # 30% chance
            print(f"\n💬 Responding to @{sender.username if sender else 'unknown'}")
            print(f"   Message: {message_text[:60]}...")
            
            context = f"User @{sender.username if sender else 'someone'} sent a chat message"
            response = self.generate_response(
                message=message_text,
                context=context,
                room=room,
                user=sender,
                is_mention=False
            )
            
            print(f"   🤖 Response: {response[:60]}...")
            return response
        
        return None  # Don't respond this time

# Quick test function
def test_ai_assistant():
    """Test the AI assistant"""
    print("\n" + "="*50)
    print("🧪 TESTING AI ASSISTANT")
    print("="*50)
    
    # Mock user for testing
    class MockUser:
        username = "TestUser"
    
    class MockRoom:
        name = "Test Room"
    
    # Create assistant
    assistant = AIAssistant()
    
    # Test cases
    test_cases = [
        ("Hello!", True),
        ("How are you?", True),
        ("What's your name?", True),
        ("Tell me about this chat", True),
        ("The weather is nice today", False),
    ]
    
    for message, is_mention in test_cases:
        print(f"\n📤 Input: {message}")
        print(f"   Type: {'@Mention' if is_mention else 'Regular message'}")
        
        if is_mention:
            response = assistant.handle_mention(
                message_text=message,
                room=MockRoom(),
                mentioned_by=MockUser()
            )
        else:
            response = assistant.respond_to_message(
                message_text=message,
                room=MockRoom(),
                sender=MockUser()
            )
        
        if response:
            print(f"📥 Response: {response}")
    
    print("\n" + "="*50)
    print("✅ TEST COMPLETE")
    print("="*50)

if __name__ == "__main__":
    # Run test if file is executed directly
    test_ai_assistant()
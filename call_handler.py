import os
import httpx
from dotenv import load_dotenv
import json
import uuid
from typing import Optional, Dict, Any

load_dotenv()

class CallHandler:
    def __init__(self):
        """Initialize the CallHandler with API keys for Vapi/Twilio"""
        self.vapi_api_key = os.getenv("VAPI_API_KEY")
        self.twilio_account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        self.twilio_auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        self.voice_service_url = os.getenv("VOICE_SERVICE_URL", "http://localhost:8000/call_webhook")
        self.use_service = os.getenv("VOICE_SERVICE", "vapi").lower()  # "vapi" or "twilio"
        
        # Phone number to use for outbound calls
        self.from_phone_number = os.getenv("FROM_PHONE_NUMBER")
        
        # Flag for simulation mode (set to False to use real services)
        self.simulation_mode = os.getenv("SIMULATION_MODE", "True").lower() == "true"
    
    async def make_outbound_call(self, phone_number: str, message: str) -> Dict[str, Any]:
        """Make an outbound call using the configured service (Vapi or Twilio)"""
        call_id = str(uuid.uuid4())
        
        if self.simulation_mode:
            print(f"SIMULATED CALL to {phone_number}")
            print(f"Message: {message}")
            return {
                "status": "simulated_call_initiated",
                "call_id": call_id
            }
        
        # Use the appropriate service
        if self.use_service == "vapi":
            return await self._make_vapi_call(phone_number, message, call_id)
        else:
            return await self._make_twilio_call(phone_number, message, call_id)
    
    async def _make_vapi_call(self, phone_number: str, message: str, call_id: str) -> Dict[str, Any]:
        """Make an outbound call using Vapi"""
        url = "https://api.vapi.ai/call/phone"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.vapi_api_key}"
        }
        
        payload = {
            "phoneNumber": phone_number,
            "callerId": self.from_phone_number,
            "firstMessage": message,
            "webhookUrl": f"{self.voice_service_url}?call_id={call_id}",
            "metadata": {
                "call_id": call_id
            }
        }
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                response_data = response.json()
                return {
                    "status": "call_initiated",
                    "call_id": call_id,
                    "service_call_id": response_data.get("id")
                }
            except httpx.HTTPError as e:
                return {
                    "status": "failed",
                    "call_id": call_id,
                    "error": str(e)
                }
    
    async def _make_twilio_call(self, phone_number: str, message: str, call_id: str) -> Dict[str, Any]:
        """Make an outbound call using Twilio"""
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.twilio_account_sid}/Calls.json"
        
        # TwiML to instruct Twilio to say the message
        twiml = f"""
        <Response>
            <Say>{message}</Say>
            <Redirect method="POST">{self.voice_service_url}?call_id={call_id}</Redirect>
        </Response>
        """
        
        data = {
            "To": phone_number,
            "From": self.from_phone_number,
            "Twiml": twiml,
            "StatusCallback": f"{self.voice_service_url}?call_id={call_id}&event=status"
        }
        
        auth = (self.twilio_account_sid, self.twilio_auth_token)
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, data=data, auth=auth)
                response.raise_for_status()
                response_data = response.json()
                return {
                    "status": "call_initiated",
                    "call_id": call_id,
                    "service_call_id": response_data.get("sid")
                }
            except httpx.HTTPError as e:
                return {
                    "status": "failed",
                    "call_id": call_id,
                    "error": str(e)
                }
    
    async def handle_inbound_call(self, call_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle an inbound call event"""
        if self.simulation_mode:
            print(f"SIMULATED INBOUND CALL from {call_data.get('from')}")
            return {
                "action": "talk",
                "text": "Thanks for calling. This is a simulated response."
            }
        
        # Extract call information
        call_id = call_data.get("call_id", str(uuid.uuid4()))
        event_type = call_data.get("event")
        
        if event_type == "call.started":
            # Initial greeting for new calls
            return {
                "action": "talk",
                "text": "Thank you for calling. How can I help you today?"
            }
        
        elif event_type == "transcription":
            # Handle speech input from the caller
            transcript = call_data.get("transcript", "")
            
            # Simple intent detection (would be more sophisticated in production)
            intent = self._detect_intent(transcript)
            
            # Generate appropriate response based on intent
            response = self._generate_response(intent, transcript)
            
            return {
                "action": "talk",
                "text": response,
                "intent": intent
            }
        
        elif event_type in ["call.ended", "call.completed"]:
            # Call has ended, no response needed
            return {"status": "call_ended"}
        
        # Default response for other events
        return {
            "action": "talk",
            "text": "I'm listening. How can I assist you?"
        }
    
    def _detect_intent(self, transcript: str) -> str:
        """Detect the intent from a customer's transcript"""
        transcript_lower = transcript.lower()
        
        # Check for various intents
        if any(word in transcript_lower for word in ["help", "support", "problem", "issue"]):
            return "support"
        elif any(word in transcript_lower for word in ["schedule", "appointment", "book", "meeting"]):
            return "schedule"
        elif any(word in transcript_lower for word in ["thank", "thanks", "appreciate"]):
            return "gratitude"
        elif any(word in transcript_lower for word in ["speak", "human", "agent", "representative"]):
            return "agent_request"
        elif any(word in transcript_lower for word in ["cancel", "stop", "end"]):
            return "terminate"
        else:
            return "general"
    
    def _generate_response(self, intent: str, transcript: str) -> str:
        """Generate an appropriate response based on the detected intent"""
        responses = {
            "support": "I understand you're having an issue. Could you please describe the problem in more detail so I can assist you better?",
            "schedule": "I'd be happy to help you schedule an appointment. What day and time works best for you?",
            "gratitude": "You're welcome! Is there anything else I can help with today?",
            "agent_request": "I'll connect you with a customer support agent right away. Please hold while I transfer your call.",
            "terminate": "Thank you for calling. Have a great day!",
            "general": "How can I assist you further today?"
        }
        
        return responses.get(intent, "I'm here to help. What can I do for you?")
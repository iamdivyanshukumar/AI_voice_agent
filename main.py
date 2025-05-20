from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from datetime import datetime
import logging
import os
from voice_services import VoiceService
from call_handler import CallHandler
from pydantic import BaseModel
from typing import Dict

app = FastAPI()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize services
voice_service = VoiceService()
call_handler = CallHandler()

# In-memory storage
call_logs: Dict[str, dict] = {}
current_call_id = 0

class CallRequest(BaseModel):
    phone_number: str
    question: str = "Hello, how can I help you today?"

@app.post("/initiate_call")
async def initiate_call(call_request: CallRequest):
    """Endpoint to initiate outbound calls"""
    global current_call_id
    
    current_call_id += 1
    call_id = f"call_{current_call_id}"
    
    call_logs[call_id] = {
        "phone_number": call_request.phone_number,
        "start_time": datetime.now().isoformat(),
        "status": "initiated",
        "transcript": "",
        "intent": None
    }
    
    try:
        # Generate and consume the audio generator
        audio_generator = voice_service.text_to_speech(call_request.question)
        audio_bytes = b"".join(audio_generator)  # Combine all chunks
        
        # Store the audio as base64 for JSON response
        import base64
        audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
        
        # Simulate call initiation
        response = call_handler.make_outbound_call(
            phone_number=call_request.phone_number,
            message=call_request.question
        )
        
        call_logs[call_id]["status"] = "in-progress"
        call_logs[call_id]["transcript"] = f"AI: {call_request.question}"
        
        return JSONResponse({
            "status": "call_initiated",
            "call_id": call_id,
            "audio_base64": audio_base64,  # Now properly serialized
            "message": f"Call to {call_request.phone_number} is being processed"
        })
    except Exception as e:
        call_logs[call_id]["status"] = "failed"
        logger.error(f"Call initiation failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
@app.post("/call_webhook")
async def handle_call_webhook(request: Request):
    """Webhook to handle call events"""
    try:
        data = await request.json()
        call_id = data.get("call_id", f"call_{len(call_logs)+1}")
        
        # Initialize call log if new call
        if call_id not in call_logs:
            call_logs[call_id] = {
                "phone_number": data.get("from", "unknown"),
                "start_time": datetime.now().isoformat(),
                "status": "in-progress",
                "transcript": "",
                "intent": None
            }
        
        event_type = data.get("event")
        
        if event_type == "speech":
            audio_data = data.get("audio")  # Base64 encoded audio in real implementation
            transcript = data.get("transcript", "")
            
            if not transcript and audio_data:
                # If no transcript provided, use STT to convert audio
                transcript = await voice_service.speech_to_text(audio_data)
            
            call_logs[call_id]["transcript"] += f"\nCustomer: {transcript}"
            
            # Simple intent detection
            intent = "general"
            transcript_lower = transcript.lower()
            if any(word in transcript_lower for word in ["help", "support", "problem"]):
                intent = "support"
            elif "schedule" in transcript_lower:
                intent = "schedule"
            elif any(word in transcript_lower for word in ["thank", "thanks"]):
                intent = "gratitude"
                
            call_logs[call_id]["intent"] = intent
            
            # Generate response based on intent
            if intent == "support":
                response_text = "I'll connect you to a support agent."
            elif intent == "schedule":
                response_text = "What time would you like to schedule?"
            elif intent == "gratitude":
                response_text = "You're welcome! Is there anything else I can help with?"
            else:
                response_text = "How can I assist you further?"
            
            # Generate TTS response
            response_audio = voice_service.text_to_speech(response_text)
            call_logs[call_id]["transcript"] += f"\nAI: {response_text}"
            
            return JSONResponse({
                "action": "talk",
                "text": response_text,
                "audio": response_audio
            })
        
        elif event_type == "call.ended":
            call_logs[call_id]["status"] = "completed"
            call_logs[call_id]["end_time"] = datetime.now().isoformat()
            return JSONResponse({"status": "call_ended"})
        
        return JSONResponse({"status": "event_processed"})
    
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/call_logs")
async def get_call_logs(limit: int = 10):
    """Get recent call logs"""
    recent_calls = dict(sorted(call_logs.items(), 
                          key=lambda x: x[1]["start_time"], 
                          reverse=True)[:limit])
    return {
        "total_calls": len(call_logs),
        "active_calls": sum(1 for call in call_logs.values() 
                          if call["status"] == "in-progress"),
        "calls": recent_calls
    }

@app.get("/")
async def health_check():
    return {
        "status": "running", 
        "version": "1.0",
        "services": {
            "tts": "ElevenLabs",
            "stt": "Deepgram",
            "call_handler": "Simulated"
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
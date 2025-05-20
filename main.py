from fastapi import FastAPI, Request, HTTPException, Depends, BackgroundTasks
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from datetime import datetime
import logging
import os
import uuid
import json
import base64
from typing import Dict, List, Optional, Any

# Import our modules
from voice_services import VoiceService
from call_handler import CallHandler
from database import CallRecord, get_db, init_db
from pydantic import BaseModel, Field

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("voice_agent.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="AI Voice Agent",
    description="A proof-of-concept voice assistant system with inbound and outbound calling capabilities.",
    version="1.0.0"
)

# Initialize services
voice_service = VoiceService()
call_handler = CallHandler()

# Initialize database
@app.on_event("startup")
def startup_event():
    """Initialize the database on startup"""
    init_db()
    logger.info("Database initialized")

# Pydantic models for request validation
class CallRequest(BaseModel):
    phone_number: str
    message: str = "Hello, this is an automated call. How can I help you today?"
    callback_url: Optional[str] = None

class CallWebhookData(BaseModel):
    call_id: Optional[str] = None
    event: Optional[str] = None
    from_number: Optional[str] = Field(None, alias="from")
    to_number: Optional[str] = Field(None, alias="to")
    transcript: Optional[str] = None
    audio: Optional[str] = None  # Base64 encoded audio
    recording_url: Optional[str] = None
    status: Optional[str] = None
    timestamp: Optional[str] = None
    
    class Config:
        allow_population_by_field_name = True

@app.post("/initiate_call")
async def initiate_call(call_request: CallRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Endpoint to initiate outbound calls"""
    call_id = str(uuid.uuid4())
    
    # Create call record in database
    db_call = CallRecord(
        call_id=call_id,
        phone_number=call_request.phone_number,
        direction="outbound",
        start_time=datetime.now(),
        status="initiated",
        transcript=f"AI: {call_request.message}"
    )
    db.add(db_call)
    db.commit()
    
    try:
        # Generate audio for TTS
        audio_base64 = voice_service.get_audio_as_base64(call_request.message)
        
        # Initiate the call (non-blocking)
        background_tasks.add_task(
            handle_outbound_call,
            call_id=call_id,
            phone_number=call_request.phone_number,
            message=call_request.message
        )
        
        return JSONResponse({
            "status": "call_initiated",
            "call_id": call_id,
            "audio_base64": audio_base64,
            "message": f"Call to {call_request.phone_number} is being processed"
        })
    except Exception as e:
        # Update call status to failed
        db_call.status = "failed"
        db.commit()
        
        logger.error(f"Call initiation failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

async def handle_outbound_call(call_id: str, phone_number: str, message: str):
    """Background task to handle outbound call"""
    try:
        # Make the actual call
        response = await call_handler.make_outbound_call(phone_number, message)
        
        # Update call status in database
        db = next(get_db())
        call_record = db.query(CallRecord).filter(CallRecord.call_id == call_id).first()
        if call_record:
            call_record.status = "in-progress" if response.get("status") == "call_initiated" else "failed"
            db.commit()
    except Exception as e:
        logger.error(f"Error in outbound call handling for call {call_id}: {str(e)}")

@app.post("/call_webhook")
async def handle_call_webhook(request: Request, db: Session = Depends(get_db)):
    """Webhook to handle call events from Vapi/Twilio"""
    try:
        # Parse the incoming webhook data
        data = await request.json()
        logger.info(f"Webhook received: {json.dumps(data)}")
        
        # Extract call_id from query params or body
        call_id = request.query_params.get("call_id")
        if not call_id and "call_id" in data:
            call_id = data["call_id"]
        
        # Handle different webhook sources (Vapi vs Twilio)
        if "CallSid" in data:
            # This is a Twilio webhook
            return await handle_twilio_webhook(data, call_id, db)
        else:
            # Assume Vapi or custom format
            return await handle_vapi_webhook(data, call_id, db)
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

async def handle_vapi_webhook(data: Dict[str, Any], call_id: str, db: Session) -> JSONResponse:
    """Handle Vapi webhook events"""
    # Extract event type
    event_type = data.get("event")
    
    # Find or create call record
    call_record = db.query(CallRecord).filter(CallRecord.call_id == call_id).first()
    if not call_record:
        # New inbound call
        call_record = CallRecord(
            call_id=call_id,
            phone_number=data.get("from", "unknown"),
            direction="inbound",
            start_time=datetime.now(),
            status="in-progress",
            transcript=""
        )
        db.add(call_record)
    
    # Process based on event type
    if event_type == "call.started":
        call_record.status = "in-progress"
        db.commit()
        
        # Handle inbound call start
        response = await call_handler.handle_inbound_call(data)
        
        # Update transcript with AI response
        if "text" in response:
            call_record.transcript += f"\nAI: {response['text']}"
            db.commit()
        
        return JSONResponse(response)
    
    elif event_type == "transcription":
        # Handle customer speech
        transcript = data.get("transcript", "")
        
        # Update transcript
        call_record.transcript += f"\nCustomer: {transcript}"
        
        # Process with call handler to get a response
        response = await call_handler.handle_inbound_call(data)
        
        # Extract and save intent if available
        if "intent" in response:
            call_record.intent = response["intent"]
        
        # Update transcript with AI response
        if "text" in response:
            call_record.transcript += f"\nAI: {response['text']}"
        
        db.commit()
        return JSONResponse(response)
    
    elif event_type in ["call.ended", "call.completed"]:
        # Call has ended
        call_record.status = "completed"
        call_record.end_time = datetime.now()
        db.commit()
        return JSONResponse({"status": "call_ended"})
    
    # Default response
    db.commit()
    return JSONResponse({"status": "event_processed"})

async def handle_twilio_webhook(data: Dict[str, Any], call_id: str, db: Session) -> JSONResponse:
    """Handle Twilio webhook events"""
    # Extract call details
    call_sid = data.get("CallSid")
    call_status = data.get("CallStatus")
    
    # Map Twilio status to our status
    status_mapping = {
        "queued": "initiated",
        "ringing": "initiated",
        "in-progress": "in-progress",
        "completed": "completed",
        "busy": "failed",
        "no-answer": "failed",
        "canceled": "failed",
        "failed": "failed"
    }
    
    # Find or create call record
    call_record = db.query(CallRecord).filter(CallRecord.call_id == call_id).first()
    if not call_record:
        # New call record
        direction = "inbound" if data.get("Direction") == "inbound" else "outbound"
        call_record = CallRecord(
            call_id=call_id,
            phone_number=data.get("From", "unknown"),
            direction=direction,
            start_time=datetime.now(),
            status=status_mapping.get(call_status.lower(), "in-progress"),
            transcript=""
        )
        db.add(call_record)
    else:
        # Update existing record
        if call_status:
            call_record.status = status_mapping.get(call_status.lower(), call_record.status)
    
    # Check if this is a recording callback
    if "RecordingUrl" in data:
        # Process recording (would download and transcribe in production)
        recording_url = data.get("RecordingUrl")
        # In production: download recording and use STT service
        
        # For demo purposes:
        call_record.transcript += "\nCustomer: [Recording received but not transcribed in demo]"
        
    # Check if call is completed
    if call_status and call_status.lower() in ["completed", "busy", "no-answer", "canceled", "failed"]:
        call_record.end_time = datetime.now()
    
    db.commit()
    
    # For voice responses, return TwiML
    if "CallStatus" in data and data.get("CallStatus") == "in-progress":
        return JSONResponse({
            "content": """
            <?xml version="1.0" encoding="UTF-8"?>
            <Response>
                <Say>Thank you for calling. How can I help you today?</Say>
                <Record maxLength="30" action="/call_webhook?call_id={call_id}&action=recording" />
            </Response>
            """.format(call_id=call_id),
            "media_type": "application/xml"
        })
    
    return JSONResponse({"status": "event_processed"})

@app.get("/call_logs")
async def get_call_logs(
    limit: int = 10, 
    offset: int = 0,
    status: Optional[str] = None,
    direction: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get call logs with optional filtering"""
    # Base query
    query = db.query(CallRecord)
    
    # Apply filters if provided
    if status:
        query = query.filter(CallRecord.status == status)
    if direction:
        query = query.filter(CallRecord.direction == direction)
    
    # Get total count
    total_count = query.count()
    
    # Get active calls count
    active_calls = db.query(CallRecord).filter(CallRecord.status == "in-progress").count()
    
    # Apply pagination
    query = query.order_by(CallRecord.start_time.desc()).offset(offset).limit(limit)
    
    # Execute query
    calls = query.all()
    
    # Convert to dict
    call_data = []
    for call in calls:
        call_dict = {
            "call_id": call.call_id,
            "phone_number": call.phone_number,
            "direction": call.direction,
            "start_time": call.start_time.isoformat() if call.start_time else None,
            "end_time": call.end_time.isoformat() if call.end_time else None,
            "status": call.status,
            "intent": call.intent,
            "transcript": call.transcript
        }
        call_data.append(call_dict)
    
    return {
        "total_calls": total_count,
        "active_calls": active_calls,
        "calls": call_data,
        "pagination": {
            "limit": limit,
            "offset": offset,
            "has_more": total_count > offset + limit
        }
    }

@app.get("/call/{call_id}")
async def get_call_detail(call_id: str, db: Session = Depends(get_db)):
    """Get details for a specific call"""
    call = db.query(CallRecord).filter(CallRecord.call_id == call_id).first()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    
    return {
        "call_id": call.call_id,
        "phone_number": call.phone_number,
        "direction": call.direction,
        "start_time": call.start_time.isoformat() if call.start_time else None,
        "end_time": call.end_time.isoformat() if call.end_time else None,
        "status": call.status,
        "intent": call.intent,
        "duration": (call.end_time - call.start_time).total_seconds() if call.end_time else None,
        "transcript": call.transcript
    }

@app.get("/")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "running", 
        "version": "1.0",
        "services": {
            "tts": "ElevenLabs",
            "stt": os.getenv("STT_SERVICE", "Deepgram"),
            "call_handler": "Vapi/Twilio" if not call_handler.simulation_mode else "Simulated"
        },
        "simulation_mode": call_handler.simulation_mode
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
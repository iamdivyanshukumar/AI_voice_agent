from elevenlabs.client import ElevenLabs
from elevenlabs import play
from deepgram import DeepgramClient, PrerecordedOptions
import os
from dotenv import load_dotenv
import asyncio
import base64
from typing import Generator, Union, Dict, Any
import io

load_dotenv()

class VoiceService:
    def __init__(self):
        """Initialize TTS and STT services"""
        # ElevenLabs for Text-to-Speech
        self.elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY")
        self.elevenlabs_client = ElevenLabs(api_key=self.elevenlabs_api_key)
        self.elevenlabs_voice = os.getenv("ELEVENLABS_VOICE", "Rachel")
        self.elevenlabs_model = os.getenv("ELEVENLABS_MODEL", "eleven_monolingual_v1")
        
        # Deepgram for Speech-to-Text
        self.deepgram_api_key = os.getenv("DEEPGRAM_API_KEY")
        self.deepgram_client = DeepgramClient(self.deepgram_api_key)
        self.deepgram_model = os.getenv("DEEPGRAM_MODEL", "nova-2")
        
        # OpenAI Whisper as fallback for STT (if configured)
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.use_whisper = os.getenv("USE_WHISPER", "False").lower() == "true"
        
        # Default settings
        self.stt_service = os.getenv("STT_SERVICE", "deepgram").lower()  # "deepgram" or "whisper"
    
    def text_to_speech(self, text: str) -> Generator[bytes, None, None]:
        """Convert text to speech using ElevenLabs"""
        try:
            audio = self.elevenlabs_client.generate(
                text=text,
                voice=self.elevenlabs_voice,
                model=self.elevenlabs_model
            )
            
            # Return the audio as a generator
            yield from audio
        except Exception as e:
            raise Exception(f"TTS Error: {str(e)}")
    
    def get_audio_as_base64(self, text: str) -> str:
        """Convert text to speech and return as base64 encoded string"""
        try:
            # Generate and collect all audio chunks
            audio_generator = self.text_to_speech(text)
            audio_bytes = b"".join(audio_generator)
            
            # Convert to base64
            audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
            return audio_base64
        except Exception as e:
            raise Exception(f"TTS to Base64 Error: {str(e)}") 
    
    async def speech_to_text(self, audio_data: Union[bytes, str]) -> str:
        """
        Convert speech to text using the configured STT service
        
        Args:
            audio_data: Either raw bytes or base64 encoded string
        
        Returns:
            Transcribed text
        """
        # Convert base64 to bytes if needed
        if isinstance(audio_data, str):
            try:
                audio_data = base64.b64decode(audio_data)
            except Exception:
                raise Exception("Invalid base64 audio data")
        
        if self.stt_service == "deepgram":
            return await self._deepgram_stt(audio_data)
        elif self.stt_service == "whisper" and self.use_whisper:
            return await self._whisper_stt(audio_data)
        else:
            # Default to Deepgram if Whisper is not configured
            return await self._deepgram_stt(audio_data)
    
    async def _deepgram_stt(self, audio_data: bytes) -> str:
        """Use Deepgram for speech-to-text conversion"""
        try:
            options = PrerecordedOptions(
                model=self.deepgram_model,
                smart_format=True,
                punctuate=True,
                diarize=False
            )
            
            payload = {
                "buffer": audio_data
            }
            
            response = await self.deepgram_client.listen.prerecorded.v("1").transcribe_file(payload, options)
            
            # Extract the transcript
            if response and hasattr(response, "results"):
                return response.results.channels[0].alternatives[0].transcript
            return ""
        except Exception as e:
            raise Exception(f"Deepgram STT Error: {str(e)}")
    
    async def _whisper_stt(self, audio_data: bytes) -> str:
        """Use OpenAI Whisper for speech-to-text conversion"""
        try:
            import openai
            
            # Set the API key
            openai.api_key = self.openai_api_key
            
            # Save audio to a temporary file
            temp_file = io.BytesIO(audio_data)
            temp_file.name = "audio.wav"  # Give it a filename for MIME type detection
            
            # Call Whisper API
            response = await openai.Audio.atranscribe(
                model="whisper-1",
                file=temp_file
            )
            
            return response.get("text", "")
        except Exception as e:
            raise Exception(f"Whisper STT Error: {str(e)}")
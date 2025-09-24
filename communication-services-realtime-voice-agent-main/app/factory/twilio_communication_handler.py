import json
import logging
from datetime import datetime

from fastapi import WebSocket
from rtclient import InputAudioBufferAppendMessage
from starlette.websockets import WebSocketState
from twilio.rest import Client

from app.factory.base_communication_handler import BaseCommunicationHandler

logger = logging.getLogger(__name__)


class TwilioCommunicationHandler(BaseCommunicationHandler):
    """Twilio implementation"""
    voice_name = "echo"

    def __init__(self, websocket: WebSocket, stream_sid: str, twilio_client: Client, call_sid: str,
                 phone_number: str = None, customer_phone: str = None):
        super().__init__(stream_sid, phone_number, customer_phone)
        self.websocket = websocket
        self.twilio_client = twilio_client
        self.stream_sid = stream_sid
        self.call_sid = call_sid
        self.session_config = {
                "type": "session.update",
                "session": {
                    "voice": self.voice_name,
                    "input_audio_format": "g711_ulaw",
                    "input_audio_transcription": {
                        "model": "whisper-1",
                        "language": "sv"
                    },
                    "output_audio_format": "g711_ulaw",
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 200
                    },
                    "tool_choice": "auto"
                    # "temperature": TEMPERATURE
                }
            }

    async def initialize_call(self) -> bool:
        """Initialize Twilio call connection"""
        try:
            self.start_time = datetime.utcnow()
            logger.info(f"Twilio call {self.call_id} initialized")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize Twilio call: {e}")
            return False

    async def end_call(self) -> bool:
        """End Twilio call"""
        try:
            if self.call_ended or not self.call_sid:
                return True

            self.call_ended = True
            self.end_time = datetime.utcnow()

            # End the call via Twilio API
            call = self.twilio_client.calls(self.call_sid).update(status="completed")

            logger.info(f"Twilio call {self.call_sid} ended")
            return True
        except Exception as e:
            logger.error(f"Failed to end Twilio call: {e}")
            return False

    async def transfer_call(self, target_number: str, reason: str = "") -> bool:
        """Transfer Twilio call"""
        try:
            if self.call_ended or not self.call_sid:
                return False

            twiml = f"""
            <Response>
                <Dial>{target_number}</Dial>
            </Response>
            """

            call = self.twilio_client.calls(self.call_sid).update(twiml=twiml)
            self.call_ended = True

            logger.info(f"Twilio call transferred to {target_number}")
            return True
        except Exception as e:
            logger.error(f"Failed to transfer Twilio call: {e}")
            return False

    async def send_audio(self, audio_data: str) -> None:
        """Send audio via Twilio websocket"""
        try:
            audio_message = {
                "event": "media",
                "streamSid": self.stream_sid,
                "media": {"payload": audio_data}
            }
            await self.websocket.send_text(json.dumps(audio_message))
        except Exception as e:
            logger.error(f"Error sending Twilio audio: {e}")

    async def send_audio_async(self, rt_client, audio_data: str) -> None:
        await rt_client.send(
            message=InputAudioBufferAppendMessage(
                type="input_audio_buffer.append", audio=audio_data, _is_azure=True
            )
        )

    async def send_message_async(self, message: str) -> None:
        try:
            if self.websocket.client_state == WebSocketState.CONNECTED:
                await self.websocket.send_text(message)
        except Exception as e:
            logger.error(f"Send Message - Failed to send message: {e}")
            raise e

    async def receive_audio(self, data_payload) -> None:
        try:
            audio_data = {
                "event": "media",
                "streamSid": self.stream_sid,
                "media": {"payload": data_payload}
            }
            await self.send_message_async(json.dumps(audio_data))
        except Exception as e:
            logger.error(f"Error sending audio: {e}")

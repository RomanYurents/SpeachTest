import base64
import json
import logging
from datetime import datetime

from fastapi import WebSocket
from scipy.signal import resample

from rtclient import InputAudioBufferAppendMessage
from starlette.websockets import WebSocketState
from twilio.rest import Client

from app.factory.base_communication_handler import BaseCommunicationHandler
import numpy as np

logger = logging.getLogger(__name__)

MU_LAW_DECODE_TABLE = np.array([
    -32124, -31100, -30076, -29052, -28028, -27004, -25980, -24956,
    -23932, -22908, -21884, -20860, -19836, -18812, -17788, -16764,
    -15996, -15484, -14972, -14460, -13948, -13436, -12924, -12412,
    -11900, -11388, -10876, -10364, -9852, -9340, -8828, -8316,
    -7932, -7676, -7420, -7164, -6908, -6652, -6396, -6140,
    -5884, -5628, -5372, -5116, -4860, -4604, -4348, -4092,
    -3900, -3772, -3644, -3516, -3388, -3260, -3132, -3004,
    -2876, -2748, -2620, -2492, -2364, -2236, -2108, -1980,
    -1884, -1820, -1756, -1692, -1628, -1564, -1500, -1436,
    -1372, -1308, -1244, -1180, -1116, -1052, -988, -924,
    -876, -844, -812, -780, -748, -716, -684, -652,
    -620, -588, -556, -524, -492, -460, -428, -396,
    -372, -356, -340, -324, -308, -292, -276, -260,
    -244, -228, -212, -196, -180, -164, -148, -132,
    -120, -112, -104, -96, -88, -80, -72, -64,
    -56, -48, -40, -32, -24, -16, -8, 0,
    32124, 31100, 30076, 29052, 28028, 27004, 25980, 24956,
    23932, 22908, 21884, 20860, 19836, 18812, 17788, 16764,
    15996, 15484, 14972, 14460, 13948, 13436, 12924, 12412,
    11900, 11388, 10876, 10364, 9852, 9340, 8828, 8316,
    7932, 7676, 7420, 7164, 6908, 6652, 6396, 6140,
    5884, 5628, 5372, 5116, 4860, 4604, 4348, 4092,
    3900, 3772, 3644, 3516, 3388, 3260, 3132, 3004,
    2876, 2748, 2620, 2492, 2364, 2236, 2108, 1980,
    1884, 1820, 1756, 1692, 1628, 1564, 1500, 1436,
    1372, 1308, 1244, 1180, 1116, 1052, 988, 924,
    876, 844, 812, 780, 748, 716, 684, 652,
    620, 588, 556, 524, 492, 460, 428, 396,
    372, 356, 340, 324, 308, 292, 276, 260,
    244, 228, 212, 196, 180, 164, 148, 132,
    120, 112, 104, 96, 88, 80, 72, 64,
    56, 48, 40, 32, 24, 16, 8, 0
], dtype=np.int16)


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
        self.audio_format = "g711_ulaw"
        self.is_closed = False
        self.voice_live_rate = 16_000  # Because of VoIP

    async def initialize_call(self) -> bool:
        """Initialize Twilio call connection"""
        try:
            self.start_time = datetime.utcnow()
            logger.error(f"Twilio call {self.call_id} initialized")
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

            logger.error(f"Twilio call {self.call_sid} ended")
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

            logger.error(f"Twilio call transferred to {target_number}")
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

    def twilio_ulaw_to_azure_pcm16(self, audio_data: bytes, target_rate: int = 16000) -> str:
        ulaw = np.frombuffer(audio_data, dtype=np.uint8)
        pcm16 = MU_LAW_DECODE_TABLE[ulaw]

        original_rate = 8000
        if target_rate != original_rate:
            num_samples = int(len(pcm16) * target_rate / original_rate)
            pcm16 = resample(pcm16, num_samples).astype(np.int16)

        return base64.b64encode(pcm16.tobytes()).decode("utf-8")

    async def send_audio_async(self, rt_client, audio_data: str, mode: str = "voice_live") -> None:
        audio_pcm16 = self.twilio_ulaw_to_azure_pcm16(
            base64.b64decode(audio_data)) if mode == "voice_live" else audio_data
        await rt_client.send(
            message=InputAudioBufferAppendMessage(
                type="input_audio_buffer.append", audio=audio_pcm16, _is_azure=True
            )
        )

    async def send_message_async(self, message: str) -> None:
        try:
            if self.websocket.client_state == WebSocketState.CONNECTED and not self.is_closed:
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

    async def stop_audio(self) -> None:
        """Stop audio playback in Twilio media stream"""
        try:
            stop_message = {
                "event": "clear",
                "streamSid": self.stream_sid
            }
            await self.send_message_async(json.dumps(stop_message))
            logger.info(f"Stopped audio for Twilio stream {self.stream_sid}")
        except Exception as e:
            logger.error(f"Error stopping Twilio audio: {e}")

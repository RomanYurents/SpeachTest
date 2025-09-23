import json
import logging
from datetime import datetime

from azure.communication.callautomation import CallAutomationClient, PhoneNumberIdentifier
from fastapi import WebSocket
from rtclient import InputAudioBufferAppendMessage
from starlette.websockets import WebSocketState

from app.factory.base_communication_handler import BaseCommunicationHandler

logger = logging.getLogger(__name__)


class AzureCommunicationHandler(BaseCommunicationHandler):
    """Azure Communication Services implementation"""
    voice_name = "cedar"

    def __init__(self, websocket: WebSocket, call_connection_id: str,
                 acs_client: CallAutomationClient, phone_number: str = None,
                 customer_phone: str = None):
        super().__init__(call_connection_id, phone_number, customer_phone)
        self.websocket = websocket
        self.acs_client = acs_client
        self.call_connection_id = call_connection_id
        self.session_config = {
            "type": "session.update",
            "session": {
                "voice": self.voice_name,
                # "instructions": self.system_prompt,
                "input_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": "whisper-1",
                    "language": "sv"
                },
                "turn_detection": {
                    "threshold": 0.3,
                    "silence_duration_ms": 300,
                    "prefix_padding_ms": 500,
                    "type": "server_vad",
                },
                # "tools": self.tools,
                "tool_choice": "auto"
            },
        }

    async def initialize_call(self) -> bool:
        """Initialize Azure call connection"""
        try:
            self.start_time = datetime.utcnow()
            logger.info(f"Azure call {self.call_id} initialized")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize Azure call: {e}")
            return False

    async def end_call(self) -> bool:
        """End Azure call"""
        try:
            if self.call_ended:
                return True

            self.call_ended = True
            self.end_time = datetime.utcnow()

            call_connection = self.acs_client.get_call_connection(self.call_connection_id)
            call_connection.hang_up(is_for_everyone=True)

            logger.info(f"Azure call {self.call_id} ended")
            return True
        except Exception as e:
            logger.error(f"Failed to end Azure call: {e}")
            return False

    async def transfer_call(self, target_number: str, reason: str = "") -> bool:
        """Transfer Azure call"""
        try:
            if self.call_ended:
                return False

            target_participant = PhoneNumberIdentifier(target_number)
            call_connection = self.acs_client.get_call_connection(self.call_connection_id)

            transfer_result = call_connection.transfer_call_to_participant(
                target_participant=target_participant
            )

            self.call_ended = True
            logger.info(f"Azure call transferred: {transfer_result}")
            return True
        except Exception as e:
            logger.error(f"Failed to transfer Azure call: {e}")
            return False

    async def send_audio(self, audio_data: str) -> None:
        """Send audio via Azure websocket"""
        try:
            audio_message = {
                "Kind": "AudioData",
                "AudioData": {"Data": audio_data},
                "StopAudio": None,
            }
            await self.websocket.send_text(json.dumps(audio_message))
        except Exception as e:
            logger.error(f"Error sending Azure audio: {e}")

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
            logger.error((f"Send Message - Failed to send message: {e}"))
            raise e

    async def receive_audio(self, data_payload) -> None:
        try:
            audio_data = {
                "Kind": "AudioData",
                "AudioData": {"Data": data_payload},
                "StopAudio": None,
            }
            await self.send_message_async(json.dumps(audio_data))
        except Exception as e:
            logger.error(f"Error sending audio: {e}")

    async def stop_audio(self) -> None:
        """Stop audio playback"""
        try:
            stop_message = {
                "Kind": "StopAudio",
                "AudioData": None,
                "StopAudio": {}
            }
            await self.websocket.send_text(json.dumps(stop_message))
        except Exception as e:
            logger.error(f"Error stopping Azure audio: {e}")

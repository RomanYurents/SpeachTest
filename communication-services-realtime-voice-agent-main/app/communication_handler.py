import json
import os
import uuid
from dotenv import load_dotenv
from fastapi import WebSocket
from fastapi.websockets import WebSocketState
from azure.core.credentials import AzureKeyCredential
import asyncio
from aiologger import Logger
from rtclient import (
    InputTextContentPart,
    ItemCreateMessage,
    RTLowLevelClient,
    ResponseCreateMessage,
    SessionUpdateMessage,
    ServerMessageType,
    UserMessageItem,
    InputAudioBufferAppendMessage,
)
from azure.communication.callautomation import CallAutomationClient

load_dotenv()

logger = Logger.with_default_handlers()

# Azure OpenAI Realtime environment variables
AZURE_OPENAI_REALTIME_ENDPOINT = "https://oai-sellifyai.openai.azure.com/openai/"
AZURE_OPENAI_REALTIME_SERVICE_KEY = "DkrrkVjmBb6ce8fbDcexApHXQoQ0cef3mh7Jumll2lZC35OHqGisJQQJ99BGACfhMk5XJ3w3AAABACOG1JST"
AZURE_OPENAI_REALTIME_DEPLOYMENT_MODEL_NAME = "gpt-4o-mini-realtime-preview"

# Farewell detection list
FAREWELL_PHRASES = [
    "thanks, that's all",
    "thank you, bye",
    "thank you",
    "bye",
    "goodbye",
    "that's all",
    "see you",
    "talk to you later",
]

class CommunicationHandler:
    voice_name = "shimmer"
    system_prompt = (
        "You are an AI assistant. Answer user questions clearly and helpfully. Keep responses concise."
    )

    def __init__(self, websocket: WebSocket, call_connection_id: str, acs_client: CallAutomationClient) -> None:
        self.rt_client = None
        self.active_websocket = websocket
        self.call_connection_id = call_connection_id
        self.acs_client = acs_client
        self.call_ended = False  # Prevent double hangup

    async def start_conversation_async(self) -> None:
        self.rt_client = RTLowLevelClient(
            url=AZURE_OPENAI_REALTIME_ENDPOINT,
            key_credential=AzureKeyCredential(AZURE_OPENAI_REALTIME_SERVICE_KEY),
            azure_deployment=AZURE_OPENAI_REALTIME_DEPLOYMENT_MODEL_NAME,
        )
        try:
            await self.rt_client.connect()
        except Exception as e:
            print(f"Failed to connect to Azure OpenAI Realtime Service: {e}")
            raise e

        session_update_message = {
            "type": "session.update",
            "session": {
                "voice": self.voice_name,
                "instructions": self.system_prompt,
                "input_audio_format": "pcm16",
                "input_audio_transcription": {"model": "whisper-1"},
                "turn_detection": {
                    "threshold": 0.6,
                    "silence_duration_ms": 300,
                    "prefix_padding_ms": 200,
                    "type": "server_vad",
                },
            },
        }

        session_update_payload = SessionUpdateMessage(**session_update_message)
        await self.rt_client.send(session_update_payload)

        # Initial greeting
        self.conversation_call_id = str(uuid.uuid4())
        content_part = InputTextContentPart(
            text="Hello! I am your AI assistant. How can I help you today?"
        )
        initial_message = ItemCreateMessage(
            item=UserMessageItem(content=[content_part]),
            call_id=self.conversation_call_id
        )
        await self.rt_client.send(message=initial_message)
        await self.rt_client.send(ResponseCreateMessage())

        asyncio.create_task(self.receive_messages_async())

    async def send_message_async(self, message: str) -> None:
        try:
            if self.active_websocket.client_state == WebSocketState.CONNECTED:
                await self.active_websocket.send_text(message)
        except Exception as e:
            logger.error(f"Send Message - Failed to send message: {e}")
            raise e

    async def receive_messages_async(self) -> None:
        try:
            while not self.rt_client.closed:
                message: ServerMessageType = await self.rt_client.recv()
                if message is None or self.rt_client.ws.closed:
                    continue

                match message.type:
                    case "conversation.item.input_audio_transcription.completed":
                        transcript = message.transcript.lower()
                        print(f"User: {transcript}")
                        await self.detect_farewell(transcript)

                    case "response.audio_transcript.done":
                        print(f"AI: {message.transcript}")

                    case "response.audio.delta":
                        await self.receive_audio(message.delta)

                    case "response.done":
                        print(f"Response Done: {message.response.id}")
                         # If we've marked the call for end, now send ResponseCreateMessage and hang up
                        if self.call_ended:
                            await asyncio.sleep(1)  # Give it a moment to flush the audio
                            await self.rt_client.send(ResponseCreateMessage())

                            # Give the user some time to hear it
                            await asyncio.sleep(6)

                            try:
                                call_connection = self.acs_client.get_call_connection(self.call_connection_id)
                                await call_connection.hang_up(is_for_everyone=True)
                                logger.info(f"Call {self.call_connection_id} ended.")
                            except Exception as e:
                                logger.error(f"Failed to hang up call {self.call_connection_id}: {e}")

                    case "error":
                        print(f"Error: {message.error}")
        except Exception as e:
            logger.error(f"Error in receive_messages_async: {e}")
            if not isinstance(e, asyncio.CancelledError):
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
            print(f"Error sending audio: {e}")

    async def send_audio_async(self, audio_data: str) -> None:
        await self.rt_client.send(
            message=InputAudioBufferAppendMessage(
                type="input_audio_buffer.append", audio=audio_data, _is_azure=True
            )
        )

    async def detect_farewell(self, transcript: str) -> None:
        if any(phrase in transcript for phrase in FAREWELL_PHRASES):
            await self.say_and_hang_up("Thank you for calling. Goodbye!")

    async def say_and_hang_up(self, message: str) -> None:
        if self.call_ended:
            return
        self.call_ended = True

        # Send final goodbye message
        content_part = InputTextContentPart(text=message)
        final_message = ItemCreateMessage(
            item=UserMessageItem(content=[content_part]),
            call_id=self.conversation_call_id
        )
        await self.rt_client.send(message=final_message)

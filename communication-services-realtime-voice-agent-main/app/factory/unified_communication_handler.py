import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from enum import Enum
from typing import List, Dict, Any

import httpx
from azure.core.credentials import AzureKeyCredential
from dotenv import load_dotenv
from openai import AsyncAzureOpenAI
from pydantic import BaseModel, Field

from app.business_context import BusinessContextManager
from app.factory.base_communication_handler import BaseCommunicationHandler
from rtclient import RTLowLevelClient

logger = logging.getLogger(__name__)


class Role(str, Enum):
    AI = "ai"
    USER = "user"


class Message(BaseModel):
    role: Role
    message: str


allowed_languages: List[str] = ['af', 'ar', 'az', 'be', 'bg', 'bs', 'ca', 'cs', 'cy', 'da', 'de', 'el', 'en', 'es',
                                'et', 'fa', 'fi', 'fr', 'gl', 'he', 'hi', 'hr', 'hu', 'hy', 'id', 'is', 'it', 'ja',
                                'kk', 'kn', 'ko', 'lt', 'lv', 'mi', 'mk', 'mr', 'ms', 'ne', 'nl', 'no', 'pl', 'pt',
                                'ro', 'ru', 'sk', 'sl', 'sr', 'sv', 'sw', 'ta', 'th', 'tl', 'tr', 'uk', 'ur', 'vi',
                                'zh']

load_dotenv()


@asynccontextmanager
async def measure_time(label: str):
    start = time.perf_counter()
    try:
        yield
    finally:
        end = time.perf_counter()
        logger.error(f"{label} completed in {end - start:.3f} seconds")


class UnifiedConversationHandler:
    """Unified conversation handler that works with any communication provider"""

    def __init__(self, comm_handler: BaseCommunicationHandler):
        self.comm_handler = comm_handler
        self.rt_client = None
        self.business_context = None
        self.system_prompt = "An error occurred, can not load context from db"
        self.conversation = []
        self.ai_speaking = False
        self.finish_requested = False
        self.tools = self._get_tools()
        self.is_order = False

        self.voice = os.getenv("SESSION_VOICE", "cedar")
        self.azure_voice = os.getenv("AZURE_VOICE", "sv-SE-SofieNeural")
        self.azure_voicelive_model = os.getenv("AZURE_VOICELIVE_MODEL", "gpt-4o-mini-realtime-preview")
        self.voice_rate = os.getenv("AZURE_VOICE_RATE", "1.0")
        self.threshold = float(os.getenv("SESSION_TURN_THRESHOLD", 0.3))
        self.silence_duration = int(os.getenv("SESSION_SILENCE_DURATION_MS", 300))
        self.prefix_padding = int(os.getenv("SESSION_PREFIX_PADDING_MS", 500))
        self.input_audio_transcription_model = os.getenv("INPUT_AUDIO_TRANSCRIPTION_MODEL", 'azure-speech')
        self.turn_detection_type = os.getenv("TURN_DETECTION_TYPE", 'azure_semantic_vad')

        self.azure_voicelive_endpoint = os.getenv("AZURE_VOICELIVE_ENDPOINT")
        self.azure_voicelive_version = os.getenv("AZURE_VOICELIVE_VERSION", "2025-10-01")
        self.azure_voicelive_api_key = os.getenv("AZURE_VOICELIVE_API_KEY")

        self.azure_openai_realtime_endpoint = os.getenv("AZURE_OPENAI_REALTIME_ENDPOINT")
        self.azure_openai_realtime_service_key = os.getenv("AZURE_OPENAI_REALTIME_SERVICE_KEY")
        self.azure_openai_realtime_deployment = os.getenv("AZURE_OPENAI_REALTIME_DEPLOYMENT_MODEL_NAME")

        self.connect_mode = os.getenv("CONNECT_MODE", 'voice_live')

    def _get_tools(self) -> List[Dict[str, Any]]:
        """Get available tools for the conversation"""
        return [
            {
                "type": "function",
                "name": "transfer_call",
                "description": "Transfer the call to a human agent",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Reason for transferring the call",
                        },
                    },
                    "required": ["reason"],
                },
            },
            {
                "type": "function",
                "name": "hangup",
                "description": "Finish the conversation and hang up the call",
                "parameters": {},
            }
        ]

    async def initialize_business_context(self) -> None:
        """Initialize business context"""
        if self.comm_handler.phone_number:
            async with BusinessContextManager() as service:
                self.business_context, self.system_prompt = await service.get_context_and_prompt(
                    self.comm_handler.phone_number
                )
                logger.error(f"Business context initialized for: {self.comm_handler.phone_number}")
        else:
            logger.error(f"Phone number does not provided")

    async def start_conversation(self) -> None:
        """Start the conversation"""
        try:
            async with measure_time(f"Call initialization {self.comm_handler.phone_number}"):
                await self.comm_handler.initialize_call()

            async with measure_time(f"Business context initialization {self.comm_handler.phone_number}"):
                await self.initialize_business_context()

            async with measure_time(f"RT client initialization {self.comm_handler.phone_number}"):
                await self._initialize_rt_client()

            # Start message processing
            asyncio.create_task(self._process_messages())

        except Exception as e:
            logger.error(f"Failed to start conversation: {e}")
            await self.comm_handler.end_call()

    async def _initialize_rt_client(self) -> None:
        """Initialize Azure Live API Realtime client"""
        from rtclient.models import (
            SessionUpdateMessage,
            SessionUpdateParams,
            ResponseCreateMessage,
            AzureSemanticVAD,
            InputAudioTranscription,
            AzureVoiceConfig,
            InputAudioNoiseReduction,
            InputAudioEchoCancellation,
        )

        if self.connect_mode == "realtime":
            self.rt_client = RTLowLevelClient(
                url=self.azure_openai_realtime_endpoint,
                key_credential=AzureKeyCredential(self.azure_openai_realtime_service_key),
                azure_deployment=self.azure_openai_realtime_deployment,
            )
        elif self.connect_mode == "voice_live":
            self.rt_client = RTLowLevelClient(
                azure_endpoint=self.azure_voicelive_endpoint,
                model=self.azure_voicelive_model,
                api_version=self.azure_voicelive_version,
                api_key=self.azure_voicelive_api_key,
            )

        await self.rt_client.connect(mode=self.connect_mode)

        if self.connect_mode == "realtime":
            if self.turn_detection_type == 'azure_semantic_vad':
                self.turn_detection_type = 'semantic_vad'
            turn_detection_config = {"type": self.turn_detection_type}

            upd_session = {
                "type": "session.update",
                "session": {
                    "voice": self.voice,
                    "instructions": self.system_prompt,
                    "input_audio_format": self.comm_handler.audio_format,
                    "output_audio_format": self.comm_handler.audio_format,
                    "input_audio_transcription": {
                        "model": self.input_audio_transcription_model
                    },
                    "turn_detection": turn_detection_config,
                    "input_audio_noise_reduction": {
                        "type": "near_field"
                    },
                    "tools": self.tools,
                    "tool_choice": "auto"
                },
            }

            if self.business_context and self.business_context.default_ai_language in allowed_languages:
                upd_session['session']['input_audio_transcription'][
                    'language'] = self.business_context.default_ai_language

            await self.rt_client.send_raw(upd_session)

        elif self.connect_mode == "voice_live":
            if self.turn_detection_type == 'semantic_vad':
                self.turn_detection_type = 'azure_semantic_vad'
            # Build voice configuration
            voice_config = AzureVoiceConfig(
                name=self.azure_voice,
                type="azure-standard",
                temperature=0.8,
                rate=self.voice_rate,
            )

            # Build input audio transcription
            input_audio_transcription = InputAudioTranscription(
                model="azure-speech",
                language=self.business_context.default_ai_language if self.business_context.default_ai_language else "sv",
            )

            # Build input audio noise reduction
            input_audio_noise_reduction = InputAudioNoiseReduction(
                type="azure_deep_noise_suppression",
            )

            # Build input audio echo cancellation
            input_audio_echo_cancellation = InputAudioEchoCancellation(
                type="server_echo_cancellation",
            )

            # Build turn detection configuration
            turn_detection = AzureSemanticVAD(
                type=self.turn_detection_type,
                threshold=self.threshold or 0.3,
                prefix_padding_ms=self.prefix_padding or 200,
                silence_duration_ms=self.silence_duration or 200,
                remove_filler_words=False,
                # end_of_utterance_detection=EndOfUtteranceDetection(
                #     model="semantic_detection_v1",
                #     threshold=0.01,
                #     timeout=2,
                # ),
            )

            # Build session update parameters
            session_params = SessionUpdateParams(
                model=self.azure_voicelive_model,
                modalities={"text", "audio"},
                instructions=self.system_prompt,
                voice=voice_config,
                turn_detection=turn_detection,
                input_audio_transcription=input_audio_transcription,
                input_audio_noise_reduction=input_audio_noise_reduction,
                input_audio_echo_cancellation=input_audio_echo_cancellation,
                input_audio_sampling_rate=24000,
                input_audio_format=self.comm_handler.audio_format,
                output_audio_format=self.comm_handler.audio_format,
                temperature=0.7,
                tools=self.tools if hasattr(self, 'tools') else [],
                tool_choice="auto",
            )

            session_update_msg = SessionUpdateMessage(session=session_params)
            await self.rt_client.send(session_update_msg)

        await self.rt_client.send(ResponseCreateMessage())

        logger.info("Azure Live API Realtime client initialized successfully")

    async def handle_tool_call(self, tool_name: str, arguments: Dict[str, Any]) -> None:
        """Handle tool calls"""

        logger.error(f"Received tool call: {tool_name}")

        if tool_name == "transfer_call":
            reason = arguments.get("reason", "User requested transfer")
            if self.business_context and hasattr(self.business_context, 'human_phone'):
                await self.comm_handler.transfer_call(
                    self.business_context.human_phone, reason
                )
            else:
                logger.error("Business context not initialized or business context does not have human phone")

        elif tool_name == "hangup":
            self.finish_requested = True
            for _ in range(50):
                if not self.ai_speaking:
                    break
                logger.error("Wait before HANGUP. Ai is speaking...")
                await asyncio.sleep(0.1)

            await asyncio.sleep(2)  # Brief delay
            await self._hangup()

    async def _hangup(self) -> None:
        """Finish the conversation"""
        try:
            # End the call
            await self.comm_handler.end_call()

            # Save order data if applicable
            if self.business_context and self.business_context.is_open and not self.is_order:
                await self._save_order_data()

                self.is_order = True

        except Exception as e:
            logger.error(f"Error finishing conversation: {e}")

    async def _save_order_data(self) -> None:
        """Save order data to server"""
        try:
            # Parse order using GPT (your existing logic)
            async with measure_time(f"Parsed order"):
                parsed_order = await self._gpt_parse_order()

            # Save to server
            url = f"{os.getenv('AITELL_SERVER_URI')}/orders"
            headers = {
                "x-api-key": os.getenv("AITELL_SERVER_API_KEY"),
                "Content-Type": "application/json"
            }

            email = parsed_order.get("customerEmail")
            payload = {
                "businessId": parsed_order.get("businessId", ""),
                "customerName": parsed_order.get("customerName", ""),
                "customerPhone": parsed_order.get("customerPhone", ""),
                "customerEmail": email if email.strip() else None,
                "customerAddress": parsed_order.get("customerAddress", ""),
                "orderItems": parsed_order.get("orderItems", []),
                "currency": parsed_order.get("currency", "USD"),
                "conversationHistory": [
                    {"role": m.role.name.lower(), "message": m.message} for m in self.conversation
                ],
                "specialInstructions": parsed_order.get("specialInstructions", ""),
                "estimatedCompletionTime": parsed_order.get(
                    "estimatedCompletionTime",
                    datetime.utcnow().isoformat() + "Z"
                ),
                "paymentMethod": parsed_order.get("paymentMethod", ""),
                "source": parsed_order.get("source", "phone"),
                "status": parsed_order.get("status", "unhandled"),
                "conversationTime": await self.comm_handler.get_call_duration(),
            }

            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, headers=headers)
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    logger.error(
                        f"Failed to save order: {exc.response.status_code} - {exc.response.text}"
                    )

                    try:
                        err_data = exc.response.json()
                        message = err_data.get("message", "Unknown error")
                    except Exception:
                        message = exc.response.text

                    raise Exception(f"Order not saved: {message}")
                else:
                    logger.info(f"Order saved successfully: {response.status_code}")

        except Exception as e:
            logger.error(f"Failed to save order: {e}")

    async def _gpt_parse_order(self) -> Dict[str, Any]:
        """Parse order using GPT"""
        try:
            api_version = "2025-01-01-preview"

            client = AsyncAzureOpenAI(
                api_version=api_version,
                azure_endpoint=os.getenv("AZURE_OPENAI_GPT4OMINI_ENDPOINT"),
                api_key=os.getenv("AZURE_OPENAI_GPT4OMINI_API_KEY"),
            )

            chat_prompt = [
                {
                    "role": "system",
                    "content":
                        [
                            {
                                "type": "text",
                                "text": f"""
        You are a smart assistant that extract order data from User-AI phone conversation text.
        - customerName: the client's name from the conversation
        - customerEmail: clients email from conversation or empty string ''
        - customerAddress: the client's address from the conversation
        - orderItems: list of ordered dishes in final order, each containing:
           • item_id: id for current item from context
           • quantity: number of portions
        - currency: use "USD"
        - specialInstructions: leave as empty string
        - estimatedCompletionTime: leave as empty string
        - paymentMethod: use "card"
        - source: use "phone"
        - status: use "confirmed"

        Here are available services with item_name and item_id {self.business_context.services}
                            """
                            }
                        ]
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Conversation User-AI text: {self.conversation}"
                        }
                    ]
                }
            ]

            logger.error(f"[Conversation transcript] - {self.conversation}")

            messages = chat_prompt

            valid_item_ids = []
            if self.business_context and self.business_context.services:
                import re
                id_pattern = re.compile(r'ID: ([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})')
                valid_item_ids = id_pattern.findall(self.business_context.services)

            if valid_item_ids:
                ValidItemEnum = Enum('ValidItemEnum', {item_id: item_id for item_id in valid_item_ids})
            else:
                # Handle the case where there are no items
                ValidItemEnum = Enum('ValidItemEnum', {'NO_ITEMS': 'no_items_available'})

            class OrderItem(BaseModel):
                id: ValidItemEnum = Field(..., description="Id for current item from context")
                quantity: int = Field(..., description="Amount of portions from conversation")

            class OrderData(BaseModel):
                customerName: str = Field(..., description="The name of client from conversation")
                customerPhone: str = Field(..., description="The phone number of client in format +1234567890")
                customerEmail: str = Field(..., description="Always 'customer@gmail.com'")
                customerAddress: str = Field(..., description="The address of client from conversation")
                orderItems: List[OrderItem] = Field(..., description="List of ordered items")
                totalAmount: float = Field(...,
                                           description="The sum of prices of all ordered dishes, always 2 digits after comma")
                currency: str = Field(..., description="Currency, e.g., USD")
                specialInstructions: str = Field(..., description="Leave this field empty string")
                estimatedCompletionTime: str = Field(..., description="Leave this field empty string")
                paymentMethod: str = Field(..., description="Payment method, e.g., card")
                source: str = Field(..., description="Order source, e.g., web")
                status: str = Field(..., description="Order status, e.g., confirmed")

            completion = await client.chat.completions.parse(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=16384,
                temperature=0,
                top_p=0.95,
                frequency_penalty=0,
                presence_penalty=0,
                stop=None,
                response_format=OrderData
            )

            await client.close()

            result = json.loads(completion.choices[0].message.content)
            result['businessId'] = str(self.business_context.id)
            result['customerPhone'] = self.comm_handler.customer_phone

            return result

        except Exception as e:
            logger.error(f"GPT Parse Order - Failed to parse order: {e}")
            raise e

    async def _process_messages(self) -> None:
        """Process messages from RT client"""
        try:
            while not self.rt_client.closed:
                message = await self.rt_client.recv()

                if message is None:
                    continue

                match message.type:
                    case "response.audio.delta":
                        self.ai_speaking = True
                        await self.comm_handler.receive_audio(message.delta)

                    case "response.done":
                        self.ai_speaking = False
                        if self.finish_requested:
                            await self._hangup()

                    case "response.function_call_arguments.done":
                        arguments = json.loads(message.arguments)
                        async with measure_time(f"Handle tool call {message.name}"):
                            await self.handle_tool_call(message.name, arguments)

                    case "input_audio_buffer.speech_stopped":
                        logger.error("Detected speech stopped.")
                        # await self.reset_silence_timer()

                    case "input_audio_buffer.speech_started":
                        logger.info("Detected speech started.")
                        await self.comm_handler.stop_audio()
                        # await self.reset_silence_timer()

                    case "conversation.item.input_audio_transcription.completed":
                        transcript = message.transcript.lower()
                        user_message = f"User: {transcript}"
                        self.conversation.append(Message(role=Role.USER, message=user_message))
                        logger.error(user_message)
                        # await self.reset_silence_timer()

                    case "response.audio_transcript.done":
                        ai_message = f"AI: {message.transcript}"
                        self.conversation.append(Message(role=Role.AI, message=ai_message))
                        logger.error(ai_message)
                        # await self.reset_silence_timer()

                    case "error":
                        logger.error(f"Error: {message.error}")

                    case _:
                        pass

        except Exception as e:
            logger.error(f"Error processing messages: {e}")

    async def send_audio_async(self, audio_data: str) -> None:
        await self.comm_handler.send_audio_async(self.rt_client, audio_data)

# Usage examples:

# For Azure:
# azure_handler = CommunicationHandlerFactory.create_handler(
#     CommunicationProvider.AZURE,
#     websocket=websocket,
#     call_connection_id=call_connection_id,
#     acs_client=acs_client,
#     phone_number=phone_number,
#     customer_phone=customer_phone
# )
# conversation_handler = UnifiedConversationHandler(azure_handler)
# await conversation_handler.start_conversation()

# For Twilio:
# twilio_handler = CommunicationHandlerFactory.create_handler(
#     CommunicationProvider.TWILIO,
#     websocket=websocket,
#     stream_sid=stream_sid,
#     twilio_client=twilio_client,
#     phone_number=phone_number,
#     customer_phone=customer_phone
# )
# conversation_handler = UnifiedConversationHandler(twilio_handler)
# await conversation_handler.start_conversation()

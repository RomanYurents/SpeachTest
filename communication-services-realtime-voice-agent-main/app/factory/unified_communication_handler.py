import asyncio
import json
import logging
import os
from datetime import datetime
from enum import Enum
from typing import List, Dict, Any

import httpx
from openai import AsyncAzureOpenAI
from pydantic import BaseModel, Field
from rtclient import RTLowLevelClient, SessionUpdateMessage, ResponseCreateMessage

from app.business_context import BusinessContextManager
from app.factory.base_communication_handler import BaseCommunicationHandler

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


class UnifiedConversationHandler:
    """Unified conversation handler that works with any communication provider"""

    def __init__(self, comm_handler: BaseCommunicationHandler):
        self.comm_handler = comm_handler
        self.rt_client = None
        self.business_context = None
        self.system_prompt = "speak ukrainian"
        self.conversation = []
        self.ai_speaking = False
        self.finish_requested = False
        self.tools = self._get_tools()

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
                logger.info(f"Business context initialized for: {self.comm_handler.phone_number}")
        else:
            logger.warning(f"Phone number does not provided")

    async def start_conversation(self) -> None:
        """Start the conversation"""
        try:
            # Initialize communication handler
            await self.comm_handler.initialize_call()

            # Initialize business context
            await self.initialize_business_context()

            # Initialize RT client
            await self._initialize_rt_client()

            # Start message processing
            asyncio.create_task(self._process_messages())

        except Exception as e:
            logger.error(f"Failed to start conversation: {e}")
            await self.comm_handler.end_call()

    async def _initialize_rt_client(self) -> None:
        """Initialize Azure OpenAI Realtime client"""
        from azure.core.credentials import AzureKeyCredential

        self.rt_client = RTLowLevelClient(
            url=os.getenv("AZURE_OPENAI_REALTIME_ENDPOINT"),
            key_credential=AzureKeyCredential(os.getenv("AZURE_OPENAI_REALTIME_SERVICE_KEY")),
            azure_deployment=os.getenv("AZURE_OPENAI_REALTIME_DEPLOYMENT_MODEL_NAME"),
        )

        await self.rt_client.connect()

        self.comm_handler.session_config['session']['instructions'] = self.system_prompt
        self.comm_handler.session_config['session']['tools'] = self.tools

        if self.business_context.default_ai_language in allowed_languages:
            self.comm_handler.session_config['session']['language'] = self.business_context.default_ai_language
        else:
            self.comm_handler.session_config['session']['language'] = None

        await self.rt_client.send(SessionUpdateMessage(**self.comm_handler.session_config))
        await self.rt_client.send(ResponseCreateMessage())

    async def handle_tool_call(self, tool_name: str, arguments: Dict[str, Any]) -> None:
        """Handle tool calls"""

        logger.info(f"Received tool call: {tool_name}")

        if tool_name == "transfer_call":
            reason = arguments.get("reason", "User requested transfer")
            if self.business_context and hasattr(self.business_context, 'human_phone'):
                await self.comm_handler.transfer_call(
                    self.business_context.human_phone, reason
                )
            else:
                logger.info("Business context not initialized or business context does not have human phone")

        elif tool_name == "hangup":
            self.finish_requested = True
            for _ in range(50):
                if not self.ai_speaking:
                    break
                logger.info("Wait before HANGUP. Ai is speaking...")
                await asyncio.sleep(0.1)

            await self._hangup()

    async def _hangup(self) -> None:
        """Finish the conversation"""
        try:
            # Save order data if applicable
            if self.business_context and self.business_context.is_open:
                await self._save_order_data()

            # End the call
            await self.comm_handler.end_call()

        except Exception as e:
            logger.error(f"Error finishing conversation: {e}")

    async def _save_order_data(self) -> None:
        """Save order data to server"""
        try:
            # Parse order using GPT (your existing logic)
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
                logger.info(f"Order saved: {response.status_code}")

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

            logger.info(f"[Conversation transcript] - {self.conversation}")

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
                            await asyncio.sleep(2)  # Brief delay
                            await self._hangup()

                    case "response.function_call_arguments.done":
                        arguments = json.loads(message.arguments)
                        await self.handle_tool_call(message.name, arguments)

                    case "input_audio_buffer.speech_stopped":
                        logger.info("Detected speech started.")
                        # await self.reset_silence_timer()

                    case "conversation.item.input_audio_transcription.completed":
                        transcript = message.transcript.lower()
                        user_message = f"User: {transcript}"
                        self.conversation.append(Message(role=Role.USER, message=user_message))
                        logger.info(user_message)
                        # await self.reset_silence_timer()

                    case "response.audio_transcript.done":
                        ai_message = f"AI: {message.transcript}"
                        self.conversation.append(Message(role=Role.AI, message=ai_message))
                        logger.info(ai_message)
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

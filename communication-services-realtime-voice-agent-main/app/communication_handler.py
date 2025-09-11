import asyncio
import json
import os
import uuid
from datetime import datetime
from enum import Enum
from typing import List

import httpx
from azure.communication.callautomation import CallAutomationClient, PhoneNumberIdentifier
from azure.core.credentials import AzureKeyCredential
from dotenv import load_dotenv
from fastapi import WebSocket
from fastapi.websockets import WebSocketState
from openai import AsyncAzureOpenAI
from pydantic import BaseModel, Field
from rtclient import (
    InputTextContentPart,
    ItemCreateMessage,
    RTLowLevelClient,
    ResponseCreateMessage,
    SessionUpdateMessage,
    ServerMessageType,
    UserMessageItem,
    InputAudioBufferAppendMessage, FunctionCallOutputItem, )

from app.business_context import BusinessContextManager

load_dotenv()

import logging

logger = logging.getLogger(__name__)


class Role(str, Enum):
    AI = "ai"
    USER = "user"


class Message(BaseModel):
    role: Role
    message: str


async def stop_audio(websocket):
    if websocket.open:
        data = {
            "Kind": "StopAudio",
            "AudioData": None,
            "StopAudio": {}
        }
        # Serialize the server streaming data
        serialized_data = json.dumps(data)
        print(f"Out Streaming Data ---> {serialized_data}")
        # Send the chunk over the WebSocket
        await websocket.send(serialized_data)


class CommunicationHandler:
    voice_name = "echo"
    system_prompt = """
[ROLE AND GOAL]
You are a friendly AI assistant designed to take calls. Please assist the caller as best you can.
[TOOL USAGE]
1. `finish_conversation`  
   - **Description**: End the current conversation. Use this when the interaction has reached its natural conclusion.  
   - **When to use**:  
     • The user says goodbye or thanks and clearly ends the conversation.  
     • The order, booking, or request has been fully processed and confirmed.  
     • The call logically comes to an end and there is no further need to continue.  
   - **Example**: `finish_conversation(reason="User said goodbye and ended the call.")`  

2. `transfer_call`  
   - **Description**: Transfer the call to a human agent. Use this when the user requests direct communication with a person.  
   - **When to use**:  
     • The user asks to speak with a human, manager, or representative.  
     • The user explicitly asks for escalation or refuses to continue with the AI.  
   - **Important**: You must always provide a reason for the transfer.  
   - **Example**: `transfer_call(reason="User requested to speak with a human.")`  
    """
    SILENCE_TIMEOUT = 10
    silence_task: asyncio.Task | None = None

    def __init__(self, websocket: WebSocket, call_connection_id: str, acs_client: CallAutomationClient,
                 phone_number: str = None, customer_phone: str = None) -> None:
        self.rt_client = None
        self.active_websocket = websocket
        self.call_connection_id = call_connection_id
        self.acs_client = acs_client
        self.call_ended = False  # Prevent double hangup
        self.phone_number = phone_number
        self.customer_phone = customer_phone
        self.business_context = None
        self.tools = [
            {
                "type": "function",
                "name": "transfer_call",
                "description": (
                    "Redirect the call to a human agent or another department. "
                    "Use this function when the user explicitly requests to speak with a manager or a human, "
                    "or when the AI cannot provide sufficient information or does not know the answer."
                ),
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
                "name": "finish_conversation",
                "description": "Finish the conversation. Firstly say clode message - then call function. Used when the conversation is finished — for example, when the user says goodbye, completes an order, or the call reaches its natural end. This function is used to hang up the call.",
                "parameters": {},
            }
        ]
        self.ai_speaking = False
        self.finish_requested = False

        self.order_text = ""
        self.order_submitted = False
        self.closed_request_id = ""
        self.conversation: List[Message] = []

        self.start_time: datetime | None = None
        self.end_time: datetime | None = None

    async def handle_tools(self, previous_item_id: str, call_id: str, tool_name: str, arguments: dict):
        if tool_name == "transfer_call":
            reason = arguments.get("reason", "User requested transfer")
            logger.info(f"Tool request: transfer_call, reason: {reason}")
            result = await self.transfer_call_to_agent(self.business_context.human_phone)

            if not result:
                await self.rt_client.send(
                    ItemCreateMessage(
                        item=FunctionCallOutputItem(
                            call_id=call_id,
                            output="An error occurred while transferring the call to the human agent.",
                        ),
                        previous_item_id=previous_item_id,
                    )
                )
        elif tool_name == "finish_conversation":
            logger.info(f"Tool request: finish_conversation")
            # await self.finish_conversation()
            self.finish_requested = True
        else:
            logger.warning(f"Unknown tool request: {tool_name}")

    async def finish_conversation(self):
        logger.info("finish_conversation process...")
        if not self.call_ended:
            if self.ai_speaking:
                logger.info("AI is still speaking, delaying hangup...")
                return

            self.finish_requested = True
            self.end_time = datetime.utcnow()
            duration = (self.end_time - self.start_time).total_seconds() if self.start_time else 0
            logger.info(f"Call {self.call_connection_id} duration: {duration:.1f} seconds")
            try:
                call_connection = self.acs_client.get_call_connection(self.call_connection_id)
                call_connection.hang_up(is_for_everyone=True)
                logger.info(f"Call {self.call_connection_id} ended.")
            except Exception as e:
                logger.error(f"Failed to hang up call {self.call_connection_id}: {e}")

            if self.business_context and self.business_context.is_open:
                parsed_order = await self.gpt_parse_order()

                url = f"{os.getenv("AITELL_SERVER_URI")}/orders"
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
                    "status": parsed_order.get("status", "unhandled")
                }

                logger.info(payload)

                async with httpx.AsyncClient() as client:
                    response = await client.post(url, json=payload, headers=headers)
                    logger.info(f"Order endpoint status code: {response.status_code}")
                    print(response.text)

                self.call_ended = True

    async def transfer_call_to_agent(self, agent_phone_number: str) -> bool:
        try:
            if self.call_ended:
                logger.warning("Cannot transfer ended call")
                return False

            logger.info(f"Transferring call {self.call_connection_id} to agent {agent_phone_number}")

            target_participant = PhoneNumberIdentifier(agent_phone_number)

            call_connection = self.acs_client.get_call_connection(self.call_connection_id)

            transfer_result = call_connection.transfer_call_to_participant(
                target_participant=target_participant
            )

            logger.info(f"Call transfer initiated successfully: {transfer_result}")

            self.call_ended = True
            if self.silence_task and not self.silence_task.done():
                self.silence_task.cancel()

            return True

        except Exception as e:
            logger.error(f"Failed to transfer call: {e}")
            return False

    async def initialize_business_context(self) -> None:
        """Initialize business context by phone number"""
        if self.phone_number:
            async with BusinessContextManager() as service:
                self.business_context, self.system_prompt = await service.get_context_and_prompt(
                    self.phone_number
                )
                logger.info(f"Initialized context for phone: {self.phone_number}")
                if self.business_context:
                    logger.info(f"Business: {self.business_context.name}")
                    logger.info(f"Is open: {self.business_context.is_open}")
        else:
            logger.warning("No phone number provided, using default prompt")

    async def start_conversation_async(self) -> None:
        self.start_time = datetime.utcnow()
        await self.initialize_business_context()

        self.rt_client = RTLowLevelClient(
            url=os.getenv("AZURE_OPENAI_REALTIME_ENDPOINT"),
            key_credential=AzureKeyCredential(os.getenv("AZURE_OPENAI_REALTIME_SERVICE_KEY")),
            azure_deployment=os.getenv("AZURE_OPENAI_REALTIME_DEPLOYMENT_MODEL_NAME"),
        )
        try:
            await self.rt_client.connect()
        except Exception as e:
            logger.error(f"Failed to connect to Azure OpenAI Realtime Service: {e}")
            raise e

        session_update_message = {
            "type": "session.update",
            "session": {
                "voice": self.voice_name,
                "instructions": self.system_prompt,
                "input_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": "whisper-1",
                },
                "turn_detection": {
                    "threshold": 0.3,
                    "silence_duration_ms": 300,
                    "prefix_padding_ms": 500,
                    "type": "server_vad",
                },
                "tools": self.tools,
                "tool_choice": "auto"
            },
        }

        session_update_payload = SessionUpdateMessage(**session_update_message)
        await self.rt_client.send(session_update_payload)

        self.conversation_call_id = str(uuid.uuid4())

        await self.rt_client.send(ResponseCreateMessage())

        asyncio.create_task(self.receive_messages_async())

    async def say_message(self, message: str):
        content_part = InputTextContentPart(
            text=message
        )
        initial_message = ItemCreateMessage(
            item=UserMessageItem(content=[content_part])
        )
        await self.rt_client.send(message=initial_message)

    async def send_message_async(self, message: str) -> None:
        try:
            if self.active_websocket.client_state == WebSocketState.CONNECTED:
                await self.active_websocket.send_text(message)
        except Exception as e:
            logger.error((f"Send Message - Failed to send message: {e}"))
            raise e

    async def reset_silence_timer(self):
        if self.silence_task and not self.silence_task.done():
            self.silence_task.cancel()

        self.silence_task = asyncio.create_task(self.silence_timeout_handler())

    async def silence_timeout_handler(self):
        try:
            await asyncio.sleep(self.SILENCE_TIMEOUT)
            logger.info(f"No user speech detected for {self.SILENCE_TIMEOUT} seconds. Hanging up.")
            await self.finish_conversation()
        except asyncio.CancelledError:
            pass

    async def gpt_parse_order(self) -> dict:
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
- source: use "web"
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
            result['customerPhone'] = self.customer_phone

            return result

        except Exception as e:
            logger.error(f"GPT Parse Order - Failed to parse order: {e}")
            raise e

    async def receive_messages_async(self) -> None:
        try:
            while not self.rt_client.closed:
                try:
                    message: ServerMessageType = await self.rt_client.recv()
                except ValueError as e:
                    logger.error(f"Failed to get message: {e}")
                    continue

                if message is None or self.rt_client.ws.closed:
                    continue

                match message.type:
                    case "input_audio_buffer.speech_started":
                        logger.info("Detected speech started.")
                        audio_data = {
                            "Kind": "StopAudio",
                            "AudioData": None,
                            "StopAudio": {}
                        }
                        await self.send_message_async(json.dumps(audio_data))
                        # await self.reset_silence_timer()
                    case "input_audio_buffer.speech_stopped":
                        logger.info("Detected speech started.")
                        # await self.reset_silence_timer()
                    case "conversation.item.input_audio_transcription.completed":
                        transcript = message.transcript.lower()
                        user_message = f"User: {transcript}"
                        self.order_text += user_message + " "
                        self.conversation.append(Message(role=Role.USER, message=user_message))
                        logger.info(user_message)
                        # await self.reset_silence_timer()

                    case "response.audio_transcript.done":
                        ai_message = f"AI: {message.transcript}"
                        self.order_text += ai_message + " "
                        self.conversation.append(Message(role=Role.AI, message=ai_message))
                        logger.info(ai_message)
                        # await self.reset_silence_timer()

                    case "response.audio.delta":
                        self.ai_speaking = True
                        await self.receive_audio(message.delta)
                        # await self.reset_silence_timer()

                    case "response.function_call_arguments.done":
                        logger.info(f"Received tool call: {message.name}")
                        arguments = json.loads(message.arguments)
                        await self.handle_tools(message.item_id, message.call_id, message.name, arguments)

                    case "response.done":
                        self.ai_speaking = False

                        if self.finish_requested:
                            await asyncio.sleep(5)
                            await self.finish_conversation()
                        logger.info(
                            f"Response Done: {message.response.id}; Closed request id: {self.closed_request_id}")
                        # await self.reset_silence_timer()

                    case "error":
                        logger.error(f"Error: {message.error}")
                    case _:
                        pass
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
            logger.error(f"Error sending audio: {e}")

    async def send_audio_async(self, audio_data: str) -> None:
        await self.rt_client.send(
            message=InputAudioBufferAppendMessage(
                type="input_audio_buffer.append", audio=audio_data, _is_azure=True
            )
        )

    async def say_and_hang_up(self, message: str) -> None:
        if self.call_ended:
            return
        self.call_ended = True
        self.silence_task.cancel()

        content_part = InputTextContentPart(text=message)
        final_message = ItemCreateMessage(
            item=UserMessageItem(content=[content_part])
        )
        await self.rt_client.send(message=final_message)
        await self.rt_client.send(ResponseCreateMessage())

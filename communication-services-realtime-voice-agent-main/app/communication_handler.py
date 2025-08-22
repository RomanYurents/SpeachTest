import asyncio
import json
import os
import uuid
from typing import List

import httpx
from azure.communication.callautomation import CallAutomationClient
from azure.core.credentials import AzureKeyCredential
from dotenv import load_dotenv
from fastapi import WebSocket
from fastapi.websockets import WebSocketState
from openai import AsyncAzureOpenAI
from pydantic import BaseModel, Field
# from aiologger import Logger
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

from app.business_context import BusinessContextManager

load_dotenv()

# logger = Logger.with_default_handlers()

# Farewell detection list
FAREWELL_PHRASES = [
    "thanks, that's all",
    "thank you, bye",
    "bye",
    "goodbye",
    "see you",
    "talk to you later",
    "tack, det var allt",
    "tack, hej då",
    "tack",
    "tack, adjö",
    "tack, hej då",
    "hej då",
    "adjö",
    "hej då",
    "det var allt",
    "vi ses",
    "vi hörs senare",
    "vi pratar senare"
]


class CommunicationHandler:
    order_text = ""
    order_submitted = False
    closed_request_id = ""
    voice_name = "shimmer"
    system_prompt = """
[ROLE AND GOAL]
You are a friendly AI assistant designed to take calls. Please assist the caller as best you can.
    """

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

    async def initialize_business_context(self) -> None:
        """Initialize business context by phone number"""
        if self.phone_number:
            async with BusinessContextManager() as service:
                self.business_context, self.system_prompt = await service.get_context_and_prompt(
                    self.phone_number
                )
                print(f"Initialized context for phone: {self.phone_number}")
                if self.business_context:
                    print(f"Business: {self.business_context.name}")
                    print(f"Is open: {self.business_context.is_open}")
        else:
            print("No phone number provided, using default prompt")

    async def start_conversation_async(self) -> None:
        await self.initialize_business_context()

        self.rt_client = RTLowLevelClient(
            url=os.getenv("AZURE_OPENAI_REALTIME_ENDPOINT"),
            key_credential=AzureKeyCredential(os.getenv("AZURE_OPENAI_REALTIME_SERVICE_KEY")),
            azure_deployment=os.getenv("AZURE_OPENAI_REALTIME_DEPLOYMENT_MODEL_NAME"),
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
                "input_audio_transcription": {
                    "model": "whisper-1",
                    # "language": "en"
                },
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
            item=UserMessageItem(content=[content_part])
        )
        await self.rt_client.send(message=initial_message)
        await self.rt_client.send(ResponseCreateMessage())

        asyncio.create_task(self.receive_messages_async())

    async def send_message_async(self, message: str) -> None:
        try:
            if self.active_websocket.client_state == WebSocketState.CONNECTED:
                await self.active_websocket.send_text(message)
        except Exception as e:
            # logger.error(f"Send Message - Failed to send message: {e}")
            print((f"Send Message - Failed to send message: {e}"))
            raise e

    async def gpt_parse_order(self) -> object:
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
                                "text": f"""You are a smart assistant that extract order data from User-AI phone conversation text.
Return your answer as a JSON object with the following fields:
- customerName: the client's name from the conversation
- customerEmail: use the fixed value "customer@gmail.com"
- customerAddress: the client's address from the conversation
- orderItems: list of ordered dishes, each containing:
   • name: dish name (e.g., Classic Caesar Salad)
   • quantity: number of portions
   • unitPrice: price of one portion (decimal, 2 digits after the comma)
   • totalPrice: total price for this item (decimal, 2 digits after the comma)
   • category: either "Salat" or "Main dish"
   • notes: leave as empty string
- totalAmount: sum of all ordered items, decimal with 2 digits after the comma
- currency: use "USD"
- specialInstructions: leave as empty string
- estimatedCompletionTime: leave as empty string
- paymentMethod: use "card"
- source: use "web"
- status: use "confirmed"

Here are available services with prices {self.business_context.services}
                    """
                            }
                        ]
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Conversation User-AI text: {self.order_text}"
                        }
                    ]
                }
            ]

            messages = chat_prompt

            class OrderItem(BaseModel):
                name: str = Field(..., description="The name of dish from menu (e.g., Classic Caesar Salad)")
                quantity: int = Field(..., description="Amount of portions from conversation")
                unitPrice: float = Field(..., description="The price of dish from menu, always 2 digits after comma")
                totalPrice: float = Field(..., description="The total price for this item, always 2 digits after comma")
                category: str = Field(..., description="'Salat' or 'Main dish'")
                notes: str = Field(..., description="Leave this field empty string")

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

            # Generate the completion
            completion = await client.chat.completions.parse(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=16384,
                temperature=0,
                top_p=0.95,
                frequency_penalty=0,  # punishment for repetition
                presence_penalty=0,  # punishment for theme
                stop=None,
                response_format=OrderData
            )

            # for update in completion:
            #     if update.choices:
            #         print(update.choices[0].delta.content or "", end="")

            await client.close()

            result = json.loads(completion.choices[0].message.content)
            result['businessId'] = str(self.business_context.id)
            result['customerPhone'] = self.customer_phone

            return result

        except Exception as e:
            # logger.error(f"GPT Parse Order - Failed to parse order: {e}")
            print(f"GPT Parse Order - Failed to parse order: {e}")
            raise e

    async def receive_messages_async(self) -> None:
        try:
            while not self.rt_client.closed:
                try:
                    message: ServerMessageType = await self.rt_client.recv()
                except ValueError as e:
                    print(f"Failed to get message: {e}")
                    continue

                if message is None or self.rt_client.ws.closed:
                    continue

                # print(f"Received message of type: {message.type}")

                match message.type:
                    case "input_audio_buffer.speech_started":
                        print("Detected speech started.")
                    case "input_audio_buffer.speech_stopped":
                        print("Detected speech started.")
                    case "conversation.item.input_audio_transcription.completed":
                        transcript = message.transcript.lower()
                        user_message = f"User: {transcript}"
                        self.order_text += user_message + " "
                        print(user_message)
                        await self.detect_farewell(transcript)

                    case "response.audio_transcript.done":
                        ai_message = f"AI: {message.transcript}"
                        self.order_text += ai_message + " "
                        print(ai_message)

                    case "response.audio.delta":
                        await self.receive_audio(message.delta)

                    case "response.done":
                        print(f"Response Done: {message.response.id}; Closed request id: {self.closed_request_id}")
                        # If we've marked the call for end, now send ResponseCreateMessage and hang up
                        if self.call_ended:
                            # logger.info(self.order_text)
                            # print(self.order_text)
                            # await asyncio.sleep(1)  # Give it a moment to flush the audio
                            # await self.rt_client.send(ResponseCreateMessage())

                            try:
                                call_connection = self.acs_client.get_call_connection(self.call_connection_id)
                                self.call_ended = False
                                call_connection.hang_up(is_for_everyone=True)
                                # logger.info(f"Call {self.call_connection_id} ended.")
                                print(f"Call {self.call_connection_id} ended.")
                            except Exception as e:
                                # logger.error(f"Failed to hang up call {self.call_connection_id}: {e}")
                                print(f"Failed to hang up call {self.call_connection_id}: {e}")

                            # Give the user some time to hear it
                            if self.business_context and self.business_context.is_open:
                                parsed_order = await self.gpt_parse_order()

                                # parsed_order_str = json.dumps(parsed_order, indent=2)

                                # if self.order_submitted != True:
                                #     url = "https://app-aitell-test-hdc4e0bmb4a7fcd3.swedencentral-01.azurewebsites.net/orders"
                                #     headers = {
                                #         "x-api-key": "ac7d13c4-db2c-4bf0-87bb-205e03b34ea6",
                                #         "Content-Type": "application/json"
                                #     }

                                #     response = requests.post(url, json=parsed_order, headers=headers)

                                #     print("Order endpoint status code:", response.status_code)

                                #     self.order_submitted = True

                                url = f"{os.getenv("AITELL_SERVER_URI")}/orders"
                                headers = {
                                    "x-api-key": os.getenv("AITELL_SERVER_API_KEY"),
                                    "Content-Type": "application/json"
                                }

                                async with httpx.AsyncClient() as client:
                                    response = await client.post(url, json=parsed_order, headers=headers)
                                    print("Order endpoint status code:", response.status_code)

                            # self.order_submitted = True
                    case "error":
                        print(f"Error: {message.error}")
        except Exception as e:
            # logger.error(f"Error in receive_messages_async: {e}")
            print(f"Error in receive_messages_async: {e}")
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
            print("FAREWELL detected")
            await self.say_and_hang_up("Thank you for calling. Goodbye!")

    async def say_and_hang_up(self, message: str) -> None:
        if self.call_ended:
            return
        self.call_ended = True

        # Send final goodbye message
        content_part = InputTextContentPart(text=message)
        final_message = ItemCreateMessage(
            item=UserMessageItem(content=[content_part])
        )
        await self.rt_client.send(message=final_message)

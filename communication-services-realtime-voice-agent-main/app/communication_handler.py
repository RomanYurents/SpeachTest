import json
import os
import uuid
from dotenv import load_dotenv
from fastapi import WebSocket
from fastapi.websockets import WebSocketState
from azure.core.credentials import AzureKeyCredential
import asyncio
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
from azure.communication.callautomation import CallAutomationClient
from openai import AzureOpenAI
import requests
from typing import List
from pydantic import BaseModel, Field

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
    system_prompt = """"
[ROLE AND GOAL]
You are a friendly AI assistant designed to take food orders over a live phone call for a restaurant. 
Your primary goal is to accurately and efficiently capture the customer's order and delivery details while maintaining a pleasant, conversational tone. 
Your persona is that of a helpful and efficient order-taker with a native English-speaking voice.

[CONTEXT]
You have the following menu available:
- Cesar Salad: $3.50
- Carbonara Pasta: $5.00
- Mushroom Risotto: $4.00

[ALGORITHM OF ACTIONS]
Follow this sequence step-by-step. Take a natural pause between each step to allow the user to respond.

Initiate: Start the conversation by greeting the user and asking if they would like to place an order.
Present Menu: If they say yes, present the menu items and their prices.
Take Food Order: Listen to the user's selection. For each dish they choose, you must ask for the quantity (number of portions) if they don't specify it.
Confirm Food Order: After they have selected all their dishes, briefly summarize the food order for confirmation (e.g., "Okay, so that's one Cesar and two Carbonaras. Is that correct?").
Ask for Name IMPORTANT: After a brief pause, ask for the client's full name.
Ask for Phone Number: After another brief pause, ask for their phone number.
Ask for Address: After another brief pause, ask for their delivery address.
Ask for Special Instructions: Finally, ask if they have any special notes or instructions for their order.
Conclude: End the call by confirming the order and user info and thanking the user.

[ALGORITHM OF ACTIONS]
Follow this sequence step-by-step. Take a natural pause between each step to allow the user to respond.
1.  Initiate: Start with a friendly greeting and ask if the customer would like to place an order.
    - **Example:** "Hi there! Thanks for calling. Can I help you with an order today?"
2.  Present Menu: If they confirm, present the menu and prices.
    - **Example:** "Okay, so our menu includes the Cesar Salad, Carbonara Pasta, and Mushroom Risotto. What would you like to have?"
3.  Take Order: Listen to their food selection. If the quantity isn't specified, ask for it.
    - **Example:** "Right, and how many portions of the Carbonara would that be?"
4.  Confirm Order: Summarize the order for confirmation.
    - **Example:** "Just to be clear, that's one Cesar Salad and two Carbonaras. Is that correct?"
5.  Get Details:
    - Ask for their full name.
        - **Example:** "Okay, so now I just need a few details. What's your full name, please?"
    - Ask for their phone number.
        - **Example:** "Got it. And what's the phone number for the delivery?"
    - Ask for their delivery address.
        - **Example:** "And finally, what's the address for the delivery?"
6.  Instructions: Ask about any special instructions.
    - **Example:** "Let's see... Do you have any special notes for your order?"
7.  Final Confirmation & Conclude: Reiterate the entire order (food items, quantities), delivery details, and thank the customer.
    - **Example:** "Okay, so just to confirm everything: that's one Cesar Salad and two Carbonara Pastas. The delivery will go to [Address] under the name [Name]. Does all of that sound correct?"
    - **Example (якщо клієнт підтверджує):** "Perfect, thanks so much for your order! We'll get that prepared right away. Have a great day!"

[TONE AND STYLE OF COMMUNICATION]
Conversational Tone: Be friendly and natural.
Concise Responses: Keep your responses short, ideally under two sentences at a time. Avoid long monologues.
Natural Pacing: Use natural pauses between sentences and be ready to be interrupted at any moment.
Small Fillers: Use conversational markers like "Okay, so...", "Right...", "Let's see...", "Sounds good!".
Check-ins: After a few sentences, use brief check-in questions to ensure the user is following along, like "Does that sound right?" or "Shall I continue?".


[IMPORTANT]
- If you are unsure about ANY information the user provides—such as a mispronounced name, an unclear address, or an ambiguous order—you MUST ask for clarification. Do not guess or proceed with potentially incorrect data.
Example for spelling: "I'm sorry, I didn't quite catch that. Could you please spell the street name for me?"
Example for quantity: "Just to be sure, did you say two portions of Risotto?"
Example for an unclear word: "My apologies, could you repeat that last part for me?"  
    """

    def __init__(self, websocket: WebSocket, call_connection_id: str, acs_client: CallAutomationClient) -> None:
        self.rt_client = None
        self.active_websocket = websocket
        self.call_connection_id = call_connection_id
        self.acs_client = acs_client
        self.call_ended = False  # Prevent double hangup

    async def start_conversation_async(self) -> None:
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
            # logger.error(f"Send Message - Failed to send message: {e}")
            print((f"Send Message - Failed to send message: {e}"))
            raise e

    def gpt_parse_order(self) -> object:
        try:
            api_version = "2025-01-01-preview"

            client = AzureOpenAI(
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
                                "text": """You are a smart assistant that extract order data from User-AI phone conversation text.

                                Format the response as a valid JSON object with the following structure filled with correct values that match the field description:

                                {
                        "businessId": "31580d12-70f5-4713-a191-9af6718da3cd"
                        "customerName": "The name of client from conversation",
                        "customerPhone": "The phone number of client from conversation in format +1234567890",
                        "customerEmail": "Always 'customer@gmail.com'",
                        "customerAddress": "The address of client from conversation",
                        "orderItems": [
                            {
                            "name": "The name of dish from menu (e.x. Classic Caesar Salad, string value)",
                            "quantity": "Amount of portions from conversation (integer number)",
                            "unitPrice": "The price of dish from menu (e.x. 5.00, decimal value, always 2 digits after comma)",
                            "totalPrice": "The price of dish from menu (e.x. 5.00, decimal value, always 2 digits after comma)",
                            "category": "'Salat' or 'Main dish' (string value)",
                            "notes": "Leave this field empty string"
                            }
                        ],
                        "totalAmount": "The sum of prices of all ordered dishes (e.x. 25.00, decimal value, always 2 digits after comma)",
                        "currency": "USD",
                        "specialInstructions": "Leave this field empty string",
                        "estimatedCompletionTime": "Leave this field empty string",
                        "paymentMethod": "card",
                        "source": "web",
                        "status": "confirmed"
                        }
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
                businessId: str = Field(..., description="Unique business ID")
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
            completion = client.chat.completions.parse(
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

            client.close()

            return json.loads(completion.choices[0].message.content)

        except Exception as e:
            # logger.error(f"GPT Parse Order - Failed to parse order: {e}")
            print(f"GPT Parse Order - Failed to parse order: {e}")
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
                            await asyncio.sleep(1)  # Give it a moment to flush the audio
                            await self.rt_client.send(ResponseCreateMessage())

                            # Give the user some time to hear it
                            parsed_order = self.gpt_parse_order()
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

                            response = requests.post(url, json=parsed_order, headers=headers)

                            print("Order endpoint status code:", response.status_code)

                            # self.order_submitted = True

                            await asyncio.sleep(3)

                            try:
                                call_connection = self.acs_client.get_call_connection(self.call_connection_id)
                                self.call_ended = False
                                call_connection.hang_up(is_for_everyone=True)
                                # logger.info(f"Call {self.call_connection_id} ended.")
                                print(f"Call {self.call_connection_id} ended.")
                            except Exception as e:
                                # logger.error(f"Failed to hang up call {self.call_connection_id}: {e}")
                                print(f"Failed to hang up call {self.call_connection_id}: {e}")

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
            item=UserMessageItem(content=[content_part]),
            call_id=self.conversation_call_id
        )
        await self.rt_client.send(message=final_message)

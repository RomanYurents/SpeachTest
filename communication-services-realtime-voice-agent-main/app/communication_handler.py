import json
import os
import uuid
from dotenv import load_dotenv
from fastapi import WebSocket
from fastapi.websockets import WebSocketState
from azure.core.credentials import AzureKeyCredential
import asyncio
#from aiologger import Logger
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

load_dotenv()

#logger = Logger.with_default_handlers()

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
    system_prompt = (
        "*** Menu: Cesar $3.5, Carbonara $5, Risotto $4 *** You are an AI assistant that takes menu order from client by phone. You need to ask user if he want to order something. If yes you should tell about menu and wait for user order. Then ask for amount of portions if needed. Take pause. After user select dishes and quantity of portions for each dish you need to ask about full name of client. Take pause. Ask about clients phone number. Take pause. Ask about clients address. Take pause. Then you should ask if user has any specific notes or instructions. Answer user questions clearly and helpfully. You can ask additional questions if you see client spelling or data seems not correct (e.x. bad user pronounciation or voice that cause mistakes.) Keep responses concise. you should use short sentences. Use quick check-ins like “Does that make sense?” or “Shall I keep going?” every few sentences. Use Small fillers like “Okay, so…”, “Right…”, “Let’s see…”. You are a friendly AI assistant speaking live over a call. Keep responses under two sentences at a time. Use a conversational tone and natural pauses. Ask brief check-in questions to confirm understanding. Avoid long monologues — instead, speak as if you are ready to be interrupted at any moment. Your voice should be English native voice !"
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
            #logger.error(f"Send Message - Failed to send message: {e}")
            print((f"Send Message - Failed to send message: {e}"))
            raise e
        
    def gpt_parse_order(self) -> object:
        try:
            api_version = "2025-01-01-preview"

            client = AzureOpenAI(
                api_version=api_version,
                azure_endpoint="https://aiftestroman1.openai.azure.com/openai/deployments/gpt-4o-mini/chat/completions?api-version=2025-01-01-preview",
                api_key="1kjOqx7DUuB3TdDBcN1jlHL4PDARSXQHnuVmW0JefylAChtNiuBAJQQJ99BGACYeBjFXJ3w3AAAAACOGu2hd",
            )

            chat_prompt =[
                {
                    "role": "system",
                    "content":
                    [
                        {
                            "type": "text",
                            "text": """You are a smart assistant that extract order data from User-AI phone conversation text.

                                Format the response as a valid JSON object with the following structure filled with correct values that match the field description. Return ONLY the JSON object WITHOUT code blocks, backticks, or markdown formatting:

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

            # Generate the completion
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=16384,
                temperature=0,
                top_p=0.95,
                frequency_penalty=0, # punishment for repetition
                presence_penalty=0, # punishment for theme
                stop=None,
                stream=False # False - if we won't use chat
            )

            # for update in completion:
            #     if update.choices:
            #         print(update.choices[0].delta.content or "", end="")

            client.close()

            return json.loads(completion.choices[0].message.content)
        
        except Exception as e:
            #logger.error(f"GPT Parse Order - Failed to parse order: {e}")
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
                            parsed_order_str = json.dumps(parsed_order, indent=2)

                            # if self.order_submitted != True:
                            #     url = "https://app-aitell-test-hdc4e0bmb4a7fcd3.swedencentral-01.azurewebsites.net/orders"
                            #     headers = {
                            #         "x-api-key": "ac7d13c4-db2c-4bf0-87bb-205e03b34ea6",
                            #         "Content-Type": "application/json"
                            #     }

                            #     response = requests.post(url, json=parsed_order, headers=headers)

                            #     print("Order endpoint status code:", response.status_code)

                            #     self.order_submitted = True

                            url = "https://app-aitell-test-hdc4e0bmb4a7fcd3.swedencentral-01.azurewebsites.net/orders"
                            headers = {
                                "x-api-key": "ac7d13c4-db2c-4bf0-87bb-205e03b34ea6",
                                "Content-Type": "application/json"
                            }

                            response = requests.post(url, json=parsed_order, headers=headers)

                            print("Order endpoint status code:", response.status_code)

                            #self.order_submitted = True


                            await asyncio.sleep(3)

                            try:
                                call_connection = self.acs_client.get_call_connection(self.call_connection_id)
                                self.call_ended = False
                                await call_connection.hang_up(is_for_everyone=True)
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

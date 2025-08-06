import os
import uuid
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import JSONResponse
from azure.eventgrid import EventGridEvent, SystemEventNames
from azure.communication.callautomation import (
    MediaStreamingOptions,
    AudioFormat,
    MediaStreamingTransportType,
    MediaStreamingContentType,
    MediaStreamingAudioChannelType,
    CallAutomationClient,
)
from app.communication_handler import CommunicationHandler
from loguru import logger
from urllib.parse import urlencode, urlparse, urlunparse

app = FastAPI()

# ACS setup
ACS_CONNECTION_STRING = "endpoint=https://cs-sellifyai-dev.europe.communication.azure.com/;accesskey=9RpBnLiy3JN7ea20RLMbUbTmaVErTsB1OdLFB8SJYJAhzwnMGWDXJQQJ99BGACULyCp643waAAAAAZCSTZ1t"
acs_ca_client = CallAutomationClient.from_connection_string(ACS_CONNECTION_STRING)

CALLBACK_URI_HOST = "https://899fcc741de4.ngrok-free.app"
CALLBACK_EVENTS_URI = CALLBACK_URI_HOST + "/api/callbacks"

# Dictionary to map contextId to callConnectionId
context_to_call_id = {}

@app.get("/")
async def root():
    return JSONResponse({"message": "AI Voice Assistant is running."})

@app.post("/api/incomingCall")
async def incoming_call_handler(request: Request):
    for event_dict in await request.json():
        event = EventGridEvent.from_dict(event_dict)

        if event.event_type == SystemEventNames.EventGridSubscriptionValidationEventName:
            validation_code = event.data["validationCode"]
            return JSONResponse({"validationResponse": validation_code})

        elif event.event_type == "Microsoft.Communication.IncomingCall":
            caller_id = event.data["from"]["phoneNumber"]["value"] if event.data["from"]["kind"] == "phoneNumber" else event.data["from"]["rawId"]
            incoming_call_context = event.data["incomingCallContext"]
            guid = str(uuid.uuid4())

            query_params = urlencode({"callerId": caller_id, "contextId": guid})
            callback_uri = f"{CALLBACK_EVENTS_URI}/{guid}?{query_params}"
            parsed_url = urlparse(CALLBACK_URI_HOST)
            websocket_url = urlunparse(("wss", parsed_url.netloc, "/ws", "", "", "")) + f"?contextId={guid}"


            media_options = MediaStreamingOptions(
                transport_url=websocket_url,
                transport_type=MediaStreamingTransportType.WEBSOCKET,
                content_type=MediaStreamingContentType.AUDIO,
                audio_channel_type=MediaStreamingAudioChannelType.MIXED,
                start_media_streaming=True,
                enable_bidirectional=True,
                audio_format=AudioFormat.PCM24_K_MONO,
            )

            answer_result = acs_ca_client.answer_call(
                incoming_call_context=incoming_call_context,
                operation_context="incomingCall",
                callback_url=callback_uri,
                media_streaming=media_options,
            )

            logger.info(f"Answered call with ID: {answer_result.call_connection_id} for contextId: {guid}")
            # Store call_connection_id using contextId
            context_to_call_id[guid] = answer_result.call_connection_id

            return JSONResponse({"message": "Call answered."})

@app.post("/api/callbacks/{contextId}")
async def handle_callback_with_context(contextId: str, request: Request):
    for event in await request.json():
        event_data = event["data"]
        call_id = event_data["callConnectionId"]
        logger.info(f"Event: {event['type']}, Call ID: {call_id}")
        # Update context mapping if needed
        context_to_call_id[contextId] = call_id

@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()

    # Extract contextId from WebSocket query params
    context_id = websocket.query_params.get("contextId")
    call_id = context_to_call_id.get(context_id)

    service = CommunicationHandler(websocket, call_id, acs_ca_client)
    await service.start_conversation_async()

    while True:
        try:
            data = await websocket.receive_json()
            if data.get("kind") == "AudioData":
                audio_data = data["audioData"]["data"]
                await service.send_audio_async(audio_data)
        except Exception as e:
            logger.error(f"WebSocket closed: {e}")
            break

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
CALLBACK_URI_HOST = "https://e5a1f2b419ab.ngrok-free.app"
CALLBACK_EVENTS_URI = CALLBACK_URI_HOST + "/api/callbacks"

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
            guid = uuid.uuid4()

            query_params = urlencode({"callerId": caller_id})
            callback_uri = f"{CALLBACK_EVENTS_URI}/{guid}?{query_params}"
            parsed_url = urlparse(CALLBACK_EVENTS_URI)
            websocket_url = urlunparse(("wss", parsed_url.netloc, "/ws", "", "", ""))

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

            logger.info(f"Answered call with ID: {answer_result.call_connection_id}")
            return JSONResponse({"message": "Call answered."})

# Minimal callback logging
@app.post("/api/callbacks/{contextId}")
async def handle_callback_with_context(contextId: str, request: Request):
    for event in await request.json():
        event_data = event["data"]
        logger.info(f"Event: {event['type']}, Call ID: {event_data['callConnectionId']}")

# WebSocket route for ACS audio streaming + AI
@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    service = CommunicationHandler(websocket)
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

















# from multiprocessing import connection
# import os
# # import logging
# import uuid
# from urllib.parse import urlencode, urlparse, urlunparse
# from dotenv import load_dotenv
# from typing import List, Dict, Any, Optional
# # from fastapi.logger import logger
# from loguru import logger
# from fastapi import (
#     Body,
#     FastAPI,
#     Form,
#     WebSocket,
#     HTTPException,
#     Request,
#     status,
#     APIRouter,
#     Depends,
# )
# from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse
# from azure.eventgrid import EventGridEvent, SystemEventNames
# from azure.communication.callautomation import (
#     MediaStreamingOptions,
#     AudioFormat,
#     MediaStreamingTransportType,
#     MediaStreamingContentType,
#     MediaStreamingAudioChannelType,
#     CallAutomationClient,
#     CallConnectionClient,
#     PhoneNumberIdentifier,
#     RecognizeInputType,
#     MicrosoftTeamsUserIdentifier,
#     CallInvite,
#     RecognitionChoice,
#     DtmfTone,
#     TextSource,
# )


# from app.communication_handler import CommunicationHandler



# load_dotenv()

# app = FastAPI()
# BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ACS_CONNECTION_STRING = os.getenv("ACS_CONNECTION_STRING")
# acs_ca_client = CallAutomationClient.from_connection_string(ACS_CONNECTION_STRING)

# # Callback events URI to handle callback events.
# CALLBACK_URI_HOST = os.getenv("CALLBACK_URI_HOST")
# CALLBACK_EVENTS_URI = CALLBACK_URI_HOST + "/api/callbacks"



# @app.get("/")
# async def root():
#     return JSONResponse({"message": "Hello World!"})


# @app.post("/api/incomingCall")
# async def incoming_call_handler(request: Request):
#     logger.info("incoming event data")
#     for event_dict in await request.json():
#         event = EventGridEvent.from_dict(event_dict)
#         # logger.info("incoming event data --> %s", event.data)
#         if (
#             event.event_type
#             == SystemEventNames.EventGridSubscriptionValidationEventName
#         ):
#             logger.info("Validating subscription")
#             validation_code = event.data["validationCode"]
#             validation_response = {"validationResponse": validation_code}
#             logger.info(validation_response)
#             return JSONResponse(
#                 content=validation_response, status_code=status.HTTP_200_OK
#             )
#         elif event.event_type == "Microsoft.Communication.IncomingCall":
#             if event.data["from"]["kind"] == "phoneNumber":
#                 caller_id = event.data["from"]["phoneNumber"]["value"]
#             else:
#                 caller_id = event.data["from"]["rawId"]

#             incoming_call_context = event.data["incomingCallContext"]
#             guid = uuid.uuid4()

#             query_parameters = urlencode({"callerId": caller_id, "boo": "poop"})
#             callback_uri = f"{CALLBACK_EVENTS_URI}/{guid}?{query_parameters}"

#             parsed_url = urlparse(CALLBACK_EVENTS_URI)
#             websocket_url = urlunparse(("wss", parsed_url.netloc, "/ws", "", "", ""))

#             logger.info(f"callback url: {callback_uri}")
#             logger.info(f"websocket url: {websocket_url}")

#             try:
#                 # Answer the incoming call

#                 media_streaming_options = MediaStreamingOptions(
#                     transport_url=websocket_url,
#                     transport_type=MediaStreamingTransportType.WEBSOCKET,
#                     content_type=MediaStreamingContentType.AUDIO,
#                     audio_channel_type=MediaStreamingAudioChannelType.MIXED,
#                     start_media_streaming=True,
#                     enable_bidirectional=True,
#                     audio_format=AudioFormat.PCM24_K_MONO,
#                 )

#                 answer_call_result = acs_ca_client.answer_call(
#                     incoming_call_context=incoming_call_context,
#                     operation_context="incomingCall",
#                     callback_url=callback_uri,
#                     media_streaming=media_streaming_options,
#                 )

#             except Exception as e:
#                 raise e

#             logger.info(
#                 f"Answered call for connection id: {answer_call_result.call_connection_id}"
#             )


# @app.post("/api/callbacks/{contextId}")
# async def handle_callback_with_context(contextId: str, request: Request):
#     for event in await request.json():
#         # Parsing callback events
#         global call_connection_id
#         event_data = event["data"]
#         call_connection_id = event_data["callConnectionId"]
#         logger.info(
#             f"Received Event:-> {event['type']}, Correlation Id:-> {event_data['correlationId']}, CallConnectionId:-> {call_connection_id}"
#         )
#         if event["type"] == "Microsoft.Communication.CallConnected":
#             call_connection_properties = acs_ca_client.get_call_connection(
#                 call_connection_id
#             ).get_call_properties()
#             media_streaming_subscription = (
#                 call_connection_properties.media_streaming_subscription
#             )

#             logger.info(
#                 f"MediaStreamingSubscription:--> {media_streaming_subscription}"
#             )
#             logger.info(
#                 f"Received CallConnected event for connection id: {call_connection_id}"
#             )
#             logger.info(f"CORRELATION ID:--> { event_data['correlationId'] }")
#             logger.info(f"CALL CONNECTION ID:--> {event_data['callConnectionId']}")
#         elif event["type"] == "Microsoft.Communication.MediaStreamingStarted":
#             logger.info(
#                 f"Media streaming content type:--> {event_data['mediaStreamingUpdate']['contentType']}"
#             )
#             logger.info(
#                 f"Media streaming status:--> {event_data['mediaStreamingUpdate']['mediaStreamingStatus']}"
#             )
#             logger.info(
#                 f"Media streaming status details:--> {event_data['mediaStreamingUpdate']['mediaStreamingStatusDetails']}"
#             )
#         elif event["type"] == "Microsoft.Communication.MediaStreamingStopped":
#             logger.info(
#                 f"Media streaming content type:--> {event_data['mediaStreamingUpdate']['contentType']}"
#             )
#             logger.info(
#                 f"Media streaming status:--> {event_data['mediaStreamingUpdate']['mediaStreamingStatus']}"
#             )
#             logger.info(
#                 f"Media streaming status details:--> {event_data['mediaStreamingUpdate']['mediaStreamingStatusDetails']}"
#             )
#         elif event["type"] == "Microsoft.Communication.MediaStreamingFailed":
#             logger.info(
#                 f"Code:->{event_data['resultInformation']['code']}, Subcode:-> {event_data['resultInformation']['subCode']}"
#             )
#             logger.info(f"Message:->{event_data['resultInformation']['message']}")
#         elif event["type"] == "Microsoft.Communication.CallDisconnected":
#             pass


# # WebSocket
# @app.websocket("/ws")
# async def ws(websocket: WebSocket):
#     await websocket.accept()

#     service = CommunicationHandler(websocket)
#     await service.start_conversation_async()
    
#     while True:
#         try:
#             # Receive data from the client
#             data = await websocket.receive_json()
#             kind = data["kind"]
#             if kind == "AudioData":
#                 audio_data = data["audioData"]["data"]
#                 # Send the audio data to the CallAutomationHandler
#                 await service.send_audio_async(audio_data)
#         except Exception as e:
#             print(f"WebSocket connection closed: {e}")
#             break

import logging
import os
import uuid
from logging import getLogger
from urllib.parse import urlencode, urlparse, urlunparse

from azure.communication.callautomation import (
    MediaStreamingOptions,
    AudioFormat,
    MediaStreamingTransportType,
    MediaStreamingContentType,
    MediaStreamingAudioChannelType,
    CallAutomationClient,
)
from azure.eventgrid import EventGridEvent, SystemEventNames
from azure.monitor.opentelemetry import configure_azure_monitor
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import JSONResponse
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.trace import (
    get_tracer_provider,
)

from app.communication_handler import CommunicationHandler

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure.monitor.opentelemetry.exporter").setLevel(logging.WARNING)


if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    configure_azure_monitor()

tracer = trace.get_tracer(__name__,
                          tracer_provider=get_tracer_provider())

logger = logging.getLogger(__name__)

app = FastAPI()

FastAPIInstrumentor.instrument_app(app)

# ACS setup
acs_ca_client = CallAutomationClient.from_connection_string(os.getenv("ACS_CONNECTION_STRING"))

# Dictionary to map contextId to callConnectionId
context_to_call_id = {}
context_store = {}


@app.get("/")
async def root():
    return JSONResponse({"message": "AI Voice Assistant is running. Pre-demo version"})


@app.post("/api/incomingCall")
async def incoming_call_handler(request: Request):
    for event_dict in await request.json():
        event = EventGridEvent.from_dict(event_dict)

        if event.event_type == SystemEventNames.EventGridSubscriptionValidationEventName:
            validation_code = event.data["validationCode"]
            return JSONResponse({"validationResponse": validation_code})

        elif event.event_type == "Microsoft.Communication.IncomingCall":
            caller_id = event.data["from"]["phoneNumber"]["value"] if event.data["from"]["kind"] == "phoneNumber" else \
                event.data["from"]["rawId"]
            callee_id = event.data["to"]["phoneNumber"]["value"] if event.data["from"]["kind"] == "phoneNumber" else \
                event.data["from"]["rawId"]
            incoming_call_context = event.data["incomingCallContext"]
            guid = str(uuid.uuid4())

            logger.info(f"Call info: {caller_id} ➝ {callee_id}")

            query_params = urlencode({"callerId": caller_id, "contextId": guid})
            callback_uri = f"{os.getenv("CALLBACK_URI_HOST")}/api/callbacks/{guid}?{query_params}"
            parsed_url = urlparse(os.getenv("CALLBACK_URI_HOST"))
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
            context_store[guid] = {
                "call_connection_id": answer_result.call_connection_id,
                "caller_id": caller_id,
                "callee_id": callee_id,
            }

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
    call_info = context_store.get(context_id, {})

    caller_id = call_info.get("caller_id")
    callee_id = call_info.get("callee_id")
    call_id = call_info.get("call_connection_id")

    logger.info(f"Starting WS session for Call ID: {call_id}, Callee: {callee_id}")

    service = CommunicationHandler(websocket, call_id, acs_ca_client, callee_id, caller_id)
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


# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(
#         "app.main:app",
#         host="localhost",
#         port=8001,
#         reload=True
#     )

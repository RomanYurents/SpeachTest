import json
import logging
import os
import uuid
from typing import Dict
from urllib.parse import urlencode, urlparse, urlunparse

from azure.communication.callautomation import (
    MediaStreamingOptions,
    AudioFormat,
    MediaStreamingTransportType,
    MediaStreamingContentType,
    MediaStreamingAudioChannelType,
    CallAutomationClient,
    PhoneNumberIdentifier,
)
from azure.eventgrid import EventGridEvent, SystemEventNames
from azure.monitor.opentelemetry import configure_azure_monitor
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, Request, HTTPException
from fastapi.responses import JSONResponse, Response
from starlette.websockets import WebSocketDisconnect
from twilio.rest import Client

from app.factory.communication_handler_factory import CommunicationHandlerFactory, CommunicationProvider
from app.factory.unified_communication_handler import UnifiedConversationHandler

from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk._logs import LoggingHandler
from opentelemetry.trace import (
    get_tracer_provider,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    configure_azure_monitor()

tracer = trace.get_tracer(__name__,
                          tracer_provider=get_tracer_provider())

logger = logging.getLogger(__name__)

otel_handler = LoggingHandler(level=logging.INFO)
logger.addHandler(otel_handler)

app = FastAPI()

FastAPIInstrumentor.instrument_app(app)

# Azure Communication Services setup
acs_ca_client = CallAutomationClient.from_connection_string(os.getenv("ACS_CONNECTION_STRING"))

# Twilio setup
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) if TWILIO_ACCOUNT_SID else None

# Azure OpenAI Realtime configuration
AZURE_OPENAI_REALTIME_ENDPOINT = os.getenv("AZURE_OPENAI_REALTIME_ENDPOINT")
AZURE_OPENAI_REALTIME_SERVICE_KEY = os.getenv("AZURE_OPENAI_REALTIME_SERVICE_KEY")
AZURE_OPENAI_REALTIME_DEPLOYMENT_MODEL_NAME = os.getenv("AZURE_OPENAI_REALTIME_DEPLOYMENT_MODEL_NAME")

# Storage for context mapping
context_to_call_id = {}
context_store = {}
active_conversations: Dict[str, UnifiedConversationHandler] = {}


@app.get("/")
async def root():
    return JSONResponse({
        "message": "Unified AI Voice Assistant",
        "version": "2.0",
        "providers": {
            "azure_acs": bool(os.getenv("ACS_CONNECTION_STRING")),
            "twilio": bool(TWILIO_ACCOUNT_SID),
            "azure_openai": bool(AZURE_OPENAI_REALTIME_ENDPOINT)
        }
    })


# ==================== AZURE COMMUNICATION SERVICES ENDPOINTS ====================

@app.post("/api/incomingCall")
async def azure_incoming_call_handler(request: Request):
    """Handle incoming Azure Communication Services calls"""
    for event_dict in await request.json():
        event = EventGridEvent.from_dict(event_dict)

        if event.event_type == SystemEventNames.EventGridSubscriptionValidationEventName:
            validation_code = event.data["validationCode"]
            return JSONResponse({"validationResponse": validation_code})

        elif event.event_type == "Microsoft.Communication.IncomingCall":
            caller_id = event.data["from"]["phoneNumber"]["value"] if event.data["from"]["kind"] == "phoneNumber" else \
                event.data["from"]["rawId"]
            callee_id = event.data["to"]["phoneNumber"]["value"] if event.data["to"]["kind"] == "phoneNumber" else \
                event.data["to"]["rawId"]
            incoming_call_context = event.data["incomingCallContext"]
            guid = str(uuid.uuid4())

            logger.info(f"Azure incoming call: {caller_id} ➝ {callee_id}")

            query_params = urlencode({"callerId": caller_id, "contextId": guid})
            callback_uri = f"{os.getenv('CALLBACK_URI_HOST')}/api/callbacks/acs/{guid}?{query_params}"
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

            logger.info(f"Answered Azure call with ID: {answer_result.call_connection_id} for contextId: {guid}")

            # Store context information
            context_to_call_id[guid] = answer_result.call_connection_id
            context_store[guid] = {
                "call_connection_id": answer_result.call_connection_id,
                "caller_id": caller_id,
                "callee_id": callee_id,
                "provider": "azure"
            }

            return JSONResponse({"message": "Azure call answered."})

    return JSONResponse({"message": "No relevant events processed"})


@app.post("/api/callbacks/acs/{contextId}")
async def azure_handle_callback_with_context(contextId: str, request: Request):
    """Handle Azure Communication Services callbacks"""
    for event in await request.json():
        event_data = event["data"]
        call_id = event_data["callConnectionId"]
        logger.info(f"Azure callback - Event: {event['type']}, Call ID: {call_id}")

        # Update context mapping
        if contextId in context_store:
            context_to_call_id[contextId] = call_id
            context_store[contextId]["call_connection_id"] = call_id

    return JSONResponse({"status": "received"})


@app.post("/api/initiateCall")
async def azure_initiate_call_handler(request: Request):
    """Initiate outbound Azure Communication Services call"""
    body = await request.json()
    callee_number = body['number']
    acs_source_number = os.getenv("ACS_SOURCE_NUMBER")
    guid = str(uuid.uuid4())

    if not acs_source_number:
        logger.warning("ACS source number not configured")
        return JSONResponse({"error": "ACS source number not configured"}, status_code=500)

    callback_uri = f"{os.getenv('CALLBACK_URI_HOST')}/api/callbacks/{guid}?contextId={guid}"
    parsed_url = urlparse(os.getenv("CALLBACK_URI_HOST"))
    websocket_url = urlunparse(("wss", parsed_url.netloc, "/acs/ws", "", "", "")) + f"?contextId={guid}"

    media_options = MediaStreamingOptions(
        transport_url=websocket_url,
        transport_type=MediaStreamingTransportType.WEBSOCKET,
        content_type=MediaStreamingContentType.AUDIO,
        audio_channel_type=MediaStreamingAudioChannelType.MIXED,
        start_media_streaming=True,
        enable_bidirectional=True,
        audio_format=AudioFormat.PCM24_K_MONO,
    )

    answer_result = acs_ca_client.create_call(
        source_caller_id_number=PhoneNumberIdentifier(acs_source_number),
        target_participant=[PhoneNumberIdentifier(callee_number)],
        callback_url=callback_uri,
        media_streaming=media_options
    )

    logger.info(
        f"Initiated Azure outbound call {answer_result.call_connection_id} to {callee_number} with contextId {guid}")

    context_store[guid] = {
        "call_connection_id": answer_result.call_connection_id,
        "callee_id": acs_source_number,
        "caller_id": callee_number,
        "provider": "azure"
    }

    context_to_call_id[guid] = answer_result.call_connection_id

    return JSONResponse({"message": "Azure call initiated", "contextId": guid})


@app.websocket("/ws")
async def azure_websocket_handler(websocket: WebSocket):
    """Azure Communication Services WebSocket handler"""
    await websocket.accept()

    context_id = websocket.query_params.get("contextId")
    if not context_id or context_id not in context_store:
        logger.error(f"Invalid or missing contextId: {context_id}")
        await websocket.close(code=1000, reason="Invalid context")
        return

    call_info = context_store[context_id]
    caller_id = call_info.get("caller_id")
    callee_id = call_info.get("callee_id")
    call_connection_id = call_info.get("call_connection_id")

    logger.info(f"Starting Azure WS session for Call ID: {call_connection_id}")

    try:
        comm_handler = CommunicationHandlerFactory.create_handler(
            CommunicationProvider.AZURE,
            websocket=websocket,
            call_connection_id=call_connection_id,
            acs_client=acs_ca_client,
            phone_number=callee_id,
            customer_phone=caller_id
        )

        conversation_handler = UnifiedConversationHandler(comm_handler)
        active_conversations[call_connection_id] = conversation_handler

        await conversation_handler.start_conversation()

        while True:
            try:
                data = await websocket.receive_json()
                if data.get("kind") == "AudioData":
                    audio_data = data["audioData"]["data"]
                    await conversation_handler.send_audio_async(audio_data)
            except Exception as e:
                logger.error(f"Azure WebSocket error: {e}")
                break

    except Exception as e:
        logger.error(f"Error in Azure WebSocket handler: {e}")
    finally:
        if call_connection_id in active_conversations:
            del active_conversations[call_connection_id]
        if context_id in context_store:
            del context_store[context_id]


# ==================== TWILIO ENDPOINTS ====================

@app.api_route("/api/twilio/incomingCall", methods=["POST", "GET"])
async def twilio_receive_call(request: Request):
    """Handle incoming Twilio call"""
    if not twilio_client:
        raise HTTPException(status_code=500, detail="Twilio not configured")

    form = await request.form() if request.method == "POST" else request.query_params

    from_number = form.get("From")
    to_number = form.get("To")
    call_connection_id = form.get("CallSid")

    logger.info(f"Starting WS session for Call ID: {from_number}, Callee: {to_number}")

    context_store[call_connection_id] = {
        "call_connection_id": call_connection_id,
        "callee_id": to_number,
        "caller_id": from_number,
        "provider": "azure"
    }

    context_to_call_id[call_connection_id] = call_connection_id

    host = os.getenv('CALLBACK_URI_HOST').replace("https://", "")
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="wss://{host}/twilio/ws" />
    </Connect>
</Response>
"""
    return Response(content=twiml, media_type="text/xml")


@app.websocket("/twilio/ws")
async def twilio_media_stream_handler(websocket: WebSocket):
    """Handle Twilio media stream with unified architecture"""
    await websocket.accept()
    call_connection_id = None
    conversation_handler = None

    try:
        async for msg in websocket.iter_text():
            data = json.loads(msg)
            # print(data)

            if data.get("event") == "start":
                call_connection_id = data["start"]["callSid"]
                stream_sid = data["start"]["streamSid"]
                context = context_store[call_connection_id]
                caller_id = context["caller_id"]
                callee_id = context["callee_id"]

                logger.info(f"Twilio call started: {call_connection_id} from {caller_id} to {callee_id}")

                comm_handler = CommunicationHandlerFactory.create_handler(
                    CommunicationProvider.TWILIO,
                    stream_sid=stream_sid,
                    websocket=websocket,
                    phone_number=callee_id,
                    customer_phone=caller_id,
                    twilio_client=twilio_client,
                    call_sid=call_connection_id,
                )

                conversation_handler = UnifiedConversationHandler(comm_handler)
                active_conversations[call_connection_id] = conversation_handler

                await conversation_handler.start_conversation()

            elif data.get("event") == "media" and conversation_handler:
                audio_data = data["media"]["payload"]
                await conversation_handler.send_audio_async(audio_data)

            elif data.get("event") == "stop":
                logger.info(f"Twilio call stopped: {call_connection_id}")
                if call_connection_id in active_conversations:
                    await active_conversations[call_connection_id].comm_handler.end_call()
                    del active_conversations[call_connection_id]
                if call_connection_id in context_store:
                    del context_store[call_connection_id]
                break

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for call {call_connection_id}")
    except Exception as e:
        logger.error(f"Error in Twilio WS handler: {e}")
    finally:
        if call_connection_id and call_connection_id in active_conversations:
            del active_conversations[call_connection_id]
        if call_connection_id and call_connection_id in context_store:
            del context_store[call_connection_id]
        await websocket.close()


# ==================== MANAGEMENT ENDPOINTS ====================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return JSONResponse({
        "status": "healthy",
        "providers": {
            "azure_acs": bool(os.getenv("ACS_CONNECTION_STRING")),
            "twilio": bool(TWILIO_ACCOUNT_SID),
            "azure_openai_realtime": bool(AZURE_OPENAI_REALTIME_ENDPOINT)
        },
        "active_conversations": len(active_conversations),
        "active_contexts": len(context_store)
    })


@app.get("/conversations")
async def list_active_conversations():
    """List all active conversations"""
    conversations_info = {}
    for call_id, handler in active_conversations.items():
        provider = "unknown"
        if hasattr(handler.comm_handler, 'acs_client'):
            provider = "azure"
        elif hasattr(handler.comm_handler, 'twilio_client'):
            provider = "twilio"

        conversations_info[call_id] = {
            "provider": provider,
            "duration": await handler.comm_handler.get_call_duration(),
            "customer_phone": handler.comm_handler.customer_phone,
            "phone_number": handler.comm_handler.phone_number
        }

    return JSONResponse({
        "active_conversations": conversations_info,
        "count": len(active_conversations)
    })


@app.post("/conversations/{call_id}/end")
async def end_conversation(call_id: str):
    """Manually end a conversation"""
    if call_id in active_conversations:
        conversation = active_conversations[call_id]
        await conversation.comm_handler.end_call()
        return JSONResponse({"status": "ended", "call_id": call_id})
    else:
        raise HTTPException(status_code=404, detail="Conversation not found")


@app.post("/conversations/{call_id}/transfer")
async def transfer_conversation(call_id: str, request: Request):
    """Manually transfer a conversation"""
    body = await request.json()
    target_number = body.get("target_number")
    reason = body.get("reason", "Manual transfer")

    if not target_number:
        raise HTTPException(status_code=400, detail="target_number is required")

    if call_id in active_conversations:
        conversation = active_conversations[call_id]
        success = await conversation.comm_handler.transfer_call(target_number, reason)

        if success:
            return JSONResponse({
                "status": "transferred",
                "call_id": call_id,
                "target": target_number,
                "reason": reason
            })
        else:
            raise HTTPException(status_code=500, detail="Transfer failed")
    else:
        raise HTTPException(status_code=404, detail="Conversation not found")


# if __name__ == "__main__":
#     import uvicorn
#
#     uvicorn.run(
#         "app.main:app",
#         host="localhost",
#         port=int(os.getenv("PORT", 8001)),
#         reload=True
#     )

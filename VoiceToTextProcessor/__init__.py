import logging
import azure.functions as func
from twilio.twiml.voice_response import VoiceResponse
from download_and_transcribe import download_audio_and_transcribe, synthesize_speech_from_text_to_file
from utils import ask_openai, upload_to_blob
import urllib.parse
import os
import tempfile

def main(req: func.HttpRequest) -> func.HttpResponse:
    if req.route_params.get('action') == "voice":
        resp = VoiceResponse()
        resp.say("Hi! Ask your question after the beep.")
        resp.record(
            action="/api/process_recording",
            max_length=10,
            transcribe=False
        )
        return func.HttpResponse(str(resp), mimetype="application/xml")

    elif req.route_params.get('action') == "process_recording":
        recording_url = req.form.get("RecordingUrl")
        print(f"Downloading from: {recording_url}")

        # Step 1: Transcribe
        user_question = download_audio_and_transcribe(recording_url)
        print(f"User asked: {user_question}")

        # Step 2: Get LLM answer
        answer = ask_openai(user_question)
        print(f"LLM replied: {answer}")

        # Step 3: Generate MP3 using updated TTS method
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_mp3:
            response_mp3_path = tmp_mp3.name
        synthesize_speech_from_text_to_file(answer, response_mp3_path)

        # Step 4: Upload MP3 to blob
        blob_url = upload_to_blob(response_mp3_path)

        # Step 5: Return <Play> TwiML to Twilio
        resp = VoiceResponse()
        resp.play(blob_url)

        return func.HttpResponse(str(resp), mimetype="application/xml")

    return func.HttpResponse("Not Found", status_code=404)

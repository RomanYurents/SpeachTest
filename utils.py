# utils.py

import azure.cognitiveservices.speech as speechsdk
from openai import AzureOpenAI
import os
from dotenv import load_dotenv

def recognize_speech_from_mic():
    # Load environment variables
    load_dotenv()

    SPEECH_KEY = os.getenv("SPEECH_KEY")
    SPEECH_REGION = os.getenv("SPEECH_REGION")

    speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
    recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config)
    print("Listening...")

    result = recognizer.recognize_once()
    if result.reason == speechsdk.ResultReason.RecognizedSpeech:
        return result.text
    return "Speech not recognized."

def ask_openai(prompt):
    # Load environment variables
    load_dotenv()

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_DEPLOYMENT_NAME = os.getenv("OPENAI_DEPLOYMENT_NAME")
    OPENAI_API_URL = os.getenv("OPENAI_API_URL")
    OPENAI_API_VERSION = os.getenv("OPENAI_API_VERSION")

    # Azure OpenAI client
    client = AzureOpenAI(
        api_version=OPENAI_API_VERSION,
        azure_endpoint=OPENAI_API_URL,
        api_key=OPENAI_API_KEY
    )

    messages = [
            {"role": "system", "content": "You are a restaurant assistant. Now the restaurant menu includes 2 dishes: 'Carbonara' price '$4.5' and Risotto price '$4'"},
            {"role": "user", "content": prompt}
        ]

    response_chunks = []
    # Generate the completion
    completion = client.chat.completions.create(
        model=OPENAI_DEPLOYMENT_NAME,
        messages=messages,
        max_tokens=16000,
        temperature=0.5,
        top_p=0.95,
        frequency_penalty=0, # punishment for repetition
        presence_penalty=0, # punishment for theme
        stop=None,
        stream=True # False - if we won't use chat
    )

    for update in completion:
        if update.choices:
            chunk = update.choices[0].delta.content or ""
            response_chunks.append(chunk)

    response_str = "".join(response_chunks)

    return response_str

def synthesize_speech(text):
    # Load environment variables
    load_dotenv()

    SPEECH_KEY = os.getenv("SPEECH_KEY")
    SPEECH_REGION = os.getenv("SPEECH_REGION")

    speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
    audio_config = speechsdk.audio.AudioOutputConfig(use_default_speaker=True)

    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
    synthesizer.speak_text_async(text).get()

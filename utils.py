# utils.py

import azure.cognitiveservices.speech as speechsdk
from openai import AzureOpenAI
import os
from dotenv import load_dotenv

def recognize_speech_from_mic():
    # Load environment variables
    load_dotenv()

    SPEECH_KEY = 'EXsLZoIDExLz7kklm0qfo9cyCVrLfTgk4NZoVOVU2ySpUQZXDIjMJQQJ99BGACYeBjFXJ3w3AAAYACOGYGiP'
    SPEECH_REGION = 'eastus'

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

    OPENAI_API_KEY = '1kjOqx7DUuB3TdDBcN1jlHL4PDARSXQHnuVmW0JefylAChtNiuBAJQQJ99BGACYeBjFXJ3w3AAAAACOGu2hd'
    OPENAI_DEPLOYMENT_NAME = 'gpt-4o-mini'
    OPENAI_API_URL = 'https://aiftestroman1.openai.azure.com/openai/deployments/gpt-4o-mini/chat/completions?api-version=2025-01-01-preview'
    OPENAI_API_VERSION = '2025-01-01-preview'

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

    SPEECH_KEY = 'EXsLZoIDExLz7kklm0qfo9cyCVrLfTgk4NZoVOVU2ySpUQZXDIjMJQQJ99BGACYeBjFXJ3w3AAAYACOGYGiP'
    SPEECH_REGION = 'eastus'

    speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
    audio_config = speechsdk.audio.AudioOutputConfig(use_default_speaker=True)

    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
    synthesizer.speak_text_async(text).get()

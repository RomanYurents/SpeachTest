import os
from openai import AzureOpenAI
import azure.cognitiveservices.speech as speechsdk
from azure.storage.blob import BlobServiceClient, ContentSettings

# Load environment variables (optional)
from dotenv import load_dotenv
load_dotenv()

# Azure Keys
SPEECH_KEY = os.getenv("SPEECH_KEY")
SPEECH_REGION = os.getenv("SPEECH_REGION")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_DEPLOYMENT_NAME = os.getenv("OPENAI_DEPLOYMENT_NAME")
OPENAI_API_URL = os.getenv("OPENAI_API_URL")
OPENAI_API_VERSION = os.getenv("OPENAI_API_VERSION")

AZURE_BLOB_CONNECTION = os.getenv("AZURE_BLOB_CONNECTION")
AZURE_BLOB_CONTAINER = os.getenv("AZURE_BLOB_CONTAINER")


def ask_openai(prompt: str) -> str:
    client = AzureOpenAI(
        api_version=OPENAI_API_VERSION,
        azure_endpoint=OPENAI_API_URL,
        api_key=OPENAI_API_KEY
    )

    messages = [
        {
            "role": "system",
            "content": "You are a restaurant assistant. The menu includes 'Carbonara' ($4.5) and 'Risotto' ($4). Answer questions clearly."
        },
        {"role": "user", "content": prompt}
    ]

    response_chunks = []
    completion = client.chat.completions.create(
        model=OPENAI_DEPLOYMENT_NAME,
        messages=messages,
        max_tokens=1000,
        temperature=0.5,
        top_p=0.95,
        stream=True
    )

    for update in completion:
        if update.choices:
            chunk = update.choices[0].delta.content or ""
            response_chunks.append(chunk)

    return "".join(response_chunks)


def synthesize_speech_from_text_to_file(text: str, filename: str):
    speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
    audio_config = speechsdk.audio.AudioOutputConfig(filename=filename)

    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
    synthesizer.speak_text_async(text).get()


def upload_to_blob(file_path: str) -> str:
    blob_service_client = BlobServiceClient.from_connection_string(AZURE_BLOB_CONNECTION)
    blob_client = blob_service_client.get_blob_client(container=AZURE_BLOB_CONTAINER, blob="response.mp3")

    with open(file_path, "rb") as data:
        blob_client.upload_blob(data, overwrite=True, content_settings=ContentSettings(content_type='audio/mpeg'))

    blob_url = f"https://{blob_service_client.account_name}.blob.core.windows.net/{AZURE_BLOB_CONTAINER}/response.mp3"
    return blob_url

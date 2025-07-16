import requests
import os
import azure.cognitiveservices.speech as speechsdk
from dotenv import load_dotenv
import tempfile
from pydub.utils import which
from pydub import AudioSegment

# Load environment variables
load_dotenv()

SPEECH_KEY = os.getenv("SPEECH_KEY")
SPEECH_REGION = os.getenv("SPEECH_REGION")


def download_audio_and_transcribe(recording_url: str) -> str:
    import time
    from pydub import AudioSegment
    from pydub.utils import which

    time.sleep(2)  # give Twilio time to finalize recording

    if not recording_url.endswith(".mp3"):
        recording_url += ".mp3"

    auth = (os.getenv("TWILIO_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
    r = requests.get(recording_url, auth=auth)

    if r.status_code != 200:
        raise Exception(f"Failed to download audio: {r.status_code}, {r.text}")

    content_type = r.headers.get("Content-Type", "")
    if "audio" not in content_type:
        raise Exception(f"Expected audio content, got {content_type}")

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_mp3:
        tmp_mp3.write(r.content)
        mp3_path = tmp_mp3.name

    print("Downloaded audio to:", mp3_path)

    # Convert to wav
    wav_path = mp3_path.replace(".mp3", ".wav")
    AudioSegment.converter = which("ffmpeg")
    AudioSegment.ffprobe   = which("ffprobe")
    audio = AudioSegment.from_file(mp3_path)
    audio = audio.set_channels(1).set_frame_rate(16000)
    audio.export(wav_path, format="wav")

    # Transcribe
    speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
    audio_input = speechsdk.AudioConfig(filename=wav_path)
    recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_input)
    result = recognizer.recognize_once()

    if result.reason == speechsdk.ResultReason.RecognizedSpeech:
        return result.text
    else:
        return "Sorry, I couldn't understand the audio."


def synthesize_speech_from_text_to_file(text: str, output_mp3_path: str):
    """Convert text to speech, synthesize to WAV, then convert to MP3 for Twilio."""
    import tempfile
    from pydub import AudioSegment
    from pydub.utils import which

    # Set ffmpeg paths for pydub
    AudioSegment.converter = which("ffmpeg")
    AudioSegment.ffprobe = which("ffprobe")

    speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)

    # First synthesize to WAV
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
        wav_path = tmp_wav.name

    audio_config = speechsdk.audio.AudioConfig(filename=wav_path)
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)

    result = synthesizer.speak_text_async(text).get()

    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        raise RuntimeError(f"Speech synthesis failed: {result.reason}")

    print("TTS WAV path:", wav_path)

    # Convert WAV to MP3 with proper format
    audio = AudioSegment.from_wav(wav_path)
    audio.export(output_mp3_path, format="mp3", bitrate="128k")
    print("Final MP3 for Twilio:", output_mp3_path)

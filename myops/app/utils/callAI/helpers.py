
import os

import azure.cognitiveservices.speech as speechsdk

# TODO Add below creds in config/env
SPEECH_KEY = os.getenv("MYOPS_SPEECH_KEY", "")
SPEECH_REGION = "eastus2"

def create_speech_recognizer():
    speech_config = speechsdk.SpeechConfig(
        subscription=SPEECH_KEY, region=SPEECH_REGION
    )
    audio_format = speechsdk.audio.AudioStreamFormat(
        samples_per_second=16000, bits_per_sample=16, channels=1
    )
    stream = speechsdk.audio.PushAudioInputStream(audio_format)
    audio_input = speechsdk.audio.AudioConfig(stream=stream)
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config, audio_config=audio_input
    )
    return recognizer, stream

def recognize_from_microphone():
    speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
    # Enable conversation mode for turn detection
    audio_config = speechsdk.audio.AudioConfig(use_default_microphone=True)
    speech_recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)

    def recognizing_handler(evt):
        print(f"Recognizing: {evt.result.text}")

    def recognized_handler(evt):
        print(f"Recognized: {evt.result.text}")

    def session_started(evt):
        print("Session started.")

    def session_stopped(evt):
        print("Session stopped.")

    def speech_start(evt):
        print("Speech started (turn begin).")

    def speech_end(evt):
        print("Speech ended (turn end).")

    # Connect event handlers
    speech_recognizer.recognizing.connect(recognizing_handler)
    speech_recognizer.recognized.connect(recognized_handler)
    speech_recognizer.session_started.connect(session_started)
    speech_recognizer.session_stopped.connect(session_stopped)
    speech_recognizer.speech_start_detected.connect(speech_start)
    speech_recognizer.speech_end_detected.connect(speech_end)

    print("Speak into your microphone.")
    speech_recognizer.start_continuous_recognition()
    try:
        while True:
            pass  # Keep the program running
    except KeyboardInterrupt:
        speech_recognizer.stop_continuous_recognition()
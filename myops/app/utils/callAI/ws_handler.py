import os

import asyncio
from enum import Enum
import json
import threading
from fastapi import WebSocket
from .logger import logger
import azure.cognitiveservices.speech as speechsdk
from typing import Optional
import uuid
from xml.sax.saxutils import escape
from azure.identity import ClientSecretCredential
from app.utils.callAI.openai_foundry_client import AzureADTokenProvider, OpenAIFoundryClient

# TODO Add below creds in config/env
# SPEECH_KEY = os.getenv("MYOPS_SPEECH_KEY", "")
TENANT_ID = os.getenv("AZURE_TENANT_ID", "")
CLIENT_ID = os.getenv("MYOPS_WS_AZURE_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("MYOPS_WS_AZURE_CLIENT_SECRET", "")
# Speech resource info
SPEECH_ENDPOINT = "https://callagentbot.cognitiveservices.azure.com/"
SPEECH_REGION = "eastus2"
OPENAI_ENDPOINT = "https://call-responder-bot-resource.openai.azure.com/"
DEPLOYMENT_NAME = "Call_responder_bot"


def get_speech_config():
    # Get AAD token
    cred = ClientSecretCredential(TENANT_ID, CLIENT_ID, CLIENT_SECRET)
    token = cred.get_token("https://cognitiveservices.azure.com/.default").token

    # Create config using endpoint
    cfg = speechsdk.SpeechConfig(endpoint=SPEECH_ENDPOINT)
    cfg.authorization_token = token  # set token AFTER creation
    return cfg

class SOCKET_EVENTS(Enum):
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    RECOGNIZING = "recognizing"
    RECOGNIZED = "recognized"
    ERROR = "error"
    AUDIO_CHUNK = "audio_chunk"
    AUDIO_START = "audio_start"
    AUDIO_END = "audio_end"
    SESSION_END = "session_end"
    SESSION_START = "session_start"
    STREAMING = "streaming"
    RESPONSE_STREAMING = "response_streaming"
    RESPONSE = "response"
    CONFIG_UPDATED = "session_updated"


token_provider = AzureADTokenProvider()


class AudioStreamCallback(speechsdk.audio.PushAudioOutputStreamCallback):
    def __init__(self, ws_handler, chunk_size=4096):
        super().__init__()
        self.ws_handler = ws_handler
        self.chunk_size = chunk_size
        self.audio_buffer = bytearray()

    def write(self, audio_buffer):
        if not self.ws_handler.speech_active:
            self.ws_handler.speech_active = True
            logger.info("Starting audio stream handling.")
            self.ws_handler.send_text(
                json.dumps({"type": SOCKET_EVENTS.AUDIO_START.value})
            )
        # Buffer audio and send in larger chunks
        self.audio_buffer.extend(audio_buffer)
        while len(self.audio_buffer) >= self.chunk_size:
            chunk = self.audio_buffer[: self.chunk_size]
            del self.audio_buffer[: self.chunk_size]
            asyncio.run_coroutine_threadsafe(
                self.ws_handler.ws.send_bytes(bytes(chunk)),
                self.ws_handler.event_loop,
            )
        return len(audio_buffer)

    def flush(self):
        # Send any remaining audio in the buffer
        if self.audio_buffer:
            asyncio.run_coroutine_threadsafe(
                self.ws_handler.ws.send_bytes(bytes(self.audio_buffer)),
                self.ws_handler.event_loop,
            )
            self.audio_buffer.clear()

    def close(self):
        self.flush()


class WebsocketConnectionHandler:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.temperature = 0.7
        self.max_tokens = 500
        self.turn_silence_timeout = 0.5
        self.turn_active = False
        self.turn_start_sent = False
        self.turn_end_sent = False
        self.speech_active = False
        self.should_send_text = True
        self.silence_timer = None
        self.turn_text_buffer = []
        self.event_loop = asyncio.get_event_loop()
        self.tts_stop_event = threading.Event()
        self.voice = "en-US-GuyNeural"
        self.locale = "en-US"
        # Voice customization defaults (aligned with app.py)
        self.voice_style: Optional[str] = (
            "normal"  # "normal", "calm", "chat", "excited", etc.
        )
        self.voice_rate: str = (
            "medium"  # "x-slow" | "slow" | "medium" | "fast" | "x-fast"
        )
        self.voice_temperature: Optional[float] = 0.7  # maps to styledegree
        # Back-compat fields (kept for existing usages)
        self.style: Optional[str] = None
        self.style_degree: Optional[float] = None
        self.current_turn_id: Optional[str] = None
        self.end_session_after_tts = False

        self.recognizer, self.recognizer_stream = self.create_speech_recognizer()
        self.recognizer.recognizing.connect(self.speech_recognition_handler)
        self.recognizer.recognized.connect(self.speech_recognition_handler)

        self.foundry_client = OpenAIFoundryClient(
            azure_ad_token_provider=token_provider,
            api_version="2024-02-01",
            azure_endpoint=OPENAI_ENDPOINT,
            deployment_name=DEPLOYMENT_NAME,
            system_prompt=self.load_system_prompt(),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )

        end_conversation_tool = {
            "type": "function",
            "function": {
                "name": "end_conversation",
                "description": "Ends the current conversation when the user wants to disconnect or has no further queries.",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        self.foundry_client.register_tool(
            end_conversation_tool, self.end_conversation_handler
        )

    def end_conversation_handler(self):
        logger.info("LLM triggered conversation end.")
        self.end_session_after_tts = True
        return "Success"

    def load_system_prompt(self) -> str:
        try:
            with open("system_prompt.txt", "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return "You are a helpful assistant."

    def create_speech_recognizer(self):
        logger.debug("Creating speech recognizer.")
        # speech_config = speechsdk.SpeechConfig(
            # subscription=SPEECH_KEY, region=SPEECH_REGION
        # )
        speech_config = get_speech_config()

        audio_format = speechsdk.audio.AudioStreamFormat(
            samples_per_second=16000, bits_per_sample=16, channels=1
        )
        stream = speechsdk.audio.PushAudioInputStream(audio_format)
        audio_input = speechsdk.audio.AudioConfig(stream=stream)
        recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config, audio_config=audio_input
        )
        logger.debug("Speech recognizer created.")
        return recognizer, stream

    def create_tts_synthesizer(self):
        # Setup speech config
        # speech_config = speechsdk.SpeechConfig(
        #     subscription=SPEECH_KEY, region=SPEECH_REGION
        # )

        # Setup speech config using AAD token (no subscription key)
        speech_config = get_speech_config

        # Create push stream
        stream = speechsdk.audio.PushAudioOutputStream()
        audio_config = speechsdk.audio.AudioOutputConfig(stream=stream)

        # Synthesizer with stream output
        synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=speech_config, audio_config=audio_config
        )
        return synthesizer, stream

    def build_ssml(self, text: str) -> str:
        # Escape XML special characters in the text to make it safe for SSML.
        sanitized_text = escape(text)

        # Derive final style and styledegree using new fields if present
        style = (
            self.voice_style
            if self.voice_style not in (None, "normal", "")
            else (self.style or None)
        )
        styledegree = (
            self.voice_temperature
            if self.voice_temperature is not None
            else self.style_degree
        )
        rate = self.voice_rate or "medium"

        # Wrap the content with prosody rate (optional)
        content = sanitized_text
        if rate:
            content = f'<prosody rate="{rate}">{content}</prosody>'

        ssml = f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{self.locale}">'
        ssml += f'<voice name="{self.voice}">'
        if style is not None or styledegree is not None:
            ssml += f'<mstts:express-as style="{style or "general"}"'
            if styledegree is not None:
                ssml += f' styledegree="{styledegree}"'
            ssml += f' xmlns:mstts="http://www.w3.org/2001/mstts">'
            ssml += content
            ssml += "</mstts:express-as>"
        else:
            ssml += content
        ssml += "</voice></speak>"
        return ssml

    def clear_tts_buffer(self):
        # Stop any ongoing TTS and reset state
        self.tts_stop_event.set()
        self.speech_active = False

    def speak(
        self,
        text: str,
    ):
        if not text:
            logger.warning("No text to speak.")
            return
        logger.info(f"Speaking text: {text}")
        # Reset TTS state before starting new synthesis
        self.tts_stop_event.clear()
        self.speech_active = False

        speech_config = speechsdk.SpeechConfig(
            subscription=SPEECH_KEY, region=SPEECH_REGION
        )

        stream_callback = AudioStreamCallback(self)
        stream = speechsdk.audio.PushAudioOutputStream(stream_callback)
        audio_config = speechsdk.audio.AudioOutputConfig(stream=stream)

        synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=speech_config, audio_config=audio_config
        )

        try:
            result = synthesizer.speak_ssml_async(self.build_ssml(text)).get()
        except Exception as e:
            logger.error(f"TTS interrupted or failed: {e}")
            result = None
        finally:
            stream_callback.flush()
            self.speech_active = False
            self.send_text(json.dumps({"type": SOCKET_EVENTS.AUDIO_END.value}))
            if self.end_session_after_tts:
                self.send_text(json.dumps({"type": SOCKET_EVENTS.SESSION_END.value}))
                self.session_ended()

        if (
            result
            and result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted
        ):
            logger.info("Speech synthesized successfully.")
        elif result:
            logger.error(f"Error: {result}")

    def send_text(self, message: str):
        logger.debug(f"Sending message: {message}")
        if self.should_send_text:
            asyncio.run_coroutine_threadsafe(
                self.ws.send_text(message), self.event_loop
            )

    def turn_started(self):
        if not self.turn_active or not self.turn_start_sent:
            self.turn_active = True
            self.turn_start_sent = True
            self.turn_end_sent = False
            self.current_turn_id = str(uuid.uuid4())
            # Clear TTS buffer and stop playback
            self.clear_tts_buffer()
            # Send AUDIO_END to ensure frontend resets audio state
            self.send_text(json.dumps({"type": SOCKET_EVENTS.AUDIO_END.value}))
            self.send_text(json.dumps({"type": SOCKET_EVENTS.TURN_START.value}))
            logger.debug("TURN_START event sent.")

    def turn_ended(self):
        if self.turn_active and not self.turn_end_sent:
            self.turn_active = False
            self.turn_end_sent = True
            self.turn_start_sent = False
            self.send_text(json.dumps({"type": SOCKET_EVENTS.TURN_END.value}))
            logger.debug("TURN_END event sent.")

    def speech_recognition_handler(self, evt):
        logger.debug(f"Speech recognition event: {evt}")
        try:
            if not evt.result.text:
                return
        except Exception as e:
            logger.error(f"Error accessing evt.result.text: {e}")
            return
        if evt.result.reason == speechsdk.ResultReason.RecognizingSpeech:
            if not self.turn_active or not self.turn_start_sent:
                self.turn_started()
            partial_text = evt.result.text
            self.send_text(
                json.dumps(
                    {
                        "type": SOCKET_EVENTS.STREAMING.value,
                        "delta": partial_text,
                        "id": self.current_turn_id,
                    }
                )
            )
            self.reset_silence_timer()
        elif evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
            text = evt.result.text
            self.turn_text_buffer.append(text)
            self.reset_silence_timer()

    def turn_end_handler(self):
        logger.info("Turn ended due to silence.")
        if self.turn_text_buffer:
            text = " ".join(self.turn_text_buffer)
            logger.debug(f"Final recognized text: {text}")
            self.send_text(
                json.dumps(
                    {
                        "type": SOCKET_EVENTS.RECOGNIZED.value,
                        "transcript": text,
                        "id": self.current_turn_id,
                    }
                )
            )
            logger.debug("Clearing turn text buffer.")
            self.turn_text_buffer = []
            self.tts_stop_event.clear()

            response_id = str(uuid.uuid4())

            def stream_callback(chunk: str):
                self.send_text(
                    json.dumps(
                        {
                            "type": SOCKET_EVENTS.RESPONSE_STREAMING.value,
                            "delta": chunk,
                            "id": response_id,
                        }
                    )
                )

            response = self.foundry_client.chat(text, stream_callback=stream_callback)
            self.send_text(
                json.dumps(
                    {
                        "type": SOCKET_EVENTS.RESPONSE.value,
                        "transcript": response,
                        "id": response_id,
                    }
                )
            )
            # Clean up response for TTS by replacing newlines and other whitespace.
            speech_text = " ".join(response.split()) if response else ""
            self.speak(speech_text)

        self.turn_ended()

    def reset_silence_timer(self):
        logger.debug("Resetting silence timer.")
        if self.silence_timer:
            self.silence_timer.cancel()
        self.silence_timer = threading.Timer(
            self.turn_silence_timeout, self.turn_end_handler
        )
        self.silence_timer.start()

    def session_started(self):
        logger.info("Session started.")
        self.send_text(json.dumps({"type": SOCKET_EVENTS.SESSION_START.value}))

    def session_ended(self):
        logger.info("Session ended.")
        if self.silence_timer:
            self.silence_timer.cancel()
        self.tts_stop_event.set()
        self.recognizer.stop_continuous_recognition()
        self.recognizer_stream.close()
        self.send_text(json.dumps({"type": SOCKET_EVENTS.SESSION_END.value}))

    def handle_config_update(self, config: dict):
        # Support either top-level fields or nested config under "data"
        cfg = config.get("data", config)
        self.turn_silence_timeout = cfg.get(
            "turn_silence_timeout", self.turn_silence_timeout
        )

        self.should_send_text = cfg.get("should_send_text", self.should_send_text)
        self.voice = cfg.get("voice", self.voice)
        self.locale = cfg.get("locale", self.locale)

        # Voice style (prefer explicit voice_style; fall back to legacy "style")
        if "voice_style" in cfg or "style" in cfg:
            self.voice_style = cfg.get(
                "voice_style", cfg.get("style", self.voice_style)
            )
            # keep legacy fields in sync
            self.style = (
                None if self.voice_style in (None, "normal", "") else self.voice_style
            )

        # Voice temperature maps to styledegree
        if "voice_temperature" in cfg or "style_degree" in cfg:
            try:
                self.voice_temperature = float(
                    cfg.get(
                        "voice_temperature",
                        cfg.get("style_degree", self.voice_temperature),
                    )
                )
            except Exception:
                pass
            # keep legacy field in sync
            self.style_degree = self.voice_temperature

        # Speaking rate
        if "voice_rate" in cfg or "rate" in cfg:
            self.voice_rate = cfg.get("voice_rate", cfg.get("rate", self.voice_rate))

        # Model settings
        self.temperature = cfg.get("temperature", self.temperature)
        self.max_tokens = cfg.get("max_tokens", self.max_tokens)

        self.foundry_client.temperature = self.temperature
        self.foundry_client.max_tokens = self.max_tokens

        # Echo back normalized config (include both ms and s for clarity)
        self.send_text(
            json.dumps(
                {
                    "type": SOCKET_EVENTS.CONFIG_UPDATED.value,
                    "data": {
                        "turn_silence_timeout": self.turn_silence_timeout,
                        "turn_silence_timeout_ms": int(
                            self.turn_silence_timeout * 1000
                        ),
                        "should_send_text": self.should_send_text,
                        "voice": self.voice,
                        "locale": self.locale,
                        "voice_style": self.voice_style,
                        "voice_rate": self.voice_rate,
                        "voice_temperature": self.voice_temperature,
                        # legacy aliases for backward compatibility
                        "style": self.style,
                        "style_degree": self.style_degree,
                        "temperature": self.temperature,
                        "max_tokens": self.max_tokens,
                    },
                }
            )
        )

    async def handle(self):
        try:
            self.session_started()
            self.recognizer.session_stopped.connect(self.session_ended)
            self.recognizer.start_continuous_recognition()

            while True:
                message = await self.ws.receive()
                if "text" in message:
                    data = message["text"]
                    try:
                        msg = json.loads(data)
                        msg_type = msg.get("type")
                        if msg_type == "session_update":
                            # Accept both flattened and nested config payloads
                            self.handle_config_update(msg)
                        else:
                            await self.send_text(
                                {
                                    "type": SOCKET_EVENTS.ERROR.value,
                                    "data": {"error": "Unknown message type"},
                                }
                            )
                    except Exception as e:
                        await self.send_text(
                            {
                                "type": SOCKET_EVENTS.ERROR.value,
                                "data": {
                                    "error": f"Error processing message: {str(e)}"
                                },
                            }
                        )
                elif "bytes" in message:
                    audio_bytes = message["bytes"]
                    self.recognizer_stream.write(audio_bytes)
        except Exception as e:
            logger.warning(f"Client disconnected. Error: {e}")
        finally:
            if self.silence_timer:
                self.silence_timer.cancel()
            self.tts_stop_event.set()
            self.recognizer.stop_continuous_recognition()
            self.recognizer_stream.close()
            self.send_text(json.dumps({"type": SOCKET_EVENTS.SESSION_END.value}))

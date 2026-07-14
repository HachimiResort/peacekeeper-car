"""Local wake-word and half-duplex voice bridge for the vehicle."""
from __future__ import annotations

import asyncio
import contextvars
import functools
import json
import threading
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

from .config import VoiceConfig

try:  # Optional hardware/runtime dependencies; failures degrade only voice.
    import numpy as np
    import sherpa_onnx
    import sounddevice as sd
    import websockets
except Exception as exc:  # pragma: no cover - exercised on the Jetson image.
    np = None
    sherpa_onnx = None
    sd = None
    websockets = None
    IMPORT_ERROR = str(exc)
else:
    IMPORT_ERROR = None


async def _to_thread(function, /, *args, **kwargs):
    """Python 3.8-compatible equivalent of asyncio.to_thread()."""
    loop = asyncio.get_running_loop()
    context = contextvars.copy_context()
    call = functools.partial(context.run, function, *args, **kwargs)
    return await loop.run_in_executor(None, call)


class VoiceGateway:
    STATES = {"disabled", "starting", "connecting", "wake_listening", "command_listening", "waiting", "speaking", "followup", "degraded", "stopped"}

    def __init__(
        self,
        config: VoiceConfig,
        stop_motion: Callable[[], None],
        beep: Callable[[bool, int], dict],
        play_error_prompt: Optional[Callable[[], object]] = None,
    ):
        self.config = config
        self.stop_motion = stop_motion
        self.beep = beep
        self.play_error_prompt = play_error_prompt
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._diagnostic_wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._input = None
        self._output = None
        self._input_device = config.input_device
        self._output_device = config.output_device
        self._input_device_name = config.input_device
        self._output_device_name = config.output_device
        self._spotter = None
        self._kws_stream = None
        self._stop_kws_stream = None
        self._state = "disabled" if not config.enabled else "stopped"
        self._connected = False
        self._last_transcript: Optional[str] = None
        self._last_reply: Optional[str] = None
        self._last_error: Optional[str] = IMPORT_ERROR
        self._last_wake_at: Optional[float] = None
        self._last_wake_source: Optional[str] = None
        self._input_rms = 0.0
        self._input_peak = 0.0
        self._input_overflows = 0
        self._last_input_at: Optional[float] = None
        self._last_error_prompt_at = 0.0

    def start(self) -> dict:
        with self._lock:
            if not self.config.enabled:
                self._state = "disabled"
                self._last_error = "Voice is disabled by configuration"
                return self.status()
            if self._thread is not None and self._thread.is_alive():
                return self.status()
            self._stop.clear()
            self._diagnostic_wake.clear()
            self._state = "starting"
            self._thread = threading.Thread(target=self._thread_main, name="voice-gateway", daemon=True)
            self._thread.start()
            return self.status()

    def stop(self) -> dict:
        self._stop.set()
        self._diagnostic_wake.clear()
        self._close_audio()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        with self._lock:
            self._connected = False
            self._state = "stopped" if self.config.enabled else "disabled"
        return self.status()

    def trigger_wake(self) -> dict:
        """Inject one wake event for diagnostics while preserving the real audio path."""
        with self._lock:
            running = bool(self._thread and self._thread.is_alive())
            if not running:
                return {"ok": False, "accepted": False, "message": "Voice gateway is not running", **self.status()}
            if not self._connected:
                return {"ok": False, "accepted": False, "message": "Mission API voice connection is not ready", **self.status()}
            if self._state != "wake_listening":
                return {
                    "ok": False,
                    "accepted": False,
                    "message": f"Voice gateway is busy in state '{self._state}'",
                    **self.status(),
                }
            self._last_error = None
            self._diagnostic_wake.set()
            return {"ok": True, "accepted": True, "message": "Diagnostic wake accepted; speak the command now", **self.status()}

    def shutdown(self) -> None:
        self.stop()

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": self.config.enabled,
                "running": bool(self._thread and self._thread.is_alive()),
                "state": self._state,
                "connected": self._connected,
                "robot_id": self.config.robot_id,
                "mission_api_configured": bool(self.config.mission_api_url and self._token()),
                "input_device": self._input_device_name,
                "output_device": self._output_device_name,
                "input_sample_rate": self.config.input_sample_rate,
                "output_sample_rate": self.config.output_sample_rate,
                "wake_phrase": self.config.wake_phrase,
                "stop_phrase": self.config.stop_phrase,
                "kws_ready": self._spotter is not None,
                "kws_input_gain": self.config.kws_input_gain,
                "keywords_score": self.config.keywords_score,
                "keywords_threshold": self.config.keywords_threshold,
                "last_transcript": self._last_transcript,
                "last_reply": self._last_reply,
                "last_wake_at": self._last_wake_at,
                "last_wake_source": self._last_wake_source,
                "input_rms": self._input_rms,
                "input_peak": self._input_peak,
                "input_overflows": self._input_overflows,
                "last_input_at": self._last_input_at,
                "last_error": self._last_error,
                "import_error": IMPORT_ERROR,
            }

    def _thread_main(self) -> None:
        try:
            if IMPORT_ERROR:
                raise RuntimeError(f"Voice dependencies unavailable: {IMPORT_ERROR}")
            if not self.config.mission_api_url or not self._token():
                raise RuntimeError("Voice requires mission_api_url and a token")
            self._resolve_audio_devices()
            self._load_kws()
            asyncio.run(self._run())
        except Exception as exc:
            self._set_state("degraded", error=self._safe_error(exc))
            self._play_local_error()
        finally:
            self._close_audio()
            with self._lock:
                self._connected = False

    async def _run(self) -> None:
        while not self._stop.is_set():
            self._set_state("connecting")
            try:
                async with websockets.connect(
                    self._websocket_url(),
                    open_timeout=self.config.connect_timeout_s,
                    close_timeout=2,
                    max_size=8 * 1024 * 1024,
                ) as socket:
                    with self._lock:
                        self._connected = True
                        self._last_error = None
                    await socket.send(json.dumps({"type": "hello", "format": "pcm_s16le", "sample_rate": self.config.input_sample_rate}))
                    ready = json.loads(await socket.recv())
                    if ready.get("type") not in {"ready", "state"}:
                        raise RuntimeError(ready.get("message") or "Mission API rejected voice connection")
                    followup = False
                    while not self._stop.is_set():
                        if followup:
                            self._set_state("followup")
                            audio = await _to_thread(self._record_utterance, self.config.followup_timeout_s)
                            if not audio:
                                followup = False
                                continue
                        else:
                            self._set_state("wake_listening")
                            keyword = await _to_thread(self._wait_for_keyword)
                            if keyword == self.config.stop_phrase:
                                self.stop_motion()
                                continue
                            if keyword != self.config.wake_phrase:
                                continue
                            self._last_wake_at = time.time()
                            try:
                                self.beep(True, 100)
                            except Exception:
                                pass
                            self._set_state("command_listening")
                            audio = await _to_thread(self._record_utterance, 7.0)
                            if not audio:
                                continue
                        await socket.send(json.dumps({"type": "turn.start", "new_session": not followup}))
                        for offset in range(0, len(audio), 3200):
                            await socket.send(audio[offset : offset + 3200])
                        await socket.send(json.dumps({"type": "turn.end"}))
                        followup = await self._receive_reply(socket)
            except Exception as exc:
                self._set_state("degraded", error=self._safe_error(exc))
                self._play_local_error()
                with self._lock:
                    self._connected = False
                if self._stop.wait(self.config.reconnect_s):
                    break

    async def _receive_reply(self, socket) -> bool:
        # Recording is complete. Close capture while waiting so its PortAudio
        # buffer cannot overflow during ASR/LLM/TTS processing.
        self._close_input()
        self._set_state("waiting")
        output_started = False
        turn_started = False
        while not self._stop.is_set():
            packet = await socket.recv()
            if isinstance(packet, bytes):
                if not output_started:
                    self._open_output()
                    output_started = True
                    self._set_state("speaking")
                await _to_thread(self._output.write, packet)
                continue
            event = json.loads(packet)
            kind = event.get("type")
            if kind == "transcript.final":
                turn_started = True
                self._last_transcript = str(event.get("text") or "")
            elif kind == "reply.text":
                turn_started = True
                self._last_reply = str(event.get("text") or "")
            elif kind == "reply.audio.start":
                self._open_output()
                output_started = True
                self._set_state("speaking")
            elif kind == "reply.audio.end":
                self._close_output()
            elif kind == "state" and event.get("state") == "followup":
                self._close_output()
                return True
            elif kind == "state" and event.get("state") in {"recognizing", "thinking"}:
                turn_started = True
            elif kind == "state" and event.get("state") == "wake_listening" and turn_started:
                self._close_output()
                return False
            elif kind == "error":
                self._last_error = str(event.get("message") or "Voice turn failed")

    def _load_kws(self) -> None:
        root = Path(self.config.kws_model_dir)
        required = {
            "tokens": root / "tokens.txt",
            "encoder": root / "encoder.int8.onnx",
            "decoder": root / "decoder.int8.onnx",
            "joiner": root / "joiner.int8.onnx",
            "keywords_file": root / "keywords.txt",
        }
        missing = [str(path) for path in required.values() if not path.is_file()]
        if missing:
            raise RuntimeError("KWS model files are missing: " + ", ".join(missing))
        self._spotter = sherpa_onnx.KeywordSpotter(
            **{key: str(value) for key, value in required.items()},
            num_threads=1,
            max_active_paths=4,
            keywords_score=self.config.keywords_score,
            keywords_threshold=self.config.keywords_threshold,
            num_trailing_blanks=1,
            provider="cpu",
        )
        self._kws_stream = self._spotter.create_stream()
        self._stop_kws_stream = self._spotter.create_stream()

    def _wait_for_keyword(self) -> str:
        if self._consume_diagnostic_wake():
            return self.config.wake_phrase
        self._open_input()
        while not self._stop.is_set():
            if self._consume_diagnostic_wake():
                return self.config.wake_phrase
            samples = self._read_float_block()
            if self._consume_diagnostic_wake():
                return self.config.wake_phrase
            self._kws_stream.accept_waveform(self.config.input_sample_rate, self._kws_samples(samples))
            while self._spotter.is_ready(self._kws_stream):
                self._spotter.decode_stream(self._kws_stream)
            result = self._spotter.get_result(self._kws_stream)
            if result:
                self._diagnostic_wake.clear()
                self._last_wake_source = "keyword"
                self._kws_stream = self._spotter.create_stream()
                return str(result).strip()
        return ""

    def _consume_diagnostic_wake(self) -> bool:
        if not self._diagnostic_wake.is_set():
            return False
        self._diagnostic_wake.clear()
        self._last_wake_source = "diagnostic"
        return True

    def _record_utterance(self, start_timeout_s: float) -> bytes:
        self._open_input()
        chunks = []
        started = False
        silence_blocks = 0
        silence_limit = max(1, round(self.config.silence_ms / 100))
        start_deadline = time.monotonic() + start_timeout_s
        end_deadline = time.monotonic() + self.config.max_utterance_s
        while not self._stop.is_set() and time.monotonic() < end_deadline:
            samples = self._read_float_block()
            if self._detect_local_stop(samples):
                self.stop_motion()
                return b""
            rms = float(np.sqrt(np.mean(np.square(samples)))) if len(samples) else 0.0
            if not started:
                if rms < self.config.vad_rms_threshold:
                    if time.monotonic() >= start_deadline:
                        return b""
                    continue
                started = True
            chunks.append(np.clip(samples * 32767.0, -32768, 32767).astype("<i2").tobytes())
            if rms < self.config.vad_rms_threshold:
                silence_blocks += 1
                if silence_blocks >= silence_limit:
                    break
            else:
                silence_blocks = 0
        return b"".join(chunks)

    def _detect_local_stop(self, samples) -> bool:
        """Run the safety keyword in parallel with VAD while recording a command."""
        self._stop_kws_stream.accept_waveform(self.config.input_sample_rate, self._kws_samples(samples))
        while self._spotter.is_ready(self._stop_kws_stream):
            self._spotter.decode_stream(self._stop_kws_stream)
        result = str(self._spotter.get_result(self._stop_kws_stream) or "").strip()
        if not result:
            return False
        self._stop_kws_stream = self._spotter.create_stream()
        return result == self.config.stop_phrase

    def _kws_samples(self, samples):
        """Boost quiet microphones for local KWS without changing ASR audio."""
        gain = max(0.1, min(float(self.config.kws_input_gain), 10.0))
        return np.clip(samples * gain, -1.0, 1.0)

    def _read_float_block(self):
        data, overflowed = self._input.read(max(1, self.config.input_sample_rate // 10))
        samples = np.frombuffer(data, dtype=np.float32)
        rms = float(np.sqrt(np.mean(np.square(samples)))) if len(samples) else 0.0
        peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
        with self._lock:
            if overflowed:
                # A single dropped block can occur while the local wake beep is
                # played. Count it for diagnostics without masking ASR/TTS errors.
                self._input_overflows += 1
            self._input_rms = rms
            self._input_peak = peak
            self._last_input_at = time.time()
        return samples

    def _open_input(self) -> None:
        if self._input is None:
            self._input = sd.RawInputStream(
                samplerate=self.config.input_sample_rate,
                channels=1,
                dtype="float32",
                device=self._input_device,
                blocksize=max(1, self.config.input_sample_rate // 10),
            )
            self._input.start()

    def _open_output(self) -> None:
        self._close_input()
        if self._output is None:
            self._output = sd.RawOutputStream(
                samplerate=self.config.output_sample_rate,
                channels=1,
                dtype="int16",
                device=self._output_device,
            )
            self._output.start()

    def _close_input(self) -> None:
        stream, self._input = self._input, None
        if stream is not None:
            try:
                stream.stop(); stream.close()
            except Exception:
                pass

    def _close_output(self) -> None:
        stream, self._output = self._output, None
        if stream is not None:
            try:
                stream.stop(); stream.close()
            except Exception:
                pass

    def _close_audio(self) -> None:
        self._close_input()
        self._close_output()

    def _set_state(self, state: str, error: Optional[str] = None) -> None:
        with self._lock:
            self._state = state
            if error is not None:
                self._last_error = error

    def _resolve_audio_devices(self) -> None:
        devices = list(sd.query_devices())

        def supports(device, channel_key: str) -> bool:
            try:
                if channel_key == "max_input_channels":
                    sd.check_input_settings(
                        device=device,
                        channels=1,
                        dtype="float32",
                        samplerate=self.config.input_sample_rate,
                    )
                else:
                    sd.check_output_settings(
                        device=device,
                        channels=1,
                        dtype="int16",
                        samplerate=self.config.output_sample_rate,
                    )
                return True
            except Exception:
                return False

        def choose(explicit, channel_key: str, default_index: int):
            if explicit is not None:
                info = sd.query_devices(explicit)
                if not supports(explicit, channel_key):
                    sample_rate = (
                        self.config.input_sample_rate
                        if channel_key == "max_input_channels"
                        else self.config.output_sample_rate
                    )
                    raise RuntimeError(
                        f"Audio device '{info['name']}' does not support mono {sample_rate} Hz"
                    )
                return explicit, str(info["name"])
            candidates = [
                (index, item) for index, item in enumerate(devices)
                if int(item.get(channel_key, 0)) > 0 and "usb" in str(item.get("name", "")).lower()
            ]
            if channel_key == "max_input_channels":
                # Depth cameras often expose a nominal USB Audio capture device
                # that opens successfully but never yields frames. Keep it only
                # as a fallback when no dedicated USB microphone is available.
                camera_markers = ("orbbec", "depth", "camera")
                candidates.sort(
                    key=lambda candidate: (
                        any(marker in str(candidate[1].get("name", "")).lower() for marker in camera_markers),
                        candidate[0],
                    )
                )
            candidate_indexes = [candidate[0] for candidate in candidates]
            if default_index is not None and int(default_index) >= 0:
                candidate_indexes.append(int(default_index))
            index = next((item for item in candidate_indexes if supports(item, channel_key)), None)
            if index is None:
                raise RuntimeError(f"No audio device provides {channel_key}")
            return int(index), str(devices[int(index)]["name"])

        defaults = sd.default.device
        self._input_device, self._input_device_name = choose(self.config.input_device, "max_input_channels", defaults[0])
        self._output_device, self._output_device_name = choose(self.config.output_device, "max_output_channels", defaults[1])

    def _play_local_error(self) -> None:
        """Play a pre-provisioned Mandarin failure prompt, with a beep fallback."""
        now = time.monotonic()
        if now - self._last_error_prompt_at < 10.0:
            return
        self._last_error_prompt_at = now
        try:
            if self.play_error_prompt is not None:
                self.play_error_prompt()
                return
        except Exception:
            pass
        try:
            self.beep(True, 300)
        except Exception:
            pass

    def _token(self) -> str:
        return self.config.mission_api_token

    def _safe_error(self, error: Exception) -> str:
        value = str(error)
        token = self._token()
        return value.replace(token, "[redacted]") if token else value

    def _websocket_url(self) -> str:
        base = self.config.mission_api_url.rstrip("/")
        if base.startswith("https://"):
            base = "wss://" + base[8:]
        elif base.startswith("http://"):
            base = "ws://" + base[7:]
        return f"{base}/ws/voice/{quote(self.config.robot_id, safe='')}?token={quote(self._token(), safe='')}"

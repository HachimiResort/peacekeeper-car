import asyncio
import contextvars
import json
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fleet_agent.config import VoiceConfig, load_config
from fleet_agent.voice_gateway import VoiceGateway, _to_thread
import fleet_agent.voice_gateway as voice_module


class ScriptedSocket:
    def __init__(self, events):
        self.events = [json.dumps(event) for event in events]

    async def recv(self):
        await asyncio.sleep(0)
        return self.events.pop(0)


class FakeSpotter:
    def __init__(self, result):
        self.result = result

    def is_ready(self, stream):
        return False

    def decode_stream(self, stream):
        raise AssertionError("stream should not be ready")

    def get_result(self, stream):
        return self.result

    def create_stream(self):
        return FakeStream()


class FakeStream:
    def accept_waveform(self, sample_rate, samples):
        self.sample_rate = sample_rate
        self.samples = samples


class FakeSoundDevice:
    class Default:
        device = (0, 0)

    default = Default()
    devices = [
        {"name": "Built-in Audio", "max_input_channels": 1, "max_output_channels": 2},
        {"name": "ORBBEC Depth Sensor: USB Audio", "max_input_channels": 1, "max_output_channels": 0},
        {"name": "USB Conference Mic", "max_input_channels": 1, "max_output_channels": 0},
        {"name": "USB Conference Speaker", "max_input_channels": 0, "max_output_channels": 2},
    ]

    @classmethod
    def query_devices(cls, device=None):
        return cls.devices if device is None else cls.devices[int(device)]

    @staticmethod
    def check_input_settings(**_kwargs):
        return None

    @staticmethod
    def check_output_settings(**_kwargs):
        return None


class IncompatibleUsbOutputSoundDevice(FakeSoundDevice):
    @staticmethod
    def check_output_settings(device, **_kwargs):
        if int(device) == 3:
            raise RuntimeError("Invalid sample rate")


class VoiceGatewayTests(unittest.IsolatedAsyncioTestCase):
    def make_gateway(self, enabled=False):
        self.stops = 0

        def stop():
            self.stops += 1

        return VoiceGateway(VoiceConfig(enabled=enabled), stop, lambda enabled, duration: {})

    def test_disabled_gateway_does_not_start_background_thread(self):
        gateway = self.make_gateway()

        status = gateway.start()

        self.assertEqual(status["state"], "disabled")
        self.assertFalse(status["running"])

    def test_shared_token_is_reused_for_voice_connection(self):
        with patch.dict(os.environ, {"PEACEKEEPER_SHARED_TOKEN": "shared"}):
            config = load_config()

        self.assertEqual(config.security.shared_token, "shared")
        self.assertEqual(config.voice.mission_api_token, "shared")

    def test_local_stop_keyword_is_detected_without_network(self):
        gateway = self.make_gateway(enabled=True)
        gateway._spotter = FakeSpotter("停止")
        gateway._stop_kws_stream = FakeStream()

        with patch.object(voice_module, "np", np):
            detected = gateway._detect_local_stop(np.array([0.1, 0.2], dtype=np.float32))

        self.assertTrue(detected)

    def test_kws_gain_is_clipped_and_does_not_modify_source_samples(self):
        gateway = self.make_gateway(enabled=True)
        gateway.config.kws_input_gain = 2.5
        samples = np.array([0.2, -0.5], dtype=np.float32)

        with patch.object(voice_module, "np", np):
            boosted = gateway._kws_samples(samples)

        np.testing.assert_allclose(boosted, [0.5, -1.0])
        np.testing.assert_allclose(samples, [0.2, -0.5])

    def test_usb_audio_devices_are_preferred_over_builtin_defaults(self):
        gateway = self.make_gateway(enabled=True)
        with patch.object(voice_module, "sd", FakeSoundDevice):
            gateway._resolve_audio_devices()

        status = gateway.status()
        self.assertEqual(status["input_device"], "USB Conference Mic")
        self.assertEqual(status["output_device"], "USB Conference Speaker")

    def test_incompatible_usb_output_falls_back_to_default_device(self):
        gateway = self.make_gateway(enabled=True)
        with patch.object(voice_module, "sd", IncompatibleUsbOutputSoundDevice):
            gateway._resolve_audio_devices()

        status = gateway.status()
        self.assertEqual(status["input_device"], "USB Conference Mic")
        self.assertEqual(status["output_device"], "Built-in Audio")

    async def test_python_38_thread_compatibility_preserves_context(self):
        marker = contextvars.ContextVar("marker")
        marker.set("voice-turn")
        event_loop_thread = threading.get_ident()

        result, worker_thread = await _to_thread(
            lambda prefix="": (prefix + marker.get(), threading.get_ident()),
            prefix="active-",
        )

        self.assertEqual(result, "active-voice-turn")
        self.assertNotEqual(worker_thread, event_loop_thread)

    def test_diagnostic_wake_is_only_accepted_while_ready(self):
        gateway = self.make_gateway(enabled=True)

        rejected = gateway.trigger_wake()

        self.assertFalse(rejected["accepted"])
        gateway._thread = unittest.mock.Mock()
        gateway._thread.is_alive.return_value = True
        gateway._connected = True
        gateway._state = "wake_listening"

        accepted = gateway.trigger_wake()

        self.assertTrue(accepted["accepted"])
        self.assertEqual(gateway._wait_for_keyword(), "你好")
        self.assertEqual(gateway.status()["last_wake_source"], "diagnostic")

    def test_diagnostic_wake_is_rejected_during_an_active_turn(self):
        gateway = self.make_gateway(enabled=True)
        gateway._thread = unittest.mock.Mock()
        gateway._thread.is_alive.return_value = True
        gateway._connected = True
        gateway._state = "speaking"

        result = gateway.trigger_wake()

        self.assertFalse(result["accepted"])
        self.assertIn("busy", result["message"])

    async def test_stale_handshake_state_does_not_end_new_turn(self):
        gateway = self.make_gateway(enabled=True)
        socket = ScriptedSocket([
            {"type": "state", "state": "wake_listening"},
            {"type": "state", "state": "recognizing"},
            {"type": "transcript.final", "text": "打开灯"},
            {"type": "reply.text", "text": "灯已打开"},
            {"type": "state", "state": "followup"},
        ])

        followup = await gateway._receive_reply(socket)

        self.assertTrue(followup)
        self.assertEqual(gateway.status()["last_transcript"], "打开灯")
        self.assertEqual(gateway.status()["last_reply"], "灯已打开")


if __name__ == "__main__":
    unittest.main()

"""Small async clients for Volcengine Ark Responses, BigASR, and Seed TTS."""
from __future__ import annotations

import asyncio
import base64
import gzip
import json
import struct
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import websockets


class VoiceProviderError(RuntimeError):
    pass


def _provider_http_error(service: str, status_code: int, body: bytes | bytearray | str = b"") -> VoiceProviderError:
    if isinstance(body, str):
        detail = body
    else:
        detail = bytes(body).decode(errors="replace")
    detail = " ".join(detail.strip().split())[:300]
    suffix = f": {detail}" if detail else ""
    return VoiceProviderError(f"{service} failed with HTTP {status_code}{suffix}")


class ArkResponsesClient:
    def __init__(self, api_key: str, model: str, base_url: str, timeout_s: float = 30.0):
        self.api_key = api_key
        self.model = model
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def create(self, input_items: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        response = await self.client.post(
            "/responses",
            json={
                "model": self.model,
                "store": False,
                "thinking": {"type": "disabled"},
                "input": input_items,
                "tools": tools,
            },
        )
        if response.is_error:
            raise VoiceProviderError(f"Ark Responses failed with HTTP {response.status_code}")
        value = response.json()
        if not isinstance(value, dict):
            raise VoiceProviderError("Ark Responses returned a non-object response")
        return value


class VolcASRClient:
    """One-shot utterance recognition over Volcengine's streaming binary protocol."""

    def __init__(self, app_id: str, access_token: str, resource_id: str, url: str, timeout_s: float = 30.0):
        self.app_id = app_id
        self.access_token = access_token
        self.resource_id = resource_id
        self.url = url
        self.timeout_s = timeout_s

    async def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        if not pcm:
            raise VoiceProviderError("ASR received empty audio")
        request_id = uuid.uuid4().hex
        headers = {
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Access-Key": self.access_token,
            "X-Api-App-Key": self.app_id,
            "X-Api-Request-Id": request_id,
            "X-Api-Sequence": "-1",
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }
        try:
            async with websockets.connect(
                self.url,
                additional_headers=headers,
                open_timeout=self.timeout_s,
                close_timeout=2,
                max_size=4 * 1024 * 1024,
            ) as socket:
                await socket.send(self._full_request(sample_rate))
                chunks = [pcm[index : index + 3200] for index in range(0, len(pcm), 3200)]
                for chunk in chunks:
                    await socket.send(self._audio_frame(chunk))
                await socket.send(self._audio_frame(b"", last=True))
                best = ""
                while True:
                    parsed = self._parse(await asyncio.wait_for(socket.recv(), self.timeout_s))
                    self._raise_error(parsed)
                    payload = parsed.get("payload_msg") or {}
                    result = payload.get("result") or {}
                    text = str(result.get("text") or "").strip()
                    if text:
                        best = text
                    utterances = result.get("utterances") or []
                    definite = any(bool(item.get("definite")) for item in utterances if isinstance(item, dict))
                    if parsed.get("is_last_package") or definite:
                        return best
        except VoiceProviderError:
            raise
        except Exception as exc:
            response = getattr(exc, "response", None)
            if response is not None:
                raise _provider_http_error(
                    "ASR handshake",
                    int(getattr(response, "status_code", 0)),
                    getattr(response, "body", b""),
                ) from exc
            raise VoiceProviderError(f"ASR request failed: {type(exc).__name__}") from exc

    @staticmethod
    def _header(message_type: int, flags: int, serialization: int = 1, compression: int = 1) -> bytes:
        return bytes((0x11, (message_type << 4) | flags, (serialization << 4) | compression, 0x00))

    def _full_request(self, sample_rate: int) -> bytes:
        body = gzip.compress(json.dumps({
            "user": {"uid": "peacekeeper"},
            "audio": {"format": "pcm", "codec": "raw", "rate": sample_rate, "bits": 16, "channel": 1},
            "request": {
                "model_name": "bigmodel",
                "enable_nonstream": True,
                "enable_itn": True,
                "enable_punc": True,
                "show_utterances": True,
            },
        }, ensure_ascii=False).encode())
        return self._header(1, 0) + struct.pack(">I", len(body)) + body

    def _audio_frame(self, audio: bytes, last: bool = False) -> bytes:
        body = gzip.compress(audio)
        flags = 2 if last else 0
        return self._header(2, flags, serialization=0, compression=1) + struct.pack(">I", len(body)) + body

    @staticmethod
    def _parse(raw: str | bytes) -> dict[str, Any]:
        if isinstance(raw, str):
            raw = raw.encode()
        header_size = raw[0] & 0x0F
        message_type = raw[1] >> 4
        flags = raw[1] & 0x0F
        serialization = raw[2] >> 4
        compression = raw[2] & 0x0F
        payload = raw[header_size * 4 :]
        result: dict[str, Any] = {"is_last_package": bool(flags & 0x02)}
        if flags & 0x01:
            result["sequence"] = int.from_bytes(payload[:4], "big", signed=True)
            payload = payload[4:]
        if message_type == 15:
            result["code"] = int.from_bytes(payload[:4], "big")
            size = int.from_bytes(payload[4:8], "big")
            content = payload[8 : 8 + size]
        elif message_type == 9:
            size = int.from_bytes(payload[:4], "big")
            content = payload[4 : 4 + size]
        elif message_type == 11:
            if len(payload) < 8:
                return result
            result["ack_sequence"] = int.from_bytes(payload[:4], "big", signed=True)
            size = int.from_bytes(payload[4:8], "big")
            content = payload[8 : 8 + size]
        else:
            return result
        if compression == 1 and content:
            content = gzip.decompress(content)
        if serialization == 1 and content:
            result["payload_msg"] = json.loads(content.decode())
        elif content:
            result["payload_msg"] = content.decode(errors="replace")
        return result

    @staticmethod
    def _raise_error(parsed: dict[str, Any]) -> None:
        if "code" in parsed:
            raise VoiceProviderError(f"ASR provider error {parsed['code']}: {parsed.get('payload_msg', '')}")


class VolcTTSClient:
    def __init__(
        self,
        app_id: str,
        access_token: str,
        resource_id: str,
        voice: str,
        url: str,
        timeout_s: float = 30.0,
    ):
        self.voice = voice
        self.url = url
        self.client = httpx.AsyncClient(
            headers={
                "X-Api-App-Id": app_id,
                "X-Api-Access-Key": access_token,
                "X-Api-Resource-Id": resource_id,
                "Content-Type": "application/json",
            },
            timeout=timeout_s,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        payload = {
            "user": {"uid": "peacekeeper"},
            "req_params": {
                "text": text,
                "speaker": self.voice,
                "audio_params": {"format": "pcm", "sample_rate": 24000},
            },
        }
        try:
            async with self.client.stream(
                "POST",
                self.url,
                json=payload,
                headers={"X-Api-Request-Id": uuid.uuid4().hex},
            ) as response:
                if response.is_error:
                    raise _provider_http_error("TTS", response.status_code, await response.aread())
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    item = json.loads(line)
                    code = int(item.get("code", 0))
                    if code not in (0, 20000000):
                        raise VoiceProviderError(f"TTS provider error {code}: {item.get('message', '')}")
                    data = item.get("data")
                    if data:
                        yield base64.b64decode(data)
                    if code == 20000000:
                        break
        except VoiceProviderError:
            raise
        except Exception as exc:
            raise VoiceProviderError(f"TTS request failed: {type(exc).__name__}") from exc

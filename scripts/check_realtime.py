"""Check the server-proxied Realtime transcript path without a network API call."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import websockets

from smoke import find_free_port, http_request, wait_for_server


FAKE_OPENAI = '''
import asyncio
import json

class Event:
    def __init__(self, type, **values):
        self.type = type
        self.values = values
    def model_dump(self, mode="json"):
        return {"type": self.type, **self.values}

class Buffer:
    def __init__(self, connection): self.connection = connection
    async def append(self, audio): self.connection.audio += audio
    async def commit(self):
        assert self.connection.audio
        await self.connection.events.put(Event(
            "conversation.item.input_audio_transcription.delta", delta="Абай көшесінде су "))
        await self.connection.events.put(Event(
            "conversation.item.input_audio_transcription.completed",
            transcript="Абай көшесінде су жоқ", item_id="item-test"))

class Connection:
    def __init__(self):
        self.audio = ""
        self.events = asyncio.Queue()
        self.input_audio_buffer = Buffer(self)
    async def send_raw(self, data):
        assert json.loads(data)["session"]["type"] == "transcription"
    def __aiter__(self): return self
    async def __anext__(self): return await self.events.get()

class Manager:
    def __init__(self): self.connection = Connection()
    async def __aenter__(self): return self.connection
    async def __aexit__(self, *args): return None

class Realtime:
    def connect(self, **kwargs): return Manager()

class AsyncOpenAI:
    def __init__(self, **kwargs): self.realtime = Realtime()
'''


async def websocket_check(url: str):
    websocket_url = url.replace("http://", "ws://") + "/api/voice/realtime"
    async with websockets.connect(websocket_url) as socket:
        ready = json.loads(await socket.recv())
        assert ready["type"] == "pulse.ready"
        await socket.send(json.dumps({"type": "input_audio_buffer.append",
                                     "audio": base64.b64encode(b"\0\0" * 2400).decode()}))
        await socket.send(json.dumps({"type": "input_audio_buffer.commit"}))
        delta = json.loads(await socket.recv())
        completed = json.loads(await socket.recv())
        assert delta["type"].endswith(".delta") and delta["delta"]
        assert completed["transcript"] == "Абай көшесінде су жоқ"


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as temporary:
        fake_root = Path(temporary) / "fake"
        package = fake_root / "openai"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(FAKE_OPENAI)
        port = find_free_port()
        env = os.environ.copy()
        for name in ("P109_STT_BASE_URL", "P109_TTS_BASE_URL", "P109_COPILOT_BASE_URL"):
            env.pop(name, None)
        env.update({
            "PYTHONPATH": os.pathsep.join((str(fake_root), str(root))),
            "DATABASE_PATH": str(Path(temporary) / "realtime.db"),
            "P109_AUTH_DISABLED": "1", "P109_DEMO_MODE": "1",
            "OPENAI_API_KEY": "fixture", "P109_REALTIME_PROVIDER": "openai",
            "P109_OPENAI_REALTIME_MODEL": "gpt-live-transcribe",
        })
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
             "--port", str(port)], cwd=root, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        url = f"http://127.0.0.1:{port}"
        try:
            if not wait_for_server(url):
                stdout, stderr = process.communicate(timeout=3)
                raise RuntimeError(stdout.decode() + stderr.decode())
            status, health = http_request(url + "/api/voice/health")
            assert status == 200 and health["realtime"] == {
                "status": "configured", "provider": "openai", "model": "gpt-live-transcribe",
                "transport": "pulse_backend_proxy", "live_copilot_debounce_ms": 2500,
            }
            asyncio.run(websocket_check(url))
            status, finalized = http_request(url + "/api/voice/finalize-transcript", "POST", {
                "text": "Абай көшесінде су жоқ", "language": "kk", "field": "problem",
            })
            assert status == 200 and finalized["language"] == "kk"
            print("PASS: Realtime proxy streams partial/final KK text and finalizes without private STT")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    main()

"""Local OpenAI-compatible HTTP fixture: no real credentials or external service calls."""

import asyncio
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pydantic import BaseModel
from yoyu.runtime import DesktopRuntime
from yoyu.settings import DesktopSettings


class ProbeReply(BaseModel):
    ok: bool


class ModelClientLifecycleCheck(unittest.TestCase):
    def test_separate_runtime_event_loops_own_and_close_their_http_clients(self):
        requests = []

        class Endpoint(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                requests.append(self.path)
                time.sleep(0.03)  # Force an awaited socket read, as with a remote endpoint.
                body = json.dumps(
                    {
                        "id": "offline-completion",
                        "object": "chat.completion",
                        "created": 0,
                        "model": "offline-fixture",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": '{"ok":true}'},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:

                async def probe(index):
                    data = Path(directory) / str(index)
                    settings = DesktopSettings(
                        database_url=f"sqlite+aiosqlite:///{data}/runs.sqlite",
                        data_dir=data,
                        checkpoint_path=data / "checkpoints.sqlite",
                        openai_api_key="offline-placeholder-not-a-real-key",
                        openai_base_url=f"http://127.0.0.1:{server.server_port}/v1",
                        openai_model="offline-fixture",
                        openai_max_retries=0,
                        openai_timeout_seconds=2,
                    )
                    runtime = DesktopRuntime(settings)
                    await runtime.start()
                    try:
                        result = await runtime.model.structured(
                            ProbeReply,
                            system="Return the required boolean.",
                            user="ok=true",
                            fallback=ProbeReply(ok=False),
                        )
                        calls = runtime.model.call_count
                        errors = [r["error"] for r in runtime.model.call_records if r.get("error")]
                        client = getattr(runtime.model, "_http_client", None)
                    finally:
                        await runtime.close()
                    return result.ok, calls, errors, bool(client and client.is_closed)

                # Separate asyncio.run calls reproduce the event-loop boundary used by distinct TestClients.
                observed = [asyncio.run(probe(index)) for index in range(2)]
                self.assertEqual([row[1] for row in observed], [1, 1], observed)
                self.assertEqual([row[0] for row in observed], [True, True], observed)
                self.assertEqual([row[2] for row in observed], [[], []], observed)
                self.assertEqual([row[3] for row in observed], [True, True], observed)
                self.assertEqual(requests, ["/v1/chat/completions", "/v1/chat/completions"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

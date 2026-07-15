import base64
import io
import json
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from code_intelligence_agent.agents.llm_client import (
    LLMRequestError,
    OpenAICompatibleLLMClient,
)
from code_intelligence_agent.agents.llm_transport_worker import execute_request


def _payload() -> dict:
    return {
        "url": "https://provider.example/v1/chat/completions",
        "data_b64": base64.b64encode(b'{"model":"fixture"}').decode("ascii"),
        "headers": {
            "Authorization": "Bearer fixture-secret",
            "Content-Type": "application/json",
        },
        "timeout_seconds": 5,
    }


def test_worker_returns_success_envelope_without_echoing_request(monkeypatch):
    response_body = b'{"choices":[{"message":{"content":"ok"}}]}'

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback

        def read(self, size):
            del size
            if getattr(self, "consumed", False):
                return b""
            self.consumed = True
            return response_body

    def fake_urlopen(request, timeout):
        assert timeout == 5
        assert request.full_url == _payload()["url"]
        assert request.data == b'{"model":"fixture"}'
        assert request.get_header("Authorization") == "Bearer fixture-secret"
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = execute_request(_payload())

    assert result["status"] == "ok"
    assert base64.b64decode(result["body_b64"]) == response_body
    assert "fixture-secret" not in str(result)


def test_worker_preserves_http_status_and_bounded_error_body(monkeypatch):
    provider_body = b"provider-private-error"

    def fake_urlopen(request, timeout):
        del timeout
        raise urllib.error.HTTPError(
            request.full_url,
            429,
            "rate limited",
            {},
            io.BytesIO(provider_body),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = execute_request(_payload())

    assert result["status"] == "http_error"
    assert result["http_status"] == 429
    assert base64.b64decode(result["body_b64"]) == provider_body
    assert "fixture-secret" not in str(result)


def test_worker_classifies_network_failure_without_exception_text(monkeypatch):
    def fake_urlopen(request, timeout):
        del request, timeout
        raise urllib.error.URLError(ConnectionRefusedError("private endpoint"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = execute_request(_payload())

    assert result == {"status": "url_error", "reason": "connection_refused"}
    assert "private endpoint" not in str(result)


def test_worker_rejects_malformed_request_without_echoing_values():
    result = execute_request(
        {
            "url": "secret-url",
            "data_b64": "not-base64",
            "headers": {"Authorization": "Bearer fixture-secret"},
            "timeout_seconds": 5,
        }
    )

    assert result == {"status": "worker_error", "reason": "invalid_request"}
    assert "fixture-secret" not in str(result)


def test_real_worker_process_is_terminated_by_total_deadline(monkeypatch):
    request_seen = threading.Event()

    class SlowHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            request_seen.set()
            time.sleep(3)
            response = json.dumps(
                {"choices": [{"message": {"content": "too late"}}]}
            ).encode("utf-8")
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, format, *args):
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    client = OpenAICompatibleLLMClient(
        provider="deepseek",
        api_key="fixture-isolated-process-key",
        model="deepseek-v4-pro",
        base_url=f"http://127.0.0.1:{server.server_port}/chat/completions",
        timeout=1,
        isolate_request_timeout=True,
    )
    started_at = time.perf_counter()
    try:
        client.complete("repair")
    except LLMRequestError as exc:
        elapsed = time.perf_counter() - started_at
        assert exc.reason == "timeout"
        assert exc.metadata["request_timeout_mode"] == "isolated_total"
        assert elapsed < 2.5
        assert "fixture-isolated-process-key" not in json.dumps(exc.metadata)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("Expected LLMRequestError")
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
    assert request_seen.is_set()

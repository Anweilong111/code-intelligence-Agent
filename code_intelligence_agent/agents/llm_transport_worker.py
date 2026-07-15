from __future__ import annotations

import base64
import http.client
import json
import socket
import sys
import urllib.error
import urllib.request
from typing import Any


MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_ERROR_BODY_BYTES = 64 * 1024


def execute_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute one HTTP request without exposing request content in diagnostics."""
    try:
        url = _required_text(payload, "url")
        data = _decode_base64(payload.get("data_b64"))
        headers = _headers(payload.get("headers"))
        timeout_seconds = _positive_float(payload.get("timeout_seconds"))
    except (TypeError, ValueError):
        return {"status": "worker_error", "reason": "invalid_request"}

    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = _read_limited(response, MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as exc:
        return {
            "status": "http_error",
            "http_status": int(exc.code),
            "body_b64": _encode_base64(_read_error_body(exc)),
        }
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return {"status": "timeout", "reason": "timeout"}
        return {
            "status": "url_error",
            "reason": _network_error_reason(reason),
        }
    except (TimeoutError, socket.timeout):
        return {"status": "timeout", "reason": "timeout"}
    except (http.client.HTTPException, OSError) as exc:
        return {
            "status": "transport_error",
            "reason": _transport_error_reason(exc),
        }
    except Exception:  # pragma: no cover - final containment boundary
        return {"status": "worker_error", "reason": "unexpected_worker_error"}
    return {"status": "ok", "body_b64": _encode_base64(body)}


def main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request payload must be an object")
        result = execute_request(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        result = {"status": "worker_error", "reason": "invalid_request"}
    serialized = json.dumps(
        result,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    sys.stdout.buffer.write(serialized)
    sys.stdout.buffer.flush()
    return 0


def _read_limited(response: Any, maximum_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(64 * 1024)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > maximum_bytes:
            raise _ResponseTooLargeError
        chunks.append(bytes(chunk))


def _read_error_body(exc: urllib.error.HTTPError) -> bytes:
    try:
        return bytes(exc.read(MAX_ERROR_BODY_BYTES))
    except (OSError, http.client.HTTPException):
        return b""


def _headers(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise TypeError("headers must be an object")
    headers = {str(name): str(content) for name, content in value.items()}
    if not headers:
        raise ValueError("headers must not be empty")
    return headers


def _required_text(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is required")
    return value


def _positive_float(value: Any) -> float:
    result = float(value)
    if result <= 0:
        raise ValueError("timeout must be positive")
    return result


def _decode_base64(value: Any) -> bytes:
    if not isinstance(value, str):
        raise TypeError("base64 value must be text")
    return base64.b64decode(value.encode("ascii"), validate=True)


def _encode_base64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _network_error_reason(reason: Any) -> str:
    if isinstance(reason, ConnectionRefusedError):
        return "connection_refused"
    if isinstance(reason, ConnectionResetError):
        return "connection_reset"
    if isinstance(reason, socket.gaierror):
        return "name_resolution_error"
    if isinstance(reason, OSError):
        return "network_unavailable"
    return "url_error"


def _transport_error_reason(exc: BaseException) -> str:
    if isinstance(exc, _ResponseTooLargeError):
        return "response_too_large"
    if isinstance(exc, http.client.IncompleteRead):
        return "incomplete_read"
    if isinstance(exc, http.client.RemoteDisconnected):
        return "remote_disconnected"
    if isinstance(exc, ConnectionResetError):
        return "connection_reset"
    if isinstance(exc, ConnectionAbortedError):
        return "connection_aborted"
    if isinstance(exc, ConnectionRefusedError):
        return "connection_refused"
    if isinstance(exc, BrokenPipeError):
        return "broken_pipe"
    return "transport_error"


class _ResponseTooLargeError(OSError):
    pass


if __name__ == "__main__":
    raise SystemExit(main())

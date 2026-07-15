from __future__ import annotations

import os


def _positive_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _apply_posix_limits() -> None:
    try:
        import resource
    except ImportError:
        return
    limits = (
        (resource.RLIMIT_CPU, _positive_int("CIA_RUNTIME_CPU_SECONDS", 30)),
        (
            resource.RLIMIT_AS,
            _positive_int("CIA_RUNTIME_MEMORY_MB", 1024) * 1024 * 1024,
        ),
        (
            resource.RLIMIT_FSIZE,
            _positive_int("CIA_RUNTIME_MAX_FILE_MB", 128) * 1024 * 1024,
        ),
    )
    for resource_id, limit in limits:
        try:
            resource.setrlimit(resource_id, (limit, limit))
        except (OSError, ValueError):
            continue


def _deny_python_network() -> None:
    if os.environ.get("CIA_RUNTIME_NETWORK_POLICY") != "deny":
        return
    import socket

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create_connection = socket.create_connection

    def is_loopback(address) -> bool:
        if isinstance(address, str):
            return True
        if not isinstance(address, tuple) or not address:
            return False
        host = str(address[0]).strip().lower()
        return host in {"127.0.0.1", "::1", "localhost"}

    def guarded_connect(sock, address):
        if is_loopback(address):
            return original_connect(sock, address)
        raise PermissionError("CIA runtime policy blocks repository network access")

    def guarded_connect_ex(sock, address):
        if is_loopback(address):
            return original_connect_ex(sock, address)
        return 13

    def guarded_create_connection(address, *args, **kwargs):
        if is_loopback(address):
            return original_create_connection(address, *args, **kwargs)
        raise PermissionError("CIA runtime policy blocks repository network access")

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.create_connection = guarded_create_connection


_apply_posix_limits()
_deny_python_network()

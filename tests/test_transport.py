"""call_ue against a real loopback TCP server (no UE needed).

test_bridge.py covers call_ue with a mocked `socket.socket`. These tests stand
up a tiny fake UE server on 127.0.0.1 instead, so the timeout and framing paths
run through the real socket stack: an editor that accepts the connection but
is still working, a server that sends a broken frame, and socket cleanup.
"""

import socket
import threading

import pytest

import unreal_ai_connection_bridge as bridge


class _FakeUE:
    """One-shot loopback server. `behaviour(conn)` runs on the accepted socket."""

    def __init__(self, behaviour):
        self._srv = socket.socket()
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.port = self._srv.getsockname()[1]
        self._behaviour = behaviour
        self._release = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            conn, _ = self._srv.accept()
        except OSError:
            return
        with conn:
            self._behaviour(conn, self._release)

    def close(self):
        self._release.set()
        self._srv.close()
        self._thread.join(timeout=5)


def _read_request(conn):
    length = int.from_bytes(bridge.recv_exact(conn, 8), "big")
    return bridge.recv_exact(conn, length)


@pytest.fixture
def fake_ue(monkeypatch):
    servers = []

    def start(behaviour, timeout_s=0.5):
        srv = _FakeUE(behaviour)
        servers.append(srv)
        monkeypatch.setattr(bridge, "UE_HOST", "127.0.0.1")
        monkeypatch.setattr(bridge, "UE_PORT", srv.port)
        monkeypatch.setattr(bridge, "UE_TIMEOUT_S", timeout_s)
        return srv

    yield start
    for srv in servers:
        srv.close()


def test_busy_editor_is_not_reported_as_not_running(fake_ue):
    """The editor accepted the request but hasn't answered within the timeout:
    that is a busy editor, not a missing one. The error must say the request
    was sent and may still apply, and must not tell the agent to open UE."""

    def never_answers(conn, release):
        _read_request(conn)
        release.wait(5)

    fake_ue(never_answers)
    resp = bridge.call_ue("build_lighting", {})

    err = resp["error"]
    assert err["code"] == bridge.UE_BUSY_TIMEOUT_CODE
    assert err["error_code"] == "ue_busy_timeout"
    assert "not reachable" not in err["message"]
    assert "Open the UE editor" not in err["message"]
    assert "may still" in err["message"]


def test_refused_connection_is_still_not_reachable(monkeypatch):
    # Reserve a free port and release it without ever listening, so the
    # connect is refused. (Closing a listener whose accept() is still blocked
    # in another thread doesn't free the port on Linux: the connect would
    # succeed and then reset.)
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    monkeypatch.setattr(bridge, "UE_HOST", "127.0.0.1")
    monkeypatch.setattr(bridge, "UE_PORT", port)

    resp = bridge.call_ue("list_tools", {})

    assert resp["error"]["code"] == -32099
    assert "not reachable" in resp["error"]["message"]


def test_zero_length_frame_returns_protocol_error_instead_of_raising(fake_ue):
    def zero_frame(conn, release):
        _read_request(conn)
        conn.sendall((0).to_bytes(8, "big"))

    fake_ue(zero_frame)
    resp = bridge.call_ue("list_tools", {})

    assert resp["error"]["code"] == -32700
    assert "framing_error" in resp["error"]["message"]


def test_oversized_frame_returns_protocol_error_instead_of_raising(fake_ue):
    def huge_frame(conn, release):
        _read_request(conn)
        conn.sendall((2 * 1024 * 1024 * 1024).to_bytes(8, "big"))

    fake_ue(huge_frame)
    resp = bridge.call_ue("list_tools", {})

    assert resp["error"]["code"] == -32700
    assert "framing_error" in resp["error"]["message"]


def test_socket_is_closed_when_the_frame_is_bad(monkeypatch):
    """No socket may leak on an exception path."""
    closed = []

    class _Sock:
        def settimeout(self, _t):
            pass

        def connect(self, _addr):
            pass

        def sendall(self, _b):
            pass

        def recv(self, _n):
            return (0).to_bytes(8, "big")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(socket, "socket", lambda *a, **k: _Sock())
    bridge.call_ue("list_tools", {})
    assert closed == [True]


def test_socket_is_closed_when_the_editor_times_out(monkeypatch):
    closed = []

    class _Sock:
        def settimeout(self, _t):
            pass

        def connect(self, _addr):
            pass

        def sendall(self, _b):
            pass

        def recv(self, _n):
            raise TimeoutError("timed out")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(socket, "socket", lambda *a, **k: _Sock())
    resp = bridge.call_ue("list_tools", {})
    assert resp["error"]["error_code"] == "ue_busy_timeout"
    assert closed == [True]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("120", 120.0), ("2.5", 2.5), ("", 30.0), ("abc", 30.0), ("0", 30.0), ("-5", 30.0)],
)
def test_ucmcp_timeout_env_is_parsed_with_a_safe_default(raw, expected):
    assert bridge._parse_timeout_env(raw) == expected

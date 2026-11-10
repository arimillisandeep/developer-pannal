"""Individual safe probes for every registered API method and WebSocket."""
import re
from uuid import UUID

import pytest
from starlette.websockets import WebSocketDisconnect

from app.main import app


_DOCUMENTATION_PATHS = {"/api/openapi.json", "/api/docs", "/api/redoc"}
_API_METHODS = []
_WEBSOCKET_PATHS = []

for _route in app.routes:
    _path = getattr(_route, "path", "")
    if "WebSocket" in type(_route).__name__:
        _WEBSOCKET_PATHS.append(_path)
        continue
    if not _path.startswith("/api/") or _path in _DOCUMENTATION_PATHS:
        continue
    for _method in sorted(getattr(_route, "methods", set()) or set()):
        if _method not in {"OPTIONS", "HEAD"} or _path.endswith("/devproxy/{server_id}/{token}/{path:path}"):
            _API_METHODS.append((_method, _path))


def _probe_path(path: str) -> str:
    """Replace route parameters with deterministic nonexistent values."""
    def replace(match: re.Match[str]) -> str:
        name, converter = match.group(1), match.group(2)
        if converter == "path":
            return "__codex_missing_probe_file__"
        if name == "version_no":
            return "999999"
        if name == "token":
            return "invalid-token"
        return str(UUID(int=0))

    return re.sub(r"\{([^}:]+)(?::([^}]+))?\}", replace, path)


@pytest.mark.parametrize(
    ("method", "route_path"),
    _API_METHODS,
    ids=[f"{method}-{path}" for method, path in _API_METHODS],
)
def test_each_registered_http_operation_has_a_safe_error_or_health_response(
    client, method, route_path, record_property
):
    """Exercise the actual route with no credentials and no mutating payload."""
    response = client.request(method, _probe_path(route_path))
    record_property("route", route_path)
    record_property("method", method)
    record_property("probe_status", response.status_code)
    record_property("content_type", response.headers.get("content-type", ""))

    assert response.status_code != 405, f"{method} {route_path} was registered but returned 405"
    assert response.status_code < 500, (
        f"{method} {route_path} returned {response.status_code}: {response.text[:300]}"
    )
    if method != "HEAD" and "application/json" in response.headers.get("content-type", ""):
        response.json()


@pytest.mark.parametrize("route_path", _WEBSOCKET_PATHS, ids=_WEBSOCKET_PATHS)
def test_each_websocket_rejects_an_invalid_token(client, route_path, record_property):
    path = _probe_path(route_path)
    if "?" not in path:
        path += "?token=invalid-token"
    else:
        path += "&token=invalid-token"
    with pytest.raises(WebSocketDisconnect) as disconnected:
        with client.websocket_connect(path):
            pass
    record_property("route", route_path)
    record_property("invalid_token_close_code", disconnected.value.code)
    assert disconnected.value.code in {1008, 4401, 4403}


def test_authenticated_notification_websocket_transmits_and_receives(client, dev_token):
    with client.websocket_connect(f"/ws?token={dev_token}") as socket:
        ready = socket.receive_json()
        assert ready["channel"] == "ready"
        socket.send_json({"action": "ping"})
        assert socket.receive_json()["channel"] == "pong"


def test_monitoring_websocket_rejects_non_admin_role(client, dev_token):
    with pytest.raises(WebSocketDisconnect) as disconnected:
        with client.websocket_connect(
            f"/api/monitoring/ws/monitoring?token={dev_token}"
        ):
            pass
    assert disconnected.value.code == 4403

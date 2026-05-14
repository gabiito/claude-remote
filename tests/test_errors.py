"""Red tests for the error_response helper — WU-5."""

from claude_remote.api.errors import error_response


def test_error_response_shape_no_details() -> None:
    """Default shape: status 400, body with code and message, no 'details' key."""
    resp = error_response(code="foo", message="bar", status_code=400)
    assert resp.status_code == 400
    body = resp.body
    import json

    data = json.loads(body)
    assert data == {"error": {"code": "foo", "message": "bar"}}
    assert "details" not in data["error"]


def test_error_response_with_details() -> None:
    resp = error_response(code="foo", message="bar", status_code=400, details={"x": 1})
    import json

    data = json.loads(resp.body)
    assert data["error"]["details"] == {"x": 1}


def test_error_response_custom_status_code() -> None:
    resp = error_response(code="conflict", message="already exists", status_code=409)
    assert resp.status_code == 409


def test_error_response_404() -> None:
    resp = error_response(code="not_found", message="not found", status_code=404)
    assert resp.status_code == 404
    import json

    data = json.loads(resp.body)
    assert data["error"]["code"] == "not_found"

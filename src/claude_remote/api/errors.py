"""Structured error response helper.

Every API error uses the envelope:
    {"error": {"code": str, "message": str, "details": dict | null}}

`details` is omitted (not included in the body at all) when None.

This module will be imported by every router in every future slice
(mvp-tmux-launcher, mvp-hooks-receiver, etc.). Placing it here avoids
an extraction diff in each of those slices.
"""

from fastapi.responses import JSONResponse


def error_response(
    code: str,
    message: str,
    status_code: int,
    details: dict[str, object] | None = None,
) -> JSONResponse:
    """Build a structured error JSONResponse.

    Args:
        code: machine-readable error code string.
        message: human-readable error description.
        status_code: HTTP status code to return.
        details: optional dict of additional context; omitted when None.

    Returns:
        JSONResponse with the structured error envelope.
    """
    error_body: dict[str, object] = {"code": code, "message": message}
    if details is not None:
        error_body["details"] = details
    return JSONResponse(status_code=status_code, content={"error": error_body})

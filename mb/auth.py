"""API key check: Travezy's backend sends X-API-Key on every call.

The key is read from MB_API_KEY (in production, from Travezy's secrets).
If it isn't set, the service refuses every request except /health: it fails
closed, so a missing setting never leaves it open. /preview also accepts
?key=..., so the phone preview can be opened in a browser.
"""
import os
import secrets

from fastapi import Header, HTTPException, Query


def require_key(x_api_key: str | None = Header(default=None), key: str | None = Query(default=None)):
    expected = os.environ.get("MB_API_KEY", "")
    if not expected:
        raise HTTPException(503, "MB_API_KEY is not set, so the service refuses requests.")
    given = x_api_key or key or ""
    if not secrets.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(401, "Missing or wrong API key (send it as the X-API-Key header).")

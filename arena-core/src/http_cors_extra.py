"""Shared HTTP middleware tweaks for browser dev clients (Ghost Signal on localhost)."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class PrivateNetworkAclMiddleware(BaseHTTPMiddleware):
    """
    Chrome may send Access-Control-Request-Private-Network on cross-port localhost requests.
    Without Access-Control-Allow-Private-Network, the browser can surface generic CORS errors.
    Safe for public APIs — only signals consent for the local preflight handshake.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Access-Control-Allow-Private-Network", "true")
        return response

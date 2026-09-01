"""Browser dev tweaks (Ghost Signal crosses origins to this service)."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class PrivateNetworkAclMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Access-Control-Allow-Private-Network", "true")
        return response

from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware, MiddlewareContext

from settings import settings


class ApiKeyMiddleware(Middleware):
    async def on_request(self, context: MiddlewareContext, call_next):
        headers = get_http_headers(include_all=True)
        if not headers:
            return await call_next(context)

        provided = ""

        direct = headers.get("x-api-key")
        if direct:
            provided = direct.strip()

        parts = headers.get("authorization", "").split(" ", 1)
        expected_parts = 2
        if len(parts) == expected_parts and parts[0].lower() == "bearer":
            provided = parts[1].strip()

        if provided != settings.api_key:
            msg = "UNAUTHORIZED: invalid api key"
            raise AuthorizationError(msg)

        return await call_next(context)

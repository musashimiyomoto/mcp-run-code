import re
from collections.abc import Mapping

from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware, MiddlewareContext

from constants import BEARER_REGEX
from settings import settings


class ApiKeyMiddleware(Middleware):
    @staticmethod
    def _extract_bearer_token(headers: Mapping[str, str]) -> str | None:
        auth_header = headers.get("authorization", "").strip()

        match = re.match(pattern=BEARER_REGEX, string=auth_header, flags=re.IGNORECASE)
        if match is None:
            return None

        return match.group(1)

    async def on_request(self, context: MiddlewareContext, call_next):
        provided = self._extract_bearer_token(headers=get_http_headers(include_all=True) or {})

        if provided is None:
            msg = "UNAUTHORIZED: missing bearer token"
            raise AuthorizationError(msg)

        if provided != settings.api_key:
            msg = "UNAUTHORIZED: invalid api key"
            raise AuthorizationError(msg)

        return await call_next(context)

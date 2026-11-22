"""What every part of the doubles' HTTP plumbing shares (TEST DOUBLES).

Header names, the body limit, the ASGI types, the scope keys of the request id and of a
5xx response's facts, and the route helpers.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.routing import APIRoute

REQUEST_ID_HEADER = "X-Request-ID"
TEST_CONTROL_HEADER = "X-Test-Control-Key"
MAX_BODY_BYTES = 1_048_576
HTTP_NOT_FOUND = 404
HTTP_METHOD_NOT_ALLOWED = 405
HTTP_SERVER_ERROR = 500
# Keys of the ASGI scope under which the middleware keeps the request id and a 5xx's facts.
REQUEST_ID_KEY = "onepledge.request_id"
FAILURE_KEY = "onepledge.failure"

type Scope = MutableMapping[str, Any]
type Message = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type AsgiApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def request_id_of(request: Request) -> str:
    """The request id bound by the middleware."""
    value = request.scope.get(REQUEST_ID_KEY)
    return value if isinstance(value, str) else str(uuid.uuid4())


def route_template(request: Request) -> str:
    """The route template a request matched (for example `/v1/certificates/{certificate_id}`)."""
    route = request.scope.get("route")
    return route.path if isinstance(route, APIRoute) else request.url.path


def contract_routes(app: FastAPI) -> frozenset[str]:
    """The route templates of the contract endpoints of an app (no `/healthz`, no `/_test/`)."""
    return frozenset(
        route.path
        for route in app.routes
        if isinstance(route, APIRoute) and route.path != "/healthz" and not route.path.startswith("/_test/")
    )

from __future__ import annotations

import logging
import re
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .database import Database, utc_now

logger = logging.getLogger("comfy_gateway.visitors")
VISITOR_COOKIE = "cg_visitor"
VISITOR_MAX_AGE_SECONDS = 180 * 24 * 60 * 60
_VISITOR_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
_SITE_VISIT_PATHS = {"/", "/gallery"}
_IDENTIFIED_PATHS = {
    "/",
    "/gallery",
    "/api/submit",
    "/api/query",
    "/api/result",
    "/api/publish",
    "/api/destroy",
}


class VisitorMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, db: Database):
        super().__init__(app)
        self.db = db

    async def dispatch(self, request: Request, call_next):
        request.state.visitor_id = None
        request.state.visitor_established = False
        path = request.url.path
        identify = request.method != "OPTIONS" and (
            path in _IDENTIFIED_PATHS
            or (
                path.startswith("/api/gallery")
                and not path.endswith(("/image", "/thumbnail"))
            )
        )
        new_visitor = False
        if identify:
            visitor_id = request.cookies.get(VISITOR_COOKIE)
            established = bool(
                visitor_id
                and _VISITOR_PATTERN.match(visitor_id)
                and self.db.visitor_exists(visitor_id)
            )
            if not established:
                visitor_id = secrets.token_urlsafe(32)
                self.db.create_visitor(visitor_id, utc_now())
                new_visitor = True
            request.state.visitor_id = visitor_id
            request.state.visitor_established = established

        response = await call_next(request)
        if (
            request.method == "GET"
            and path in _SITE_VISIT_PATHS
            and response.status_code < 400
        ):
            try:
                self.db.record_site_visit(
                    visitor_id=request.state.visitor_id,
                    path=path,
                    created_at=utc_now(),
                    client_ip=request.client.host if request.client else None,
                )
            except Exception:
                logger.exception("could not record site visit for %s", path)
        if new_visitor:
            response.set_cookie(
                VISITOR_COOKIE,
                request.state.visitor_id,
                max_age=VISITOR_MAX_AGE_SECONDS,
                httponly=True,
                samesite="lax",
                secure=request.url.scheme == "https",
                path="/",
            )
        return response

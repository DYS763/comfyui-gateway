from __future__ import annotations

from email.utils import parsedate
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse
from starlette.staticfiles import NotModifiedResponse


def _is_not_modified(request: Request, response: FileResponse) -> bool:
    request_headers = request.headers
    response_headers = response.headers
    try:
        if_none_match = request_headers["if-none-match"]
        etag = response_headers["etag"]
        if etag in [tag.strip(" W/") for tag in if_none_match.split(",")]:
            return True
    except KeyError:
        pass
    try:
        if_modified_since = parsedate(request_headers["if-modified-since"])
        last_modified = parsedate(response_headers["last-modified"])
        if (
            if_modified_since is not None
            and last_modified is not None
            and if_modified_since >= last_modified
        ):
            return True
    except KeyError:
        pass
    return False


def cached_file_response(
    request: Request,
    path: Path,
    *,
    media_type: str,
    cache_control: str,
    filename: str | None = None,
) -> FileResponse | NotModifiedResponse:
    response = FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers={"Cache-Control": cache_control},
        stat_result=path.stat(),
    )
    if _is_not_modified(request, response):
        return NotModifiedResponse(response.headers)
    return response

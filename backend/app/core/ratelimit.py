"""Tiny in-process sliding-window rate limiter (per IP + path prefix).

Production note: behind multiple workers/instances put Redis here instead.
"""
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from app.core.config import settings

_hits: dict[str, deque] = defaultdict(deque)
WINDOW = 60.0


async def rate_limit_middleware(request: Request, call_next):
    if request.url.path.startswith(f"{settings.API_PREFIX}/auth/login"):
        limit = settings.LOGIN_RATE_LIMIT
    else:
        limit = settings.RATE_LIMIT_PER_MINUTE

    ip = request.client.host if request.client else "unknown"
    key = f"{ip}:{request.url.path.split('/')[1:3]}"
    now = time.time()
    bucket = _hits[key]
    while bucket and now - bucket[0] > WINDOW:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="Too many requests. Please slow down.")
    bucket.append(now)
    return await call_next(request)

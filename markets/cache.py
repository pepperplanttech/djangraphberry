from .exceptions import NotFoundError
import hashlib
import json
from functools import wraps
from django.conf import settings
from django.core.cache import cache
from django.utils.cache import patch_cache_control
import time
from contextlib import contextmanager
from contextvars import ContextVar

# --- Caching ---

_MISSING = object()
_NOT_FOUND = "__markets_not_found__"
_freshness: ContextVar[list[float] | None] = ContextVar("markets_freshness", default=None)

def _cache_key(name: str, args: tuple, kwargs: dict) -> str:
    payload = json.dumps([args, sorted(kwargs.items())], default=str, sort_keys=True)
    return f"markets:{name}:{hashlib.sha256(payload.encode()).hexdigest()[:16]}"


def cached(setting_name: str):
    """Cache a fetch function's parsed result, keyed on its arguments.

    A NotFoundError is cached briefly so repeated bad lookups don't reach the
    provider. UpstreamError is never cached: a failure must not stick.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = _cache_key(func.__name__, args, kwargs)
            hit = cache.get(key, _MISSING)

            if hit is not _MISSING:
                value, expires_at = hit
                _record_expiry(expires_at)
                if isinstance(value, str) and value == _NOT_FOUND:
                    raise NotFoundError(func.__name__)
                return value

            timeout = getattr(settings, setting_name)
            try:
                value = func(*args, **kwargs)
            except NotFoundError:
                timeout = settings.NOT_FOUND_CACHE_SECONDS
                expires_at = time.time() + timeout
                cache.set(key, (_NOT_FOUND, expires_at), timeout)
                _record_expiry(expires_at)
                raise

            expires_at = time.time() + timeout
            cache.set(key, (value, expires_at), timeout)
            _record_expiry(expires_at)
            return value

        return wrapper

    return decorator


# --- HTTP cache headers ---

class CacheControlMixin:
    """Advertise a JSON response's freshness using the same TTL as the server-side cache.

    Set `cache_seconds_setting` to the name of a settings constant. Anything
    that isn't a successful, safe, JSON response is marked `no-store`.
    """

    cache_seconds_setting: str | None = None

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)

        renderer = getattr(response, "accepted_renderer", None)
        cacheable = (
            self.cache_seconds_setting
            and getattr(renderer, "format", None) == "json"
            and request.method in ("GET", "HEAD")
            and response.status_code < 400
        )

        if cacheable:
            max_age = remaining_max_age()
            if max_age is None:
                max_age = getattr(settings, self.cache_seconds_setting)
            patch_cache_control(response, public=True, max_age=max_age)
        else:
            patch_cache_control(response, no_store=True)

        return response
    
# --- Per-request freshness tracking ---

@contextmanager
def track_freshness():
    """Collect the expiry time of every cache entry used while handling one request."""
    token = _freshness.set([])
    try:
        yield
    finally:
        _freshness.reset(token)


def _record_expiry(expires_at: float) -> None:
    ledger = _freshness.get()
    if ledger is not None:
        ledger.append(expires_at)


def remaining_max_age() -> int | None:
    """Seconds until the soonest-expiring entry this request relied on, or None."""
    ledger = _freshness.get()
    if not ledger:
        return None
    return max(0, int(min(ledger) - time.time()))


class FreshnessMiddleware:
    """Scope the freshness ledger to one request and advertise the result.

    Views that set their own Cache-Control (the REST endpoints) are left alone;
    this fills in the header for everything else, notably the GraphQL endpoint.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        with track_freshness():
            response = self.get_response(request)
            if "Cache-Control" not in response.headers:
                max_age = remaining_max_age()
                if max_age is not None:
                    patch_cache_control(response, max_age=max_age)
            return response
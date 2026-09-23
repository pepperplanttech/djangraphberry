from .exceptions import NotFoundError
import hashlib
import json
from functools import wraps
from django.conf import settings
from django.core.cache import cache
from django.utils.cache import patch_cache_control

# --- Caching ---

_MISSING = object()
_NOT_FOUND = "__markets_not_found__"


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

            if isinstance(hit, str) and hit == _NOT_FOUND:
                raise NotFoundError(func.__name__)
            if hit is not _MISSING:
                return hit

            try:
                value = func(*args, **kwargs)
            except NotFoundError:
                cache.set(key, _NOT_FOUND, settings.NOT_FOUND_CACHE_SECONDS)
                raise

            cache.set(key, value, getattr(settings, setting_name))
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
            max_age = getattr(settings, self.cache_seconds_setting)
            patch_cache_control(response, public=True, max_age=max_age)
        else:
            patch_cache_control(response, no_store=True)

        return response
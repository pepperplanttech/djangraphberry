"""Fake upstream providers.

The stub replaces `httpx.get`, the outermost boundary of the project, so tests
exercise the real URL building, status handling, parsing and caching in
`markets.clients` rather than skipping past them.
"""
from contextlib import contextmanager
from unittest.mock import patch

CURRENCIES = {
    "USD": "United States Dollar",
    "EUR": "Euro",
    "GBP": "British Pound Sterling",
}

RATES = {"EUR": 0.87237, "GBP": 0.74832, "USD": 1.0}

COINS = {
    "bitcoin": {"usd": 85457.0, "usd_24h_change": -0.59, "eur": 74550.12},
    "ethereum": {"usd": 3125.5, "usd_24h_change": 1.204},
    "flatline": {"usd": 1.0, "usd_24h_change": None},
}


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    @property
    def is_error(self):
        return self.status_code >= 400

    def json(self):
        return self._payload


class FakeUpstream:
    """Routes a request to a canned payload and counts the calls."""

    def __init__(self):
        self.calls = []
        self.fail_with = None

    def __call__(self, url, params=None, timeout=None):
        self.calls.append((url, params or {}))
        if self.fail_with is not None:
            raise self.fail_with

        if url.endswith("/currencies"):
            return FakeResponse(200, CURRENCIES)

        if url.endswith("/latest"):
            base = params["base"]
            if base not in CURRENCIES:
                return FakeResponse(404)
            wanted = params["symbols"].split(",") if params.get("symbols") else list(RATES)
            if any(code not in RATES for code in wanted):
                return FakeResponse(404)
            # Frankfurter quotes relative to the requested base.
            return FakeResponse(200, {
                "base": base,
                "date": "2026-09-22",
                "rates": {code: RATES[code] / RATES[base] for code in wanted if code != base},
            })

        if url.endswith("/simple/price"):
            coin = COINS.get(params["ids"], {})
            currency = params["vs_currencies"]
            if currency not in coin:
                return FakeResponse(200, {})  # CoinGecko's answer for an unknown pair
            body = {currency: coin[currency]}
            if coin.get(f"{currency}_24h_change") is not None:
                body[f"{currency}_24h_change"] = coin[f"{currency}_24h_change"]
            return FakeResponse(200, {params["ids"]: body})

        raise AssertionError(f"Unexpected upstream request: {url}")

    def count(self, fragment):
        """How many requests hit a path containing `fragment`."""
        return sum(1 for url, _ in self.calls if fragment in url)


@contextmanager
def upstream():
    """Install the fake and hand it back so tests can assert on its calls."""
    fake = FakeUpstream()
    with patch("markets.clients.httpx.get", side_effect=fake):
        yield fake

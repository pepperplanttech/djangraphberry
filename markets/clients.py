"""Thin wrappers around the external APIs.

Everything else in the project calls these functions, never httpx directly.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import httpx
from django.conf import settings


class UpstreamError(Exception):
    """The upstream API was unreachable or returned an error."""


class NotFoundError(Exception):
    """The requested resource doesn't exist upstream."""


@dataclass(frozen=True)
class ExchangeRates:
    base: str
    date: date
    rates: dict[str, Decimal]


@dataclass(frozen=True)
class CryptoPrice:
    coin_id: str
    currency: str
    price: Decimal
    change_24h_percent: Decimal | None


def _get_json(base_url: str, path: str, params: dict | None = None):
    try:
        response = httpx.get(
            f"{base_url}{path}",
            params=params,
            timeout=settings.UPSTREAM_TIMEOUT_SECONDS,
        )
    except httpx.RequestError as exc:
        raise UpstreamError(f"Could not reach {base_url}") from exc

    if response.status_code == 404:
        raise NotFoundError(path)
    if response.is_error:
        raise UpstreamError(f"{base_url} returned HTTP {response.status_code}")
    return response.json()


# --- Frankfurter (fiat exchange rates) ---

def fetch_currencies() -> dict[str, str]:
    """Return {"USD": "United States Dollar", ...}."""
    return _get_json(settings.FRANKFURTER_BASE_URL, "/currencies")


def fetch_latest_rates(base: str = "EUR", symbols: list[str] | None = None) -> ExchangeRates:
    params = {"base": base.upper()}
    if symbols:
        params["symbols"] = ",".join(s.upper() for s in symbols)

    data = _get_json(settings.FRANKFURTER_BASE_URL, "/latest", params)
    return ExchangeRates(
        base=data["base"],
        date=date.fromisoformat(data["date"]),
        rates={code: Decimal(str(rate)) for code, rate in data["rates"].items()},
    )


# --- CoinGecko (crypto prices) ---

def fetch_crypto_price(coin_id: str, currency: str = "usd") -> CryptoPrice:
    coin_id, currency = coin_id.lower(), currency.lower()
    data = _get_json(
        settings.COINGECKO_BASE_URL,
        "/simple/price",
        {"ids": coin_id, "vs_currencies": currency, "include_24hr_change": "true"},
    )

    # CoinGecko answers 200 with an empty object for unknown coins/currencies.
    coin = data.get(coin_id) or {}
    if currency not in coin:
        raise NotFoundError(coin_id)

    change = coin.get(f"{currency}_24h_change")
    return CryptoPrice(
        coin_id=coin_id,
        currency=currency,
        price=Decimal(str(coin[currency])),
        change_24h_percent=Decimal(str(change)) if change is not None else None,
    )
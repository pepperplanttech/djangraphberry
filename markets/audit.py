"""Request-scoped audit log of price lookups.

Lookups are collected while a request is handled and written in a single query
at the end of it. GraphQL resolves `converted` as a child of `cryptoPrice`, so
the conversion is not known at the moment the price is; collecting first and
writing last lets one lookup produce one row instead of two.
"""
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from django.utils import timezone

from .models import PriceLookup

logger = logging.getLogger(__name__)

_lookups: ContextVar[list["_Lookup"] | None] = ContextVar("markets_lookups", default=None)


@dataclass
class _Lookup:
    source: str
    coin_id: str
    base_currency: str
    price: Decimal
    change_24h_percent: Decimal | None
    conversions: list[tuple[str, Decimal]] = field(default_factory=list)
    rate_date: date | None = None
    at: datetime = field(default_factory=timezone.now)


def record_price(source, coin_id, base_currency, price, change_24h_percent) -> None:
    """Note that a price was returned to a client."""
    ledger = _lookups.get()
    if ledger is None:
        return
    ledger.append(
        _Lookup(
            source=source,
            coin_id=coin_id,
            base_currency=base_currency.upper(),
            price=price,
            change_24h_percent=change_24h_percent,
        )
    )


def record_conversions(coin_id, base_currency, rate_date, conversions) -> None:
    """Attach conversions to the price lookup they were derived from."""
    ledger = _lookups.get()
    if not ledger:
        return
    base_currency = base_currency.upper()
    for entry in reversed(ledger):
        if entry.coin_id == coin_id and entry.base_currency == base_currency:
            entry.rate_date = rate_date
            entry.conversions.extend((code.upper(), price) for code, price in conversions)
            return


def _rows(ledger: list[_Lookup]) -> list[PriceLookup]:
    rows = []
    for entry in ledger:
        common = {
            "looked_up_at": entry.at,
            "source": entry.source,
            "coin_id": entry.coin_id,
            "base_currency": entry.base_currency,
            "price": entry.price,
            "change_24h_percent": entry.change_24h_percent,
            "rate_date": entry.rate_date,
        }
        if entry.conversions:
            rows.extend(
                PriceLookup(**common, quote_currency=code, quote_price=price)
                for code, price in entry.conversions
            )
        else:
            rows.append(PriceLookup(**common))
    return rows


@contextmanager
def track_lookups():
    """Collect the lookups made while handling one request, then persist them."""
    token = _lookups.set([])
    try:
        yield
    finally:
        ledger = _lookups.get()
        _lookups.reset(token)
        if ledger:
            try:
                PriceLookup.objects.bulk_create(_rows(ledger))
            except Exception:
                # An audit write must never turn a successful lookup into a 500.
                logger.exception("Failed to write %d price lookup(s)", len(ledger))


class AuditMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        with track_lookups():
            return self.get_response(request)
from datetime import date
from decimal import Decimal

import strawberry
from graphql import GraphQLError

from . import clients
from .serializers import CURRENCY_CODE


def _check_codes(*codes: str) -> None:
    invalid = [code for code in codes if not CURRENCY_CODE.match(code)]
    if invalid:
        raise GraphQLError(
            f"Invalid currency code(s): {', '.join(invalid)}.",
            extensions={"code": "BAD_USER_INPUT"},
        )


def _upstream_error() -> GraphQLError:
    return GraphQLError(
        "An upstream data provider is unavailable. Try again later.",
        extensions={"code": "UPSTREAM_UNAVAILABLE"},
    )


# --- Types ---

@strawberry.type(description="A fiat currency supported for exchange rates.")
class Currency:
    code: str
    name: str


@strawberry.type(description="One currency's rate relative to the base currency.")
class Rate:
    currency: str
    rate: Decimal


@strawberry.type(description="ECB reference rates for a base currency on a given date.")
class ExchangeRates:
    base: str
    date: date
    rates: list[Rate]


@strawberry.type(description="A crypto price converted into another fiat currency.")
class ConvertedPrice:
    currency: str
    price: Decimal


@strawberry.type(description="Current price of a cryptocurrency.")
class CryptoPrice:
    id: strawberry.ID
    currency: str
    price: Decimal
    change_24h_percent: Decimal | None

    @strawberry.field(description="This price converted into other fiat currencies using ECB rates.")
    def converted(self, to: list[str]) -> list[ConvertedPrice]:
        _check_codes(*to)
        try:
            rates = clients.fetch_latest_rates(self.currency, to)
        except clients.NotFoundError:
            raise GraphQLError(
                f"Cannot convert from '{self.currency.upper()}'.",
                extensions={"code": "BAD_USER_INPUT"},
            )
        except clients.UpstreamError:
            raise _upstream_error()
        return [
            ConvertedPrice(currency=code, price=self.price * rate)
            for code, rate in rates.rates.items()
        ]


# --- Root query ---

@strawberry.type
class Query:
    @strawberry.field(description="All fiat currencies supported for exchange rates.")
    def currencies(self) -> list[Currency] | None:
        try:
            data = clients.fetch_currencies()
        except clients.UpstreamError:
            raise _upstream_error()
        return [Currency(code=code, name=name) for code, name in data.items()]

    @strawberry.field(description="Latest ECB reference rates.")
    def exchange_rates(self, base: str = "EUR", symbols: list[str] | None = None) -> ExchangeRates | None:
        _check_codes(base, *(symbols or []))
        try:
            result = clients.fetch_latest_rates(base, symbols)
        except clients.NotFoundError:
            raise GraphQLError(
                "Unsupported currency code in 'base' or 'symbols'.",
                extensions={"code": "BAD_USER_INPUT"},
            )
        except clients.UpstreamError:
            raise _upstream_error()
        return ExchangeRates(
            base=result.base,
            date=result.date,
            rates=[Rate(currency=code, rate=rate) for code, rate in result.rates.items()],
        )

    @strawberry.field(description="Current price of a cryptocurrency, or null if the coin is unknown.")
    def crypto_price(self, id: str, currency: str = "usd") -> CryptoPrice | None:
        _check_codes(currency)
        try:
            result = clients.fetch_crypto_price(id, currency)
        except clients.NotFoundError:
            return None
        except clients.UpstreamError:
            raise _upstream_error()
        return CryptoPrice(
            id=result.coin_id,
            currency=result.currency,
            price=result.price,
            change_24h_percent=result.change_24h_percent,
        )


schema = strawberry.Schema(query=Query)
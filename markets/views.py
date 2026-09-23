from .cache import CacheControlMixin
from rest_framework import viewsets
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from . import clients
from .serializers import (
    CryptoPriceQuerySerializer,
    CryptoPriceSerializer,
    CurrencySerializer,
    ExchangeRatesQuerySerializer,
    ExchangeRatesSerializer,
)


class CurrencyViewSet(CacheControlMixin, viewsets.ViewSet):
    """Fiat currencies supported for exchange rates (source: Frankfurter / ECB)."""

    cache_seconds_setting = "CURRENCIES_CACHE_SECONDS"
    lookup_field = "code"
    lookup_value_regex = "[A-Za-z]{3}"

    def list(self, request, **kwargs):
        currencies = [{"code": code, "name": name} for code, name in clients.fetch_currencies().items()]
        serializer = CurrencySerializer(currencies, many=True)
        return Response({"count": len(currencies), "results": serializer.data})

    def retrieve(self, request, code=None, **kwargs):
        code = code.upper()
        currencies = clients.fetch_currencies()
        if code not in currencies:
            raise NotFound(f"Currency '{code}' is not supported.")
        return Response(CurrencySerializer({"code": code, "name": currencies[code]}).data)


class LatestExchangeRatesView(CacheControlMixin, APIView):
    """Latest ECB reference rates for a base currency (source: Frankfurter)."""

    cache_seconds_setting = "EXCHANGE_RATE_CACHE_SECONDS"

    def get(self, request, **kwargs):
        query = ExchangeRatesQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)

        try:
            rates = clients.fetch_latest_rates(**query.validated_data)
        except clients.NotFoundError:
            raise ValidationError("Unsupported currency code in 'base' or 'symbols'. See /api/v1/currencies/.")
        return Response(ExchangeRatesSerializer(rates).data)


class CryptocurrencyViewSet(CacheControlMixin, viewsets.ViewSet):
    """Current price of a cryptocurrency (source: CoinGecko)."""

    cache_seconds_setting = "CRYPTO_CACHE_SECONDS"
    lookup_field = "coin_id"
    lookup_value_regex = "[a-z0-9-]+"


    def retrieve(self, request, coin_id=None, **kwargs):
        query = CryptoPriceQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        currency = query.validated_data["currency"]

        try:
            price = clients.fetch_crypto_price(coin_id, currency)
        except clients.NotFoundError:
            raise NotFound(f"No price found for '{coin_id}' in '{currency}'.")
        return Response(CryptoPriceSerializer(price).data)
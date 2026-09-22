import re

from rest_framework import serializers

CURRENCY_CODE = re.compile(r"^[A-Za-z]{3}$")


class CurrencyCodeField(serializers.RegexField):
    default_error_messages = {"invalid": "Must be a 3-letter ISO 4217 currency code."}

    def __init__(self, **kwargs):
        super().__init__(CURRENCY_CODE, **kwargs)


# --- Query parameter validation (input) ---

class ExchangeRatesQuerySerializer(serializers.Serializer):
    base = CurrencyCodeField(default="EUR")
    symbols = serializers.CharField(required=False, help_text="Comma-separated, e.g. EUR,GBP")

    def validate_symbols(self, value):
        codes = [code.strip() for code in value.split(",") if code.strip()]
        invalid = [code for code in codes if not CURRENCY_CODE.match(code)]
        if invalid:
            raise serializers.ValidationError(f"Invalid currency code(s): {', '.join(invalid)}.")
        return codes


class CryptoPriceQuerySerializer(serializers.Serializer):
    currency = CurrencyCodeField(default="usd")


# --- Response bodies (output) ---

class CurrencySerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()


class ExchangeRatesSerializer(serializers.Serializer):
    base = serializers.CharField()
    date = serializers.DateField()
    rates = serializers.DictField(
        child=serializers.DecimalField(max_digits=None, decimal_places=None)
    )

class CryptoPriceSerializer(serializers.Serializer):
    id = serializers.CharField(source="coin_id")
    currency = serializers.CharField()
    price = serializers.DecimalField(max_digits=None, decimal_places=None)
    change_24h_percent = serializers.DecimalField(
        max_digits=None, decimal_places=None, allow_null=True
    )
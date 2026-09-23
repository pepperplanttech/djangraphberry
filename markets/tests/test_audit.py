import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from markets.models import PriceLookup

from .base import UpstreamTestCase

JSON = {"HTTP_ACCEPT": "application/json"}

SNAPSHOT = """
query { cryptoPrice(id: "bitcoin", currency: "usd") {
  id price change24hPercent converted(to: ["EUR"]) { currency price } } }
"""
TWO_CURRENCIES = """
query { cryptoPrice(id: "bitcoin", currency: "usd") {
  id converted(to: ["EUR", "GBP"]) { currency price } } }
"""


class AuditTestCase(UpstreamTestCase):
    def graphql(self, document):
        return self.client.post(
            "/graphql/", json.dumps({"query": document}), content_type="application/json"
        )


class RecordedLookupTests(AuditTestCase):
    def test_a_rest_lookup_is_recorded_without_a_conversion(self):
        res = self.client.get("/api/v1/cryptocurrencies/bitcoin/", **JSON)
        self.assertEqual(res.status_code, 200)

        row = PriceLookup.objects.get()
        self.assertEqual(row.source, PriceLookup.Source.REST)
        self.assertEqual(row.coin_id, "bitcoin")
        self.assertEqual(row.base_currency, "USD")
        self.assertEqual(row.price, Decimal("85457.0000000000"))
        self.assertEqual(row.change_24h_percent, Decimal("-0.590000"))
        self.assertEqual(row.quote_currency, "")
        self.assertIsNone(row.quote_price)
        self.assertIsNone(row.rate_date)
        self.assertIsNotNone(row.looked_up_at)

    def test_a_graphql_snapshot_is_one_row_carrying_the_conversion(self):
        self.assertNotIn("errors", self.graphql(SNAPSHOT).json())

        row = PriceLookup.objects.get()
        self.assertEqual(row.source, PriceLookup.Source.GRAPHQL)
        self.assertEqual(row.base_currency, "USD")
        self.assertEqual(row.quote_currency, "EUR")
        self.assertEqual(row.rate_date, date(2026, 9, 22))
        self.assertGreater(row.quote_price, 0)

    def test_each_target_currency_gets_its_own_row(self):
        self.graphql(TWO_CURRENCIES)
        self.assertEqual(
            sorted(PriceLookup.objects.values_list("quote_currency", flat=True)),
            ["EUR", "GBP"],
        )

    def test_repeated_lookups_are_recorded_separately(self):
        self.graphql(SNAPSHOT)
        self.graphql(SNAPSHOT)
        self.assertEqual(PriceLookup.objects.count(), 2)

    def test_a_cache_hit_is_still_a_lookup(self):
        self.client.get("/api/v1/cryptocurrencies/bitcoin/", **JSON)
        self.client.get("/api/v1/cryptocurrencies/bitcoin/", **JSON)
        self.assertEqual(self.upstream.count("/simple/price"), 1)
        self.assertEqual(PriceLookup.objects.count(), 2)


class UnrecordedTests(AuditTestCase):
    def test_an_unknown_coin_records_nothing(self):
        res = self.client.get("/api/v1/cryptocurrencies/dogecoin/", **JSON)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(PriceLookup.objects.count(), 0)

    def test_a_query_without_a_price_records_nothing(self):
        self.graphql("query { currencies { code } }")
        self.assertEqual(PriceLookup.objects.count(), 0)

    def test_an_upstream_failure_records_nothing(self):
        import httpx

        self.upstream.fail_with = httpx.ConnectError("boom")
        self.client.get("/api/v1/cryptocurrencies/bitcoin/", **JSON)
        self.assertEqual(PriceLookup.objects.count(), 0)


class AuditFailureTests(AuditTestCase):
    def test_a_failed_write_is_logged_but_does_not_break_the_response(self):
        with patch.object(PriceLookup.objects, "bulk_create", side_effect=RuntimeError("db down")):
            with self.assertLogs("markets.audit", level="ERROR"):
                res = self.client.get("/api/v1/cryptocurrencies/bitcoin/", **JSON)

        self.assertEqual(res.status_code, 200)
        self.assertEqual(PriceLookup.objects.count(), 0)

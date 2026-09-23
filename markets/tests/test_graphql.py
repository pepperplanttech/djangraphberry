import json
import logging

from .base import UpstreamTestCase

SNAPSHOT = """
query Snapshot($coinId: String!, $currency: String!) {
  cryptoPrice(id: $coinId, currency: "usd") {
    id
    price
    change24hPercent
    converted(to: [$currency]) { currency price }
  }
  exchangeRates(base: "USD", symbols: [$currency]) { date }
}
"""


class GraphQLTestCase(UpstreamTestCase):
    def setUp(self):
        super().setUp()
        # Strawberry logs a traceback for every GraphQL error it returns, which
        # is noise here: raising one deliberately is what several tests do.
        logger = logging.getLogger("strawberry.execution")
        level = logger.level
        logger.setLevel(logging.CRITICAL)
        self.addCleanup(logger.setLevel, level)

    def query(self, document, **variables):
        res = self.client.post(
            "/graphql/",
            json.dumps({"query": document, "variables": variables}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        return res, res.json()

    def assertCode(self, body, code):
        self.assertEqual(body["errors"][0]["extensions"]["code"], code)


class QueryTests(GraphQLTestCase):
    def test_currencies(self):
        _, body = self.query("query { currencies { code name } }")
        self.assertIn({"code": "EUR", "name": "Euro"}, body["data"]["currencies"])

    def test_exchange_rates(self):
        _, body = self.query('query { exchangeRates(base: "USD", symbols: ["EUR"]) { base date rates { currency rate } } }')
        rates = body["data"]["exchangeRates"]
        self.assertEqual(rates["base"], "USD")
        self.assertEqual(rates["date"], "2026-09-22")
        self.assertEqual([r["currency"] for r in rates["rates"]], ["EUR"])

    def test_snapshot_combines_both_providers(self):
        _, body = self.query(SNAPSHOT, coinId="bitcoin", currency="EUR")
        self.assertNotIn("errors", body)
        price = body["data"]["cryptoPrice"]
        self.assertEqual(price["id"], "bitcoin")
        self.assertEqual(price["change24hPercent"], "-0.59")
        self.assertEqual(price["converted"][0]["currency"], "EUR")
        self.assertEqual(body["data"]["exchangeRates"]["date"], "2026-09-22")

    def test_frankfurter_is_only_called_when_converted_is_selected(self):
        self.query('query { cryptoPrice(id: "bitcoin") { price } }')
        self.assertEqual(self.upstream.count("/latest"), 0)

        self.query('query { cryptoPrice(id: "bitcoin") { converted(to: ["EUR"]) { price } } }')
        self.assertEqual(self.upstream.count("/latest"), 1)

    def test_an_unknown_coin_resolves_to_null_without_an_error(self):
        _, body = self.query('query { cryptoPrice(id: "dogecoin") { price } }')
        self.assertIsNone(body["data"]["cryptoPrice"])
        self.assertNotIn("errors", body)


class ErrorTests(GraphQLTestCase):
    def test_a_malformed_currency_code_is_bad_user_input(self):
        _, body = self.query('query { exchangeRates(base: "DOLLAR") { date } }')
        self.assertCode(body, "BAD_USER_INPUT")

    def test_an_unsupported_currency_code_is_bad_user_input(self):
        _, body = self.query('query { exchangeRates(base: "ZZZ") { date } }')
        self.assertCode(body, "BAD_USER_INPUT")

    def test_an_upstream_failure_is_upstream_unavailable(self):
        import httpx

        self.upstream.fail_with = httpx.ConnectError("boom")
        _, body = self.query("query { currencies { code } }")
        self.assertCode(body, "UPSTREAM_UNAVAILABLE")

    def test_one_failing_field_does_not_discard_the_others(self):
        _, body = self.query(
            'query { ok: cryptoPrice(id: "bitcoin") { price } bad: exchangeRates(base: "ZZZ") { date } }'
        )
        self.assertIsNotNone(body["data"]["ok"])
        self.assertIsNone(body["data"]["bad"])
        self.assertCode(body, "BAD_USER_INPUT")


class GraphQLCacheHeaderTests(GraphQLTestCase):
    def test_the_response_advertises_the_freshness_it_relied_on(self):
        res, _ = self.query('query { cryptoPrice(id: "bitcoin") { price } }')
        self.assertIn("max-age=", res.headers["Cache-Control"])

    def test_the_shortest_lived_entry_wins(self):
        # Crypto prices (60s) are shorter-lived than the currency list (24h).
        res, _ = self.query('query { currencies { code } cryptoPrice(id: "bitcoin") { price } }')
        max_age = int(res.headers["Cache-Control"].split("max-age=")[1].split(",")[0])
        self.assertLessEqual(max_age, 60)

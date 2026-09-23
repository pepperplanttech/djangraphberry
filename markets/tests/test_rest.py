from django.test import override_settings

from .base import UpstreamTestCase

JSON = {"HTTP_ACCEPT": "application/json"}


class CurrencyEndpointTests(UpstreamTestCase):
    def test_list_is_wrapped_with_a_count(self):
        res = self.client.get("/api/v1/currencies/", **JSON)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["count"], 3)
        self.assertIn({"code": "EUR", "name": "Euro"}, body["results"])

    def test_retrieve_is_case_insensitive(self):
        res = self.client.get("/api/v1/currencies/eur/", **JSON)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["code"], "EUR")

    def test_unknown_currency_is_a_problem_details_404(self):
        res = self.client.get("/api/v1/currencies/ZZZ/", **JSON)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.headers["Content-Type"], "application/problem+json")
        self.assertEqual(res.json()["status"], 404)
        self.assertIn("ZZZ", res.json()["detail"])

    def test_write_methods_are_rejected_with_an_allow_header(self):
        res = self.client.post("/api/v1/currencies/", **JSON)
        self.assertEqual(res.status_code, 405)
        self.assertIn("GET", res.headers["Allow"])

    def test_an_unknown_api_version_is_a_404(self):
        self.assertEqual(self.client.get("/api/v2/currencies/", **JSON).status_code, 404)


class ExchangeRateEndpointTests(UpstreamTestCase):
    def test_rates_are_serialized_as_strings(self):
        res = self.client.get("/api/v1/exchange-rates/latest/?base=USD&symbols=EUR,GBP", **JSON)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["base"], "USD")
        self.assertEqual(body["date"], "2026-09-22")
        self.assertEqual(sorted(body["rates"]), ["EUR", "GBP"])
        for value in body["rates"].values():
            self.assertIsInstance(value, str)

    def test_a_malformed_code_is_a_400_with_per_field_errors(self):
        res = self.client.get("/api/v1/exchange-rates/latest/?base=DOLLAR", **JSON)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.headers["Content-Type"], "application/problem+json")
        self.assertIn("base", res.json()["errors"])

    def test_a_well_formed_but_unsupported_code_is_a_400(self):
        res = self.client.get("/api/v1/exchange-rates/latest/?base=ZZZ", **JSON)
        self.assertEqual(res.status_code, 400)
        self.assertIn("currencies", res.json()["detail"])


class CryptocurrencyEndpointTests(UpstreamTestCase):
    def test_price_and_change_are_serialized_as_strings(self):
        res = self.client.get("/api/v1/cryptocurrencies/bitcoin/", **JSON)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["id"], "bitcoin")
        self.assertEqual(body["currency"], "usd")
        self.assertEqual(body["price"], "85457.0")
        self.assertEqual(body["change_24h_percent"], "-0.59")

    def test_a_missing_change_is_null_rather_than_absent(self):
        res = self.client.get("/api/v1/cryptocurrencies/flatline/", **JSON)
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.json()["change_24h_percent"])

    def test_an_unknown_coin_is_a_404(self):
        res = self.client.get("/api/v1/cryptocurrencies/dogecoin/", **JSON)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["status"], 404)

    def test_a_malformed_currency_parameter_is_a_400(self):
        res = self.client.get("/api/v1/cryptocurrencies/bitcoin/?currency=dollars", **JSON)
        self.assertEqual(res.status_code, 400)
        self.assertIn("currency", res.json()["errors"])


class UpstreamFailureTests(UpstreamTestCase):
    def test_an_upstream_error_becomes_a_502(self):
        import httpx

        self.upstream.fail_with = httpx.ConnectError("boom")
        res = self.client.get("/api/v1/currencies/", **JSON)
        self.assertEqual(res.status_code, 502)
        self.assertEqual(res.headers["Content-Type"], "application/problem+json")
        self.assertEqual(res.json()["status"], 502)


class HttpCacheHeaderTests(UpstreamTestCase):
    def test_a_success_advertises_freshness_and_an_etag(self):
        res = self.client.get("/api/v1/cryptocurrencies/bitcoin/", **JSON)
        self.assertEqual(res.status_code, 200)
        self.assertIn("public", res.headers["Cache-Control"])
        self.assertIn("max-age=", res.headers["Cache-Control"])
        self.assertIn("ETag", res.headers)

    def test_max_age_never_exceeds_the_ttl(self):
        with override_settings(CRYPTO_CACHE_SECONDS=30):
            res = self.client.get("/api/v1/cryptocurrencies/bitcoin/", **JSON)
        max_age = int(res.headers["Cache-Control"].split("max-age=")[1].split(",")[0])
        self.assertLessEqual(max_age, 30)

    def test_caching_is_disabled_by_a_zero_ttl(self):
        with override_settings(CRYPTO_CACHE_SECONDS=0):
            self.client.get("/api/v1/cryptocurrencies/bitcoin/", **JSON)
            self.client.get("/api/v1/cryptocurrencies/bitcoin/", **JSON)
        self.assertEqual(self.upstream.count("/simple/price"), 2)

    def test_an_error_is_never_stored(self):
        res = self.client.get("/api/v1/currencies/ZZZ/", **JSON)
        self.assertIn("no-store", res.headers["Cache-Control"])

    def test_a_matching_etag_gets_a_304_with_no_body(self):
        first = self.client.get("/api/v1/currencies/EUR/", **JSON)
        second = self.client.get(
            "/api/v1/currencies/EUR/", HTTP_IF_NONE_MATCH=first.headers["ETag"], **JSON
        )
        self.assertEqual(second.status_code, 304)
        self.assertEqual(second.content, b"")

    def test_content_negotiation_is_declared_in_vary(self):
        res = self.client.get("/api/v1/currencies/", **JSON)
        self.assertIn("Accept", res.headers["Vary"])

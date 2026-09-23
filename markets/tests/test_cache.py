from decimal import Decimal

import httpx
from django.test import override_settings

from markets import clients
from markets.cache import remaining_max_age, track_freshness
from markets.exceptions import NotFoundError, UpstreamError

from .base import UpstreamTestCase


class CachedDecoratorTests(UpstreamTestCase):
    def test_a_repeat_call_does_not_reach_upstream(self):
        first = clients.fetch_crypto_price("bitcoin", "usd")
        second = clients.fetch_crypto_price("bitcoin", "usd")
        self.assertEqual(first, second)
        self.assertEqual(self.upstream.count("/simple/price"), 1)

    def test_different_arguments_are_cached_separately(self):
        clients.fetch_crypto_price("bitcoin", "usd")
        clients.fetch_crypto_price("ethereum", "usd")
        self.assertEqual(self.upstream.count("/simple/price"), 2)

    def test_parsed_values_survive_the_round_trip(self):
        clients.fetch_latest_rates("USD", ["EUR"])
        rates = clients.fetch_latest_rates("USD", ["EUR"])
        self.assertIsInstance(rates.rates["EUR"], Decimal)
        self.assertEqual(rates.date.year, 2026)

    def test_a_not_found_is_cached_so_a_bad_id_cannot_hammer_upstream(self):
        for _ in range(3):
            with self.assertRaises(NotFoundError):
                clients.fetch_crypto_price("dogecoin", "usd")
        self.assertEqual(self.upstream.count("/simple/price"), 1)

    def test_an_upstream_failure_is_never_cached(self):
        self.upstream.fail_with = httpx.ConnectError("boom")
        for _ in range(3):
            with self.assertRaises(UpstreamError):
                clients.fetch_currencies()
        self.assertEqual(self.upstream.count("/currencies"), 3)

    @override_settings(CRYPTO_CACHE_SECONDS=0)
    def test_a_zero_ttl_disables_caching(self):
        clients.fetch_crypto_price("bitcoin", "usd")
        clients.fetch_crypto_price("bitcoin", "usd")
        self.assertEqual(self.upstream.count("/simple/price"), 2)


class FreshnessLedgerTests(UpstreamTestCase):
    def test_the_ledger_reports_the_soonest_expiry(self):
        with override_settings(CURRENCIES_CACHE_SECONDS=3600, CRYPTO_CACHE_SECONDS=60):
            with track_freshness():
                clients.fetch_currencies()
                clients.fetch_crypto_price("bitcoin", "usd")
                self.assertLessEqual(remaining_max_age(), 60)

    def test_a_cache_hit_reports_the_remaining_life_not_the_full_ttl(self):
        clients.fetch_crypto_price("bitcoin", "usd")  # populate, outside any ledger
        with track_freshness():
            clients.fetch_crypto_price("bitcoin", "usd")
            self.assertIsNotNone(remaining_max_age())

    def test_nothing_is_reported_when_no_cache_was_touched(self):
        with track_freshness():
            self.assertIsNone(remaining_max_age())

    def test_nothing_is_reported_outside_a_request(self):
        clients.fetch_crypto_price("bitcoin", "usd")
        self.assertIsNone(remaining_max_age())

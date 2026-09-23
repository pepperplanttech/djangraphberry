from django.core.cache import cache
from django.test import TestCase

from .stubs import upstream


class UpstreamTestCase(TestCase):
    """Installs the fake upstream and starts every test with an empty cache.

    LocMemCache lives for the life of the process, so without the clear a cache
    entry written by one test would silently satisfy the next one.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        ctx = upstream()
        self.upstream = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)

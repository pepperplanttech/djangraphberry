from django.db import models
from django.utils import timezone


class PriceLookup(models.Model):
    """One cryptocurrency price lookup, as it was answered for a client.

    A row is written for every lookup, including ones served from cache: this
    records what a client was told, not what the upstream providers were asked.
    """

    class Source(models.TextChoices):
        REST = "rest", "REST"
        GRAPHQL = "graphql", "GraphQL"

    looked_up_at = models.DateTimeField(default=timezone.now, editable=False, db_index=True)
    source = models.CharField(max_length=10, choices=Source.choices)

    coin_id = models.CharField(max_length=100)
    base_currency = models.CharField(max_length=3)
    price = models.DecimalField(max_digits=24, decimal_places=10)
    change_24h_percent = models.DecimalField(max_digits=12, decimal_places=6, null=True)

    quote_currency = models.CharField(max_length=3, blank=True)
    quote_price = models.DecimalField(max_digits=24, decimal_places=10, null=True)
    rate_date = models.DateField(null=True)

    class Meta:
        ordering = ["-looked_up_at"]
        indexes = [models.Index(fields=["coin_id", "-looked_up_at"])]

    def __str__(self):
        return f"{self.coin_id}/{self.base_currency} at {self.looked_up_at:%Y-%m-%d %H:%M:%S}"
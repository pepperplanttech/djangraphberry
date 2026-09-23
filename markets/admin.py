from django.contrib import admin
from .models import PriceLookup


@admin.register(PriceLookup)
class PriceLookupAdmin(admin.ModelAdmin):
    """Read-only: an audit log that can be edited is not an audit log."""

    list_display = (
        "looked_up_at", "source", "coin_id", "base_currency", "price",
        "change_24h_percent", "quote_currency", "quote_price", "rate_date",
    )
    list_filter = ("source", "coin_id", "base_currency", "quote_currency")
    search_fields = ("coin_id",)
    date_hierarchy = "looked_up_at"
    readonly_fields = [f.name for f in PriceLookup._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("currencies", views.CurrencyViewSet, basename="currency")
router.register("cryptocurrencies", views.CryptocurrencyViewSet, basename="cryptocurrency")

urlpatterns = [
    path("exchange-rates/latest/", views.LatestExchangeRatesView.as_view(), name="exchange-rates-latest"),
    *router.urls,
]
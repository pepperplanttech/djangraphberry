# djangraphberry

A Django service that aggregates two public financial data sources behind a single API, exposed as both a versioned REST interface and a GraphQL endpoint, with a React client. Everything runs locally with Docker Compose.

## What it does

The service wraps two upstream providers and normalizes their responses:

| Provider | Data | Notes |
| --- | --- | --- |
| [Frankfurter](https://frankfurter.dev) | Daily ECB reference exchange rates for ~30 fiat currencies | No API key; updated once per working day |
| [CoinGecko](https://www.coingecko.com/en/api) | Current cryptocurrency prices and 24-hour change | Public endpoints; rate limited without a key |

On top of that data it answers questions that need both providers at once, such as the current price of a cryptocurrency converted into an arbitrary fiat currency.

## Architecture

```
                      ┌──────────────────────────────┐
  browser  ─────────► │ React + TypeScript (Vite)    │
                      │ dev server :5173             │
                      └──────────────┬───────────────┘
                         /graphql, /api proxied
                      ┌──────────────▼───────────────┐
                      │ Django :8000                 │
                      │  ┌────────────┬────────────┐ │
                      │  │ DRF        │ Strawberry │ │
                      │  │ /api/v1/   │ /graphql/  │ │
                      │  └──────┬─────┴─────┬──────┘ │
                      │     markets.clients          │
                      └──────────────┬───────────────┘
                     ┌───────────────┴───────────────┐
                     ▼                               ▼
               Frankfurter                      CoinGecko
```

Both API surfaces are thin layers over one shared client module, so they always return the same data and enforce the same validation rules.

### Layers

- **`markets/clients.py`** — the only code that performs outbound HTTP. It owns the upstream URLs, request timeouts and response parsing, returns frozen dataclasses with `Decimal` money values, and translates every upstream failure into one of two domain exceptions: `NotFoundError` or `UpstreamError`.
- **`markets/serializers.py`** — DRF serializers used in both directions: validating query parameters on the way in, and shaping JSON responses on the way out. Currency-code validation lives here and is shared with the GraphQL layer.
- **`markets/views.py`** — DRF viewsets and views. They contain no HTTP-client code and no error-response construction.
- **`markets/exceptions.py`** — a custom DRF exception handler that renders every error as [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem details, plus the `UpstreamUnavailable` (502) exception that upstream failures map to.
- **`markets/schema.py`** — the Strawberry GraphQL schema: code-first types, root query fields, and a nested `converted` resolver that combines both providers.
- **`config/`** — settings, URL routing and the WSGI/ASGI entry points.

No database models are involved. Django's ORM is unused except for its built-in apps; all data is fetched live from the upstream providers.

## REST API

Base path: `/api/v1/`. The version is captured from the URL and validated by DRF's `URLPathVersioning`, so unknown versions return 404.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/v1/currencies/` | Supported fiat currencies |
| `GET` | `/api/v1/currencies/{code}/` | One currency, by ISO 4217 code |
| `GET` | `/api/v1/exchange-rates/latest/` | Latest rates; `?base=USD&symbols=EUR,GBP` |
| `GET` | `/api/v1/cryptocurrencies/{id}/` | Current price; `?currency=usd` |

Design characteristics:

- Resources are nouns, collections are plural, and the identifier is in the path; query parameters only filter or shape the representation.
- Only `GET` is implemented, so every other method returns `405` with an `Allow` header.
- Collections are wrapped in an object (`count`, `results`) to leave room for pagination.
- Status codes distinguish client error (`400` for an invalid or unsupported parameter), missing resource (`404`), and upstream failure (`502`).
- Errors use a single format, `application/problem+json`, with per-field messages under `errors` for validation failures.
- Monetary values are serialized as strings to preserve decimal precision.

A browsable HTML version of every endpoint is available in a browser via DRF's browsable API renderer.

### Example

```bash
curl "http://localhost:8000/api/v1/exchange-rates/latest/?base=USD&symbols=EUR,GBP"
```
```json
{"base": "USD", "date": "2026-09-22", "rates": {"EUR": "0.87237", "GBP": "0.74832"}}
```

## GraphQL API

Endpoint: `/graphql/`. GraphiQL is served at the same URL in a browser.

```graphql
type Query {
  currencies: [Currency!]
  exchangeRates(base: String! = "EUR", symbols: [String!]): ExchangeRates
  cryptoPrice(id: String!, currency: String! = "usd"): CryptoPrice
}
```

`CryptoPrice.converted(to: [String!]!)` resolves a price into other fiat currencies, calling Frankfurter only when a query actually selects that field.

```graphql
query MarketSnapshot {
  cryptoPrice(id: "bitcoin") {
    price
    change24hPercent
    converted(to: ["EUR", "GBP"]) { currency price }
  }
  exchangeRates(base: "USD", symbols: ["EUR"]) { date }
}
```

Design characteristics:

- Code-first schema: GraphQL types are generated from Python type hints, with snake_case fields exposed as camelCase.
- The endpoint is unversioned; changes are made additively, with deprecations rather than a new path.
- A missing resource resolves to `null`; failures are reported in `errors` with a machine-readable `extensions.code` (`BAD_USER_INPUT`, `UPSTREAM_UNAVAILABLE`).
- Root fields are nullable so that one failing field does not discard the rest of a multi-field response.
- Currency-code validation is shared with the REST layer.

## Frontend

`frontend/` holds a React 19 + TypeScript application built with Vite. In development it runs in its own container and proxies `/graphql` and `/api` to the Django service over the Compose network, so the browser makes same-origin requests only and no CORS configuration is required.

The client queries the GraphQL endpoint for a market snapshot — a cryptocurrency's price, its 24-hour change, and that price converted into a selected fiat currency — and renders it as a formatted table.

## Running locally

Requirements: Docker Desktop.

1. Create `.env` in the project root (see `.env.example`):

   ```
   DJANGO_SECRET_KEY=<any long random string>
   DJANGO_DEBUG=1
   ```

2. Start both services:

   ```bash
   docker compose up --build
   ```

| URL | Service |
| --- | --- |
| http://localhost:5173 | React client |
| http://localhost:8000/api/v1/ | REST API (browsable) |
| http://localhost:8000/graphql/ | GraphiQL |

Source directories are bind-mounted, so Django's auto-reloader and Vite's hot module replacement both pick up edits without a rebuild. Rebuild (`docker compose build`) only after changing `requirements.txt` or `frontend/package.json`.

### Common commands

```bash
docker compose run --rm web python manage.py shell     # Django shell
docker compose build web                               # after a dependency change
docker compose logs -f frontend                        # follow one service's logs
```

## Configuration

| Setting | Source | Purpose |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | environment (required) | Django cryptographic signing |
| `DJANGO_DEBUG` | environment | `1` enables debug mode |
| `FRANKFURTER_BASE_URL` | `config/settings.py` | Exchange-rate provider |
| `COINGECKO_BASE_URL` | `config/settings.py` | Crypto-price provider |
| `UPSTREAM_TIMEOUT_SECONDS` | `config/settings.py` | Per-request timeout for outbound calls |

Upstream base URLs are settings rather than constants so they can be pointed at a stub server in tests.

Neither provider requires an API key. CoinGecko's keyless tier is rate limited and returns `429` under load, which the service surfaces as `502` / `UPSTREAM_UNAVAILABLE`; a free CoinGecko demo key can be supplied to raise that limit.

## Project layout

```
.
├── compose.yaml          Two services: web (Django), frontend (Vite)
├── Dockerfile            Django image
├── config/               Settings, root URLconf, WSGI/ASGI
│   ├── settings.py
│   └── urls.py           /admin/, /api/<version>/, /graphql/
├── markets/              The application
│   ├── clients.py        Upstream HTTP clients and domain exceptions
│   ├── serializers.py    Input validation and output shaping
│   ├── views.py          DRF viewsets and views
│   ├── exceptions.py     Problem-details error handling
│   ├── schema.py         Strawberry GraphQL schema
│   └── urls.py           Router registrations
├── requirements.txt
└── frontend/             React + TypeScript client (Vite)
    ├── Dockerfile
    ├── vite.config.ts    Dev-server proxy to the Django service
    └── src/
```

## Planned work

- Response caching for upstream calls, to cut latency and stay inside CoinGecko's rate limit.
- `504` for upstream timeouts, distinct from `502` for upstream errors.
- Test suites for both API surfaces, with the upstream providers stubbed.

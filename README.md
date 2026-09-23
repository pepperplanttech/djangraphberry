# djangraphberry

A Django service that aggregates two public financial data sources behind a single API, exposed as both a versioned REST interface and a GraphQL endpoint, with a React client. Every lookup it answers is recorded to PostgreSQL. Everything runs locally with Docker Compose.

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
                      │ keyed client-side cache      │
                      └──────────────┬───────────────┘
                         /graphql, /api proxied
                      ┌──────────────▼───────────────┐     ┌────────────┐
                      │ Django :8000                 │     │ PostgreSQL │
                      │  ┌────────────┬────────────┐ │     │ audit log  │
                      │  │ DRF        │ Strawberry │ │     └──────▲─────┘
                      │  │ /api/v1/   │ /graphql/  │ │            │
                      │  └──────┬─────┴─────┬──────┘ │            │
                      │     markets.cache            │            │
                      │     markets.clients          │            │
                      │     markets.audit ───────────┼────────────┘
                      └──────────────┬───────────────┘
                     ┌───────────────┴───────────────┐
                     ▼                               ▼
               Frankfurter                      CoinGecko
```

Both API surfaces are thin layers over one shared client module, so they always return the same data, enforce the same validation rules, and share one cache.

### Layers

- **`markets/clients.py`** — the only code that performs outbound HTTP. It owns the upstream URLs, request timeouts and response parsing, returns frozen dataclasses with `Decimal` money values, and translates every upstream failure into one of two domain exceptions: `NotFoundError` or `UpstreamError`.
- **`markets/serializers.py`** — DRF serializers used in both directions: validating query parameters on the way in, and shaping JSON responses on the way out. Currency-code validation lives here and is shared with the GraphQL layer.
- **`markets/views.py`** — DRF viewsets and views. They contain no HTTP-client code and no error-response construction.
- **`markets/exceptions.py`** — a custom DRF exception handler that renders every error as [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem details, plus the `UpstreamUnavailable` (502) exception that upstream failures map to.
- **`markets/schema.py`** — the Strawberry GraphQL schema: code-first types, root query fields, and a nested `converted` resolver that combines both providers.
- **`markets/cache.py`** — the caching layer: a `@cached` decorator applied to the client functions, a per-request freshness ledger, and the middleware and DRF mixin that translate that freshness into HTTP cache headers.
- **`markets/models.py`** — `PriceLookup`, the single persisted model: one row per price lookup answered.
- **`markets/audit.py`** — a per-request ledger of lookups and the middleware that writes it, keeping persistence out of the views and resolvers.
- **`config/`** — settings, URL routing and the WSGI/ASGI entry points.

Market data itself is never stored. It is fetched live from the upstream providers and cached in memory; the database holds only the audit log and Django's built-in tables.

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
- Successful responses carry `Cache-Control: public, max-age=<remaining>` and an `ETag`, so clients and intermediaries can avoid or revalidate requests. Errors and the browsable HTML variant are `no-store`.
- `Vary: Accept` is set, because the same URL serves JSON or HTML depending on content negotiation.

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

## Caching

Upstream responses are cached in three layers, each with a distinct responsibility.

**Server-side, in `markets/cache.py`.** A `@cached("<TTL setting>")` decorator wraps each client function, keyed on its arguments, storing the parsed dataclass rather than the raw HTTP response. Because every outbound call goes through those functions, REST and GraphQL share one cache and one set of TTLs. Durations follow how fast each source actually changes:

| Data | TTL | Rationale |
| --- | --- | --- |
| Currency list | 24 hours | Effectively static |
| Exchange rates | 1 hour | ECB publishes once per working day |
| Crypto prices | 60 seconds | CoinGecko's own data is about a minute fresh |
| Not-found results | 30 seconds | Keeps a bad identifier from reaching the provider repeatedly |

Upstream *failures* are never cached, so a rate-limit response or timeout does not persist past the request that hit it.

**HTTP freshness.** A `ContextVar`-scoped ledger records the expiry of every cache entry a request touched. The response then advertises the *remaining* life of the shortest-lived one — not the full TTL — as `max-age`, so a client can never be staler than the server intended. The REST endpoints set this alongside `public` and an `ETag`; `FreshnessMiddleware` supplies it for the GraphQL endpoint, which no HTTP cache would honor on its own since the queries are `POST`.

**Client-side.** The React client keeps its own keyed, in-memory cache and expires entries using the `max-age` the server reported. It holds no TTL of its own, which is what keeps the two layers from disagreeing.

The cache backend is `LocMemCache`, which is per-process: under multiple workers each holds its own copy. Production would point `CACHES` at Redis; no application code changes.

## Audit log

Every price lookup the service answers is recorded in PostgreSQL as a `PriceLookup` row: when it happened, which surface served it, the coin, its price and 24-hour change, and — when the client asked for a conversion — the target currency, the converted price and the date of the rates used. That is the same set of facts the client table displays, plus a timestamp.

The write is deferred. GraphQL resolves `converted` as a child of `cryptoPrice`, so at the moment a price is known it is not yet clear whether a conversion was requested. Views and resolvers therefore *note* what they answered into a `ContextVar` ledger — the same mechanism the freshness tracking uses — and `AuditMiddleware` writes the whole ledger in one `bulk_create` at the end of the request. One lookup becomes one row; a conversion into several currencies becomes one row each.

Two consequences worth being explicit about:

- **Cache hits are logged.** The recording happens in the views and resolvers, outside the `@cached` client functions, because the log answers "what were clients told" rather than "what did we ask upstream". Repeated requests inside a TTL produce repeated rows and no upstream traffic.
- **Failed lookups are not logged.** Recording happens after the not-found branch, so the table contains answers, not attempts.

An audit write that fails is logged and swallowed rather than turned into a `500`, on the grounds that losing a row about a read-only lookup is less harmful than failing the lookup. A log with compliance weight would want the opposite trade.

`PriceLookup` is registered in the Django admin as read-only, with filters on source, coin and currency.

## Frontend

`frontend/` holds a React 19 + TypeScript application built with Vite. In development it runs in its own container and proxies `/graphql` and `/api` to the Django service over the Compose network, so the browser makes same-origin requests only and no CORS configuration is required.

The client queries the GraphQL endpoint for a market snapshot — a cryptocurrency's price, its 24-hour change, and that price converted into a selected fiat currency — and renders it as a formatted table. A cryptocurrency dropdown offers a fixed set of coins; the fiat dropdown is populated from the `currencies` query, so it always reflects what the upstream provider actually supports.

- Snapshots are cached in memory by `(coin, currency)` and expire on the server-supplied `max-age`, so revisiting a pair costs no request.
- The cache lives outside React and is read through `useSyncExternalStore`, rather than being mirrored into component state.
- A refresh control bypasses the cache on demand, keeping the current row visible until new data arrives.
- Each result is labelled with the time it was fetched, so a cached value is never mistaken for a live one.

## Tests

```bash
docker compose exec web python manage.py test
```

49 tests across four modules in `markets/tests/`, covering both API surfaces, the caching layer and the audit log.

The upstream providers are stubbed at `httpx.get` — the outermost boundary of the project — rather than at the client functions. Tests therefore exercise the real URL building, status handling, parsing, caching and error mapping; only the network is fake. `markets/tests/stubs.py` holds the canned payloads, counts the requests that reach it so tests can assert a cache hit made none, and has a switch for simulating an unreachable provider.

| Module | Covers |
| --- | --- |
| `test_rest.py` | Response shapes, status codes, problem details, `Allow` and `Vary` headers, versioning, `Cache-Control` and `304` revalidation |
| `test_graphql.py` | Queries against both providers, null-on-missing, error `extensions.code`, partial success, and that Frankfurter is only called when `converted` is selected |
| `test_cache.py` | The `@cached` decorator's hits, key separation, negative caching, never caching failures, and the freshness ledger |
| `test_audit.py` | One row per lookup, one per target currency, cache hits still logged, failures not logged, and a failed write not breaking the response |

The test runner creates and drops a separate `test_djangraphberry` database, so running the suite never touches development data.

## Running locally

Requirements: Docker Desktop.

1. Create `.env` in the project root (see `.env.example`):

   ```
   DJANGO_SECRET_KEY=<any long random string>
   DJANGO_DEBUG=1
   POSTGRES_DB=djangraphberry
   POSTGRES_USER=djangraphberry
   POSTGRES_PASSWORD=<any password>
   POSTGRES_HOST=db
   POSTGRES_PORT=5432
   ```

   One file feeds both services: Compose passes the `POSTGRES_*` values to the database image, which uses them to initialize the cluster, and Django reads the same values to connect. They cannot drift apart.

2. Start all three services:

   ```bash
   docker compose up --build
   ```

   The `web` service waits on the database's `pg_isready` healthcheck, not merely on its container starting, so Django never opens its first connection too early.

3. Apply migrations:

   ```bash
   docker compose exec web python manage.py migrate
   ```

4. Optionally create an admin login to browse the audit log:

   ```bash
   docker compose exec web python manage.py createsuperuser
   ```

| URL | Service |
| --- | --- |
| http://localhost:5173 | React client |
| http://localhost:8000/api/v1/ | REST API (browsable) |
| http://localhost:8000/graphql/ | GraphiQL |
| http://localhost:8000/admin/ | Django admin (audit log) |

Source directories are bind-mounted, so Django's auto-reloader and Vite's hot module replacement both pick up edits without a rebuild. Rebuild (`docker compose build`) only after changing `requirements.txt` or `frontend/package.json`.

Database contents live in the `postgres-data` named volume and survive `docker compose down`. `docker compose down -v` destroys them, including the admin user; the Postgres image only runs its initialization while the volume is empty, so changing `POSTGRES_USER` or `POSTGRES_PASSWORD` later requires that reset.

### Common commands

```bash
docker compose exec web python manage.py shell         # Django shell
docker compose exec web python manage.py makemigrations
docker compose exec web python manage.py migrate
docker compose exec web python manage.py test          # see Tests, above
docker compose exec db psql -U djangraphberry -d djangraphberry   # SQL prompt
docker compose build web                               # after a dependency change
docker compose logs -f frontend                        # follow one service's logs
```

## Configuration

| Setting | Source | Purpose |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | environment (required) | Django cryptographic signing |
| `DJANGO_DEBUG` | environment | `1` enables debug mode |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | environment (required) | Database name and credentials, shared by the `db` and `web` services |
| `POSTGRES_HOST` / `POSTGRES_PORT` | environment | Default to `db` and `5432`, the Compose service address |
| `FRANKFURTER_BASE_URL` | `config/settings.py` | Exchange-rate provider |
| `COINGECKO_BASE_URL` | `config/settings.py` | Crypto-price provider |
| `UPSTREAM_TIMEOUT_SECONDS` | `config/settings.py` | Per-request timeout for outbound calls |
| `CACHES` | `config/settings.py` | Cache backend (`LocMemCache` in development) |
| `CURRENCIES_CACHE_SECONDS` | `config/settings.py` | TTL for the supported-currency list |
| `EXCHANGE_RATE_CACHE_SECONDS` | `config/settings.py` | TTL for exchange rates |
| `CRYPTO_CACHE_SECONDS` | `config/settings.py` | TTL for crypto prices |
| `NOT_FOUND_CACHE_SECONDS` | `config/settings.py` | TTL for cached not-found results |

The TTL settings are read per call rather than at import, so `override_settings` adjusts them in tests; a TTL of `0` disables caching for that data without a separate code path.

Database credentials use `os.environ[...]` so a missing value fails at startup rather than silently falling back; host and port have defaults, being infrastructure rather than secrets. The connection is configured with `CONN_MAX_AGE` so it is reused across requests, and `CONN_HEALTH_CHECKS` so a connection left stale by a database restart is replaced rather than handed to a request.

Upstream base URLs are settings rather than constants so they can be pointed at a stub server in tests.

Neither provider requires an API key. CoinGecko's keyless tier is rate limited and returns `429` under load, which the service surfaces as `502` / `UPSTREAM_UNAVAILABLE`; a free CoinGecko demo key can be supplied to raise that limit.

## Project layout

```
.
├── compose.yaml          Three services: db (PostgreSQL), web (Django), frontend (Vite)
├── Dockerfile            Django image
├── config/               Settings, root URLconf, WSGI/ASGI
│   ├── settings.py
│   └── urls.py           /admin/, /api/<version>/, /graphql/
├── markets/              The application
│   ├── clients.py        Upstream HTTP clients
│   ├── cache.py          Caching decorator, freshness ledger, HTTP cache headers
│   ├── serializers.py    Input validation and output shaping
│   ├── views.py          DRF viewsets and views
│   ├── exceptions.py     Domain exceptions and problem-details error handling
│   ├── schema.py         Strawberry GraphQL schema
│   ├── models.py         PriceLookup, the audit-log table
│   ├── audit.py          Per-request lookup ledger and its middleware
│   ├── admin.py          Read-only admin for the audit log
│   ├── migrations/
│   ├── urls.py           Router registrations
│   └── tests/            Suites for REST, GraphQL, caching and the audit log
├── requirements.txt
└── frontend/             React + TypeScript client (Vite)
    ├── Dockerfile
    ├── vite.config.ts    Dev-server proxy to the Django service
    └── src/
        ├── App.tsx       Controls, snapshot table, refresh
        ├── api.ts        GraphQL queries and freshness parsing
        └── cache.ts      Keyed client-side cache
```

## Planned work

- `504` for upstream timeouts, distinct from `502` for upstream errors.
- Deduplication of concurrent identical upstream requests, so a cold cache under load produces one call rather than several.
- `400` rather than `404` when the crypto endpoint receives an unsupported `?currency=`.
- A retention policy for the audit table, since it grows without bound.
- Exchange-rate lookups in the audit log; only crypto lookups are recorded today, because those are what the client table shows.

import { useEffect, useState, useSyncExternalStore } from 'react'
import {
  fetchCurrencies,
  fetchMarketSnapshot,
  type Currency,
  type MarketSnapshot,
} from './api'
import { invalidate, readCache, subscribe, writeCache } from './cache'

const COINS = [
  { id: 'bitcoin', label: 'Bitcoin' },
  { id: 'ethereum', label: 'Ethereum' },
  { id: 'solana', label: 'Solana' },
]

const BASE_CURRENCY = 'USD'

function isAbort(err: unknown): boolean {
  return err instanceof Error && err.name === 'AbortError'
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

function formatMoney(amount: string, currency: string): string {
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency,
    maximumFractionDigits: 2,
  }).format(Number(amount))
}

function formatPercent(value: string | null): string {
  if (value === null) return '—'
  const number = Number(value)
  return `${number >= 0 ? '+' : ''}${number.toFixed(2)}%`
}

function formatTime(timestamp: number): string {
  return new Intl.DateTimeFormat(undefined, { timeStyle: 'medium' }).format(timestamp)
}

/** Fetch a snapshot and store it under `key`. Writing to the cache is what
 *  notifies the component; nothing returns through React state. */
async function loadSnapshot(
  coinId: string,
  currency: string,
  key: string,
  signal?: AbortSignal,
): Promise<void> {
  const result = await fetchMarketSnapshot(coinId, currency, signal)
  writeCache(key, result.data, result.maxAge, result.fetchedAt)
}

export default function App() {
  const [coinId, setCoinId] = useState(COINS[0].id)
  const [currency, setCurrency] = useState('EUR')

  const [currencies, setCurrencies] = useState<Currency[]>([])
  const [listError, setListError] = useState<string | null>(null)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [error, setError] = useState<{ key: string; message: string } | null>(null)

  const cacheKey = `snapshot:${coinId}:${currency}`

  const entry = useSyncExternalStore(subscribe, () => readCache<MarketSnapshot>(cacheKey))

  const failure = error?.key === cacheKey ? error.message : null
  const isLoading = !entry && !failure

  useEffect(() => {
    const controller = new AbortController()

    fetchCurrencies(controller.signal)
      .then((all) => setCurrencies(all.filter((one) => one.code !== BASE_CURRENCY)))
      .catch((err: unknown) => {
        if (isAbort(err)) return
        setListError(message(err))
      })

    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (entry || failure) return

    const controller = new AbortController()

    loadSnapshot(coinId, currency, cacheKey, controller.signal).catch((err: unknown) => {
      if (isAbort(err)) return
      setError({ key: cacheKey, message: message(err) })
    })

    return () => controller.abort()
  }, [cacheKey, coinId, currency, entry, failure])

  // A refresh is a user event, not a synchronization concern, so it fetches
  // here rather than through an effect.
  function refresh() {
    setIsRefreshing(true)
    setError(null)
    invalidate(cacheKey)

    loadSnapshot(coinId, currency, cacheKey)
      .catch((err: unknown) => setError({ key: cacheKey, message: message(err) }))
      .finally(() => setIsRefreshing(false))
  }

  const price = entry?.value.cryptoPrice
  const converted = price?.converted[0]

  return (
    <main>
      <h1>Market snapshot</h1>

      <form className="controls" onSubmit={(event) => event.preventDefault()}>
        <label>
          Cryptocurrency
          <select value={coinId} onChange={(event) => setCoinId(event.target.value)}>
            {COINS.map((coin) => (
              <option key={coin.id} value={coin.id}>
                {coin.label}
              </option>
            ))}
          </select>
        </label>

        <label>
          Convert to
          <select
            value={currency}
            onChange={(event) => setCurrency(event.target.value)}
            disabled={currencies.length === 0}
          >
            {currencies.map((one) => (
              <option key={one.code} value={one.code}>
                {one.code} — {one.name}
              </option>
            ))}
          </select>
        </label>

        <button type="button" onClick={refresh} disabled={isLoading || isRefreshing}>
          Refresh
        </button>

        {(isLoading || isRefreshing) && <span className="status">Updating…</span>}
      </form>

      {listError && <p role="alert">Could not load the currency list: {listError}</p>}
      {failure && <p role="alert">Could not load market data: {failure}</p>}

      {!failure && !isLoading && !price && <p role="alert">No price found for “{coinId}”.</p>}

      {entry && price && (
        <>
          <table>
            <thead>
              <tr>
                <th>Coin</th>
                <th>Rate date</th>
                <th>Price ({BASE_CURRENCY})</th>
                <th>24h change</th>
                <th>Price ({converted?.currency ?? currency})</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>{price.id}</td>
                <td>{entry.value.exchangeRates?.date ?? '—'}</td>
                <td className="numeric">{formatMoney(price.price, BASE_CURRENCY)}</td>
                <td className={`numeric ${Number(price.change24hPercent ?? 0) < 0 ? 'down' : 'up'}`}>
                  {formatPercent(price.change24hPercent)}
                </td>
                <td className="numeric">
                  {converted ? formatMoney(converted.price, converted.currency) : '—'}
                </td>
              </tr>
            </tbody>
          </table>

          <p className="status">As of {formatTime(entry.fetchedAt)}</p>
        </>
      )}
    </main>
  )
}
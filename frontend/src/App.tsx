import { useEffect, useState } from 'react'
import {
  fetchCurrencies,
  fetchMarketSnapshot,
  type Currency,
  type MarketSnapshot,
} from './api'

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

export default function App() {
  const [coinId, setCoinId] = useState(COINS[0].id)
  const [currency, setCurrency] = useState('EUR')

  const [currencies, setCurrencies] = useState<Currency[]>([])
  const [snapshot, setSnapshot] = useState<MarketSnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    const controller = new AbortController()

    fetchCurrencies(controller.signal)
      .then((all) => setCurrencies(all.filter((one) => one.code !== BASE_CURRENCY)))
      .catch((err: unknown) => {
        if (isAbort(err)) return
        setError(message(err))
      })

    return () => controller.abort()
  }, [])

  useEffect(() => {
    const controller = new AbortController()

    setIsLoading(true)
    setError(null)

    fetchMarketSnapshot(coinId, currency, controller.signal)
      .then(setSnapshot)
      .catch((err: unknown) => {
        if (isAbort(err)) return
        setSnapshot(null)
        setError(message(err))
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoading(false)
      })

    return () => controller.abort()
  }, [coinId, currency])

  const price = snapshot?.cryptoPrice
  const converted = price?.converted[0]

  return (
    <main>
      <h1>Market snapshot</h1>

      <form className="controls">
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

        {isLoading && <span className="status">Updating…</span>}
      </form>

      {error && <p role="alert">Could not load market data: {error}</p>}

      {!error && !price && !isLoading && <p role="alert">No price found for “{coinId}”.</p>}

      {price && (
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
              <td>{snapshot?.exchangeRates?.date ?? '—'}</td>
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
      )}
    </main>
  )
}
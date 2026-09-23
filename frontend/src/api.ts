export type ConvertedPrice = {
  currency: string
  price: string
}

export type CryptoPrice = {
  id: string
  price: string
  change24hPercent: string | null
  converted: ConvertedPrice[]
}

export type MarketSnapshot = {
  cryptoPrice: CryptoPrice | null
  exchangeRates: { date: string } | null
}

type GraphQLResponse<T> = {
  data?: T | null
  errors?: { message: string }[]
}

/** A payload plus the freshness the server advertised for it. */
export type Fresh<T> = {
  data: T
  maxAge: number | null
  fetchedAt: number
}

function parseMaxAge(header: string | null): number | null {
  if (!header) return null
  const match = /max-age=(\d+)/i.exec(header)
  return match ? Number(match[1]) : null
}

export async function graphqlRequest<T>(
  query: string,
  variables: Record<string, unknown> = {},
  signal?: AbortSignal,
): Promise<Fresh<T>> {
  const response = await fetch('/graphql/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, variables }),
    signal,
  })

  if (!response.ok) {
    throw new Error(`Request failed with HTTP ${response.status}`)
  }

  const result = (await response.json()) as GraphQLResponse<T>

  if (result.errors?.length) {
    throw new Error(result.errors.map((error) => error.message).join('; '))
  }
  if (!result.data) {
    throw new Error('The server returned no data.')
  }
  return {
    data: result.data,
    maxAge: parseMaxAge(response.headers.get('Cache-Control')),
    fetchedAt: Date.now(),
  }
}

export const MARKET_SNAPSHOT_QUERY = `
  query MarketSnapshot($coinId: String!, $currency: String!) {
    cryptoPrice(id: $coinId, currency: "usd") {
      id
      price
      change24hPercent
      converted(to: [$currency]) {
        currency
        price
      }
    }
    exchangeRates(base: "USD", symbols: [$currency]) {
      date
    }
  }
`

export function fetchMarketSnapshot(
  coinId: string,
  currency: string,
  signal?: AbortSignal,
): Promise<Fresh<MarketSnapshot>> {
  return graphqlRequest<MarketSnapshot>(MARKET_SNAPSHOT_QUERY, { coinId, currency }, signal)
}

export type Currency = {
  code: string
  name: string
}

type CurrenciesData = {
  currencies: Currency[] | null
}

export const CURRENCIES_QUERY = `
  query Currencies {
    currencies {
      code
      name
    }
  }
`

export async function fetchCurrencies(signal?: AbortSignal): Promise<Currency[]> {
  const { data } = await graphqlRequest<CurrenciesData>(CURRENCIES_QUERY, {}, signal)
  return data.currencies ?? []
}
/** A tiny in-memory cache whose entries expire when the server says they should.
 *
 *  It lives outside React: module state survives re-renders and StrictMode's
 *  double-mount, and components read it through useSyncExternalStore.
 */

const DEFAULT_MAX_AGE_SECONDS = 60

export type CacheEntry<T> = {
  value: T
  fetchedAt: number
  expiresAt: number
}

const entries = new Map<string, CacheEntry<unknown>>()
const listeners = new Set<() => void>()

function notify(): void {
  for (const listener of listeners) listener()
}

/** Subscribe to writes. Returns an unsubscribe function, as React expects. */
export function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** Returns the stored entry, or null if absent or expired.
 *  The same object identity is returned until the key is written again, which
 *  is what lets useSyncExternalStore avoid redundant re-renders. */
export function readCache<T>(key: string): CacheEntry<T> | null {
  const entry = entries.get(key) as CacheEntry<T> | undefined
  if (!entry) return null

  if (Date.now() >= entry.expiresAt) {
    entries.delete(key)
    return null
  }
  return entry
}

export function writeCache<T>(
  key: string,
  value: T,
  maxAgeSeconds: number | null,
  fetchedAt: number = Date.now(),
): void {
  const maxAge = maxAgeSeconds ?? DEFAULT_MAX_AGE_SECONDS
  if (maxAge <= 0) {
    entries.delete(key)
  } else {
    entries.set(key, { value, fetchedAt, expiresAt: fetchedAt + maxAge * 1000 })
  }
  notify()
}

export function invalidate(key: string): void {
  entries.delete(key)
  notify()
}
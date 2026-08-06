import { isCatalogUrl } from './api'

/** What the user last asked for, so an empty homepage has something to offer.
 *
 *  Deliberately only the input they typed — not results, not covers. Replaying
 *  it runs the same code path as typing it again, so a stale entry costs a
 *  fresh search rather than showing a cached page that no longer exists. */
export interface RecentSearch {
  /** Exactly what was submitted: a query, or a catalog URL. */
  input: string
  /** True when the input was a link — the chip labels itself differently. */
  isLink: boolean
  at: number
}

const KEY = 'unstream:recent'
/** Six fits two rows of chips under the hero without crowding it. */
const MAX = 6

export function recentSearches(): RecentSearch[] {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return (parsed as RecentSearch[])
      .filter((r) => r && typeof r.input === 'string' && r.input.length > 0)
      .slice(0, MAX)
  } catch {
    return [] // storage disabled, or a shape from an older release
  }
}

/** Record a submission, most recent first, without duplicating an entry the
 *  user is simply repeating. */
export function rememberSearch(input: string): RecentSearch[] {
  const trimmed = input.trim()
  if (!trimmed) return recentSearches()
  const entry: RecentSearch = { input: trimmed, isLink: isCatalogUrl(trimmed), at: Date.now() }
  const next = [entry, ...recentSearches().filter((r) => r.input !== trimmed)].slice(0, MAX)
  try {
    localStorage.setItem(KEY, JSON.stringify(next))
  } catch {
    // Nothing to recover from — the chips are a convenience, not state.
  }
  return next
}

export function clearRecentSearches(): RecentSearch[] {
  try {
    localStorage.removeItem(KEY)
  } catch {
    // As above.
  }
  return []
}

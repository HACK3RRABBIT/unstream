import { isCatalogUrl } from './api'

/** Something the user submitted before: a query, or a catalog URL. Only the
 *  input is kept — replaying a chip runs the same code path as typing it, so a
 *  stale one costs a fresh search rather than showing a page that has since
 *  disappeared. */
export interface RecentSearch {
  input: string
  isLink: boolean
  at: number
}

const KEY = 'unstream:recent'
/** Two rows of chips under the hero without crowding it. */
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

export function rememberSearch(input: string): RecentSearch[] {
  const trimmed = input.trim()
  if (!trimmed) return recentSearches()
  const entry: RecentSearch = { input: trimmed, isLink: isCatalogUrl(trimmed), at: Date.now() }
  const next = [entry, ...recentSearches().filter((r) => r.input !== trimmed)].slice(0, MAX)
  try {
    localStorage.setItem(KEY, JSON.stringify(next))
  } catch {
    // The chips are a convenience, not state — nothing to recover.
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

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

/** Which catalog a recent search belongs to. The music and anime tabs keep
 *  their own history — a chip is replayed on the tab it came from. */
export type RecentDomain = 'music' | 'anime'

const KEYS: Record<RecentDomain, string> = {
  music: 'unstream:recent',
  anime: 'unstream:recent-anime',
}
/** Two rows of chips under the hero without crowding it. */
const MAX = 6

function key(domain: RecentDomain): string {
  return KEYS[domain]
}

export function recentSearches(domain: RecentDomain = 'music'): RecentSearch[] {
  try {
    const raw = localStorage.getItem(key(domain))
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

export function rememberSearch(input: string, domain: RecentDomain = 'music'): RecentSearch[] {
  const trimmed = input.trim()
  if (!trimmed) return recentSearches(domain)
  const entry: RecentSearch = { input: trimmed, isLink: isCatalogUrl(trimmed), at: Date.now() }
  const next = [entry, ...recentSearches(domain).filter((r) => r.input !== trimmed)].slice(0, MAX)
  try {
    localStorage.setItem(key(domain), JSON.stringify(next))
  } catch {
    // The chips are a convenience, not state — nothing to recover.
  }
  return next
}

export function clearRecentSearches(domain: RecentDomain = 'music'): RecentSearch[] {
  try {
    localStorage.removeItem(key(domain))
  } catch {
    // As above.
  }
  return []
}

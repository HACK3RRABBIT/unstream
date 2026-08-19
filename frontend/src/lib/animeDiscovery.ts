import { useQuery } from '@tanstack/react-query'
import { getAnimeSources, type AnimeSource } from './api'

/** One authoritative discovery probe: the states the header quality picker
 *  (and the per-episode availability line) share when determining which
 *  resolutions a set of episodes really serves.
 *
 *  `loading`/`error`/`unknown` are NOT availability verdicts — only `ready`
 *  (a resolved list of provider sources) lets the picker render choices. An
 *  unknown provider never creates or removes a resolution (req 5). */
export type DiscoveryState =
  | { kind: 'loading' }
  | { kind: 'error' }
  | { kind: 'unknown' }
  | { kind: 'ready'; sources: AnimeSource[] }

/** Probe availability for one anime season — the discovery the header picker
 *  renders from. Shares the exact query AnimeSeasonView runs (`anime-sources`,
 *  same staleTime), so React-query fires one network request and both
 *  consumers see the same result.
 *
 *  Pass `null` for a non-season context (music tab, anime search, franchise
 *  detail): the probe is never asked and the picker falls back to the only
 *  honest choice, `original`. */
export function useAnimeSeasonDiscovery(
  media_id: number | null,
  season: number | null,
): { discovery: DiscoveryState; refetch: () => void } {
  const enabled = media_id != null && season != null
  const query = useQuery({
    queryKey: ['anime-sources', media_id, season],
    queryFn: () => getAnimeSources(media_id!, season!),
    staleTime: 5 * 60 * 1000,
    enabled,
  })
  const refetch = query.refetch
  // A disabled query sits in `status: 'pending'` — that must NOT read as
  // "checking". Without a season context there is nothing to discover, so the
  // picker gets an empty ready set (→ only `original`).
  if (!enabled) return { discovery: { kind: 'ready', sources: [] }, refetch }
  if (query.status === 'error') return { discovery: { kind: 'error' }, refetch }
  if (query.status === 'pending') return { discovery: { kind: 'loading' }, refetch }
  const sources = query.data?.providers
  if (sources == null) return { discovery: { kind: 'unknown' }, refetch }
  return { discovery: { kind: 'ready', sources }, refetch }
}

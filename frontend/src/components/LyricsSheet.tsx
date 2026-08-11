import {
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Captions,
  CaptionsOff,
  Check,
  CloudOff,
  Copy,
  RefreshCcw,
  TriangleAlert,
  X,
} from 'lucide-react'
import clsx from 'clsx'
import { apiError, getLyrics, type Track } from '../lib/api'
import { faNumerals, useMessages, useStartAlign } from '../lib/i18n'
import { useToast } from '../lib/toast'
import { Sheet } from './Sheet'

interface Props {
  track: Track
  onClose: () => void
}

/** Shown in the footer. Named rather than hidden because the sources are not
 *  equally trustworthy — a lyrics.ovh hit is matched on nothing but the title,
 *  so when a lyric looks wrong, this is the line that explains why.
 *
 *  Not translated: these are the sources' own names. */
const SOURCE_LABEL: Record<string, string> = {
  'lrclib-get': 'LRCLIB',
  'lrclib-search': 'LRCLIB',
  genius: 'Genius',
  'lyrics-ovh': 'lyrics.ovh',
}

/** Whether the scroll area is hiding content above or below it, so the fades
 *  are drawn only when true. Re-measured whenever the body changes: the
 *  skeleton, an empty state and a full lyric are three different heights. */
function useScrollEdges(body: unknown) {
  const ref = useRef<HTMLDivElement | null>(null)
  const [edges, setEdges] = useState({ top: false, bottom: false })

  const measure = useCallback(() => {
    const el = ref.current
    if (!el) return
    setEdges({
      top: el.scrollTop > 6,
      bottom: el.scrollHeight - el.scrollTop - el.clientHeight > 6,
    })
  }, [])

  useLayoutEffect(() => {
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [measure, body])

  return { ref, edges, onScroll: measure }
}

/** The lyric text, cut into verses by blank lines so the spacing reads like a
 *  lyric sheet rather than one unbroken wall.
 *
 *  `dir="auto"` sits on each verse, not on the wrapper: it resolves from the
 *  first strong character of whatever it is on, so one wrapper would hand the
 *  whole sheet the direction of its opening word — and a Persian song with an
 *  English intro would then run every Persian verse backwards. `faNumerals`
 *  is applied per verse for the same reason: a digit belongs to the script
 *  around it, not to the language of the UI reading it. */
function Verses({ text }: { text: string }) {
  const verses = text.split(/\n{2,}/).map((v) => v.trim())
  return (
    <div className="stagger-verses mx-auto max-w-[34rem]">
      {verses.map((verse, i) => (
        <div
          key={i}
          dir="auto"
          style={{ '--i': i } as CSSProperties}
          className={clsx('mb-7 last:mb-0', faNumerals(verse))}
        >
          {verse.split('\n').map((line, j) => (
            <p key={j} className="text-lyric text-pretty text-ink-100/90">
              {line}
            </p>
          ))}
        </div>
      ))}
    </div>
  )
}

/** Verse-shaped loading blocks: three stanzas of a few lines, rather than a
 *  uniform stack, so the wait looks like the thing that is arriving. */
function VerseSkeleton() {
  return (
    <div className="mx-auto max-w-[34rem] space-y-7" aria-busy>
      {[4, 3, 4].map((lines, verse) => (
        <div key={verse} className="space-y-3">
          {Array.from({ length: lines }, (_, i) => (
            <div
              key={i}
              className="shimmer h-4 rounded-ctl"
              // Ragged right edge, deterministic per position — lyrics are not
              // justified prose and a column of equal bars reads as a table.
              style={{ width: `${[92, 74, 84, 63][(verse + i) % 4]}%` }}
            />
          ))}
        </div>
      ))}
    </div>
  )
}

/** A centred "nothing here" state, shared by all three so the only difference
 *  the user sees is the words and whether a retry is offered. */
function Empty({
  icon,
  title,
  hint,
  tone = 'neutral',
  retryLabel,
  onRetry,
  retrying = false,
}: {
  icon: ReactNode
  title: string
  hint?: string
  tone?: 'neutral' | 'danger'
  retryLabel: string
  onRetry?: () => void
  retrying?: boolean
}) {
  return (
    <div className="flex animate-fade-up flex-col items-center gap-4 py-14 text-center">
      <span
        className={clsx(
          'grid size-14 animate-pop place-items-center rounded-full ring-1',
          tone === 'danger'
            ? 'bg-danger/8 text-danger ring-danger/25'
            : 'bg-ink-800/60 text-ink-400 ring-ink-700',
        )}
      >
        {icon}
      </span>
      <p className="max-w-64 text-body font-medium text-balance text-ink-100">{title}</p>
      {hint && <p className="max-w-60 text-mini text-pretty text-ink-400">{hint}</p>}
      {onRetry && (
        <button
          onClick={onRetry}
          disabled={retrying}
          className="mt-1 flex items-center gap-1.5 rounded-btn border border-ink-600 px-4 py-2 text-mini font-medium text-ink-100 transition duration-200 hover:border-ink-400 hover:bg-ink-800 active:scale-95 disabled:opacity-60"
        >
          <RefreshCcw className={clsx('size-3.5', retrying && 'animate-spin')} />
          {retryLabel}
        </button>
      )}
    </div>
  )
}

/** The lyric itself. Everything about being a sheet — the scrim, the drag,
 *  Escape, the focus trap — belongs to `Sheet`; what is left here is the one
 *  thing that is about lyrics. */
export function LyricsSheet({ track, onClose }: Props) {
  const { push } = useToast()
  const m = useMessages()
  const startAlign = useStartAlign()
  const [copied, setCopied] = useState(false)

  // Lyrics are read, not commanded — the one fetch in this app that is a
  // query rather than a mutation. The cache is the point: reopening a sheet
  // during a session should be instant, and a lyric does not change under us.
  //
  // The ref, not state, is what carries "this fetch is a retry": it must reach
  // the query function without changing the key (which would orphan the cache
  // entry) and without a re-render (which would race the refetch it triggers).
  const retrying = useRef(false)
  const { data, isPending, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['lyrics', track.id],
    queryFn: async () => {
      const force = retrying.current
      retrying.current = false
      return getLyrics(track, force)
    },
    staleTime: Infinity,
    retry: false,
  })

  const plain = data?.plain ?? null
  const { ref: scrollRef, edges, onScroll } = useScrollEdges(plain ?? data?.status ?? isPending)

  // Asking again has to mean asking the *sources* again, or the button is a
  // decoration: the backend caches an unreachable source for minutes.
  const retry = useCallback(() => {
    retrying.current = true
    return refetch()
  }, [refetch])

  const copyLyrics = useCallback(async () => {
    if (!plain) return
    try {
      await navigator.clipboard.writeText(plain)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
      push(m.lyrics.copied, 'success')
    } catch {
      push(m.lyrics.copyFailed, 'error')
    }
  }, [plain, push, m])

  return (
    <Sheet label={m.lyrics.dialog(track.title)} onClose={onClose}>
      <header className="relative shrink-0 overflow-hidden border-b border-ink-800">
        {/* Decoration only: aria-hidden, and the content above it sits on an
            opaque wash so contrast never depends on which cover turned up. */}
        {track.cover_url && (
          <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
            <img
              src={track.cover_url}
              alt=""
              className="size-full animate-wash-in object-cover opacity-30 blur-2xl saturate-150"
            />
            <div className="absolute inset-0 bg-linear-to-b from-ink-900/75 to-ink-900/95" />
          </div>
        )}

        <div className="relative flex items-center gap-3 px-4 py-3.5 sm:px-5">
          {track.cover_url ? (
            <img
              src={track.cover_url}
              alt=""
              className="size-12 shrink-0 rounded-btn object-cover ring-1 ring-ink-100/10"
            />
          ) : (
            <span className="grid size-12 shrink-0 place-items-center rounded-btn bg-ink-800 text-ink-400 ring-1 ring-ink-700">
              <Captions className="size-5" />
            </span>
          )}

          {/* `dir="auto"` shapes and truncates the title correctly but also
              drives `text-align: start`, which put an English title on the
              opposite edge from its own eyebrow. Alignment comes from the UI's
              direction instead — same trade as the track rows. */}
          <div className={clsx('min-w-0 flex-1', startAlign)}>
            <p className="text-micro font-semibold text-ink-400">{m.lyrics.label}</p>
            <h2
              className={clsx(
                'mt-0.5 truncate font-display text-body font-bold text-ink-100',
                startAlign,
              )}
              dir="auto"
            >
              {track.title}
            </h2>
            <p className={clsx('truncate text-micro text-ink-400', startAlign)} dir="auto">
              {track.artists.join(', ')}
            </p>
          </div>

          <div className="flex shrink-0 items-center gap-1">
            {plain && (
              <button
                onClick={copyLyrics}
                title={m.lyrics.copy}
                aria-label={m.lyrics.copy}
                className={clsx(
                  'tap-target grid size-9 animate-pop place-items-center rounded-btn border transition duration-200 active:scale-90',
                  copied
                    ? 'border-lime-flash/50 bg-lime-flash/10 text-lime-flash'
                    : 'border-ink-600 text-ink-400 hover:border-ink-400 hover:text-ink-100',
                )}
              >
                {copied ? (
                  <Check className="size-4 animate-pop" strokeWidth={3} />
                ) : (
                  <Copy className="size-4" />
                )}
              </button>
            )}
            <button
              onClick={onClose}
              aria-label={m.lyrics.close}
              className="tap-target grid size-9 place-items-center rounded-btn text-ink-400 transition duration-200 hover:bg-ink-800 hover:text-ink-100 active:scale-90"
            >
              <X className="size-4" />
            </button>
          </div>
        </div>
      </header>

      {/* `min-h-0 flex-auto` is what makes this scroll at all: without the
          `min-h-0` the item refuses to shrink below its content, so the words
          grew the panel past its `max-h` and `overflow-hidden` clipped them.
          Not `flex-1` — `flex-basis: 0` sizes from free space, and a panel of
          `auto` height under a `max-h` has none to give, so a short lyric
          would collapse. The fades are `sticky` for the same reason: an
          absolute overlay would need a wrapper, and a wrapper has no in-flow
          content to be sized from. */}
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="min-h-0 flex-auto overflow-y-auto overscroll-contain px-5 py-7 sm:px-7"
      >
        <div
          aria-hidden
          className={clsx(
            'pointer-events-none sticky top-0 z-10 -mx-5 -mb-8 h-8 bg-linear-to-b from-ink-900 to-transparent transition-opacity duration-200 sm:-mx-7',
            edges.top ? 'opacity-100' : 'opacity-0',
          )}
        />

        {isPending && <VerseSkeleton />}

        {/* Three ways to have no words, and they are not the same news.
                  A request that never landed, a source that would not answer,
                  and a song the catalogs genuinely do not carry — the first two
                  are worth a retry button and the third is not. */}
        {isError && (
          <Empty
            tone="danger"
            icon={<TriangleAlert className="size-6" />}
            title={apiError(error, m)}
            retryLabel={m.lyrics.retry}
            onRetry={retry}
            retrying={isFetching}
          />
        )}

        {data?.status === 'unavailable' && (
          <Empty
            icon={<CloudOff className="size-6" />}
            title={m.lyrics.unavailable.title}
            hint={m.lyrics.unavailable.hint}
            retryLabel={m.lyrics.retry}
            onRetry={retry}
            retrying={isFetching}
          />
        )}

        {data?.status === 'absent' && (
          <Empty
            icon={<CaptionsOff className="size-6" />}
            title={m.lyrics.absent.title}
            hint={m.lyrics.absent.hint}
            retryLabel={m.lyrics.retry}
          />
        )}

        {plain && (
          <>
            <Verses text={plain} />
            <footer className="mx-auto mt-10 flex max-w-[34rem] items-center justify-between border-t border-ink-800 pt-3.5 text-micro text-ink-400">
              <span>{m.lyrics.source}</span>
              <span dir="ltr" className="font-medium text-ink-300">
                {SOURCE_LABEL[data?.source ?? ''] ?? data?.source ?? ''}
              </span>
            </footer>
          </>
        )}

        <div
          aria-hidden
          className={clsx(
            'pointer-events-none sticky bottom-0 z-10 -mx-5 -mt-10 h-10 bg-linear-to-t from-ink-900 to-transparent transition-opacity duration-200 sm:-mx-7',
            edges.bottom ? 'opacity-100' : 'opacity-0',
          )}
        />
      </div>
    </Sheet>
  )
}

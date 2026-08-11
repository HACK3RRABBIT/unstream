import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
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
import { useToast } from '../lib/toast'

interface Props {
  track: Track
  onClose: () => void
}

/** Shown in the footer. Named rather than hidden because the sources are not
 *  equally trustworthy — a lyrics.ovh hit is matched on nothing but the title,
 *  so when a lyric looks wrong, this is the line that explains why. */
const SOURCE_LABEL: Record<string, string> = {
  'lrclib-get': 'LRCLIB',
  'lrclib-search': 'LRCLIB',
  genius: 'Genius',
  'lyrics-ovh': 'lyrics.ovh',
}

/** How far the sheet must be dragged down before letting go dismisses it.
 *  Same distance as the downloads dock, so the gesture feels like one app. */
const DISMISS_AFTER_PX = 90

/** The lyric text, cut into verses by blank lines so the spacing reads like a
 *  lyric sheet rather than one unbroken wall.
 *
 *  `dir="auto"` sits on each verse, not on the wrapper: it resolves from the
 *  first strong character of whatever it is on, so one wrapper would hand the
 *  whole sheet the direction of its opening word — and a Persian song with an
 *  English intro would then run every Persian verse backwards.
 *
 *  Verses arrive on the same staggered `rise` the track lists use. */
function Verses({ text }: { text: string }) {
  const verses = text.split(/\n{2,}/).map((v) => v.trim())
  return (
    <div className="stagger mx-auto max-w-[34rem]">
      {verses.map((verse, i) => (
        <div key={i} dir="auto" style={{ '--i': i } as CSSProperties} className="mb-6 last:mb-0">
          {verse.split('\n').map((line, j) => (
            <p key={j} className="text-body leading-8 text-pretty text-ink-100/90">
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
    <div className="mx-auto max-w-[34rem] space-y-6" aria-busy>
      {[4, 3, 4].map((lines, verse) => (
        <div key={verse} className="space-y-2.5">
          {Array.from({ length: lines }, (_, i) => (
            <div
              key={i}
              className="shimmer h-3.5 rounded-ctl"
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
  onRetry,
  retrying = false,
}: {
  icon: ReactNode
  title: string
  hint?: string
  tone?: 'neutral' | 'danger'
  onRetry?: () => void
  retrying?: boolean
}) {
  return (
    <div className="flex animate-fade-up flex-col items-center gap-4 py-12 text-center">
      <span
        className={clsx(
          'grid size-12 place-items-center rounded-full',
          tone === 'danger' ? 'bg-danger/10 text-danger' : 'bg-ink-800 text-ink-400',
        )}
      >
        {icon}
      </span>
      <p className="max-w-60 text-mini text-ink-300">{title}</p>
      {hint && <p className="max-w-56 text-micro text-ink-400">{hint}</p>}
      {onRetry && (
        <button
          onClick={onRetry}
          disabled={retrying}
          className="flex items-center gap-1.5 rounded-btn border border-ink-600 px-4 py-2 text-mini font-medium text-ink-100 transition hover:border-ink-400 active:scale-95 disabled:opacity-60"
        >
          <RefreshCcw className={clsx('size-3.5', retrying && 'animate-spin')} />
          دوباره تلاش کن
        </button>
      )}
    </div>
  )
}

/** Lyrics as a bottom sheet on phones, a centered panel on desktop — the same
 *  scrim + drag + Escape + scroll-lock contract as the downloads dock. */
export function LyricsSheet({ track, onClose }: Props) {
  const { push } = useToast()
  const [copied, setCopied] = useState(false)
  const [drag, setDrag] = useState(0)
  const startY = useRef<number | null>(null)
  const panelRef = useRef<HTMLElement | null>(null)

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

  // Asking again has to mean asking the *sources* again, or the button is a
  // decoration: the backend caches an unreachable source for minutes.
  const retry = useCallback(() => {
    retrying.current = true
    return refetch()
  }, [refetch])

  // Escape belongs to this sheet while it's up; App's global handler would
  // otherwise navigate back, which is not what "close this" means here. Tab is
  // caught for the same reason: `aria-modal` tells a screen reader the page
  // behind is gone, and a keyboard must agree or focus walks out of it.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    panelRef.current?.focus()

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
        return
      }
      if (e.key !== 'Tab' || !panelRef.current) return
      const focusable = panelRef.current.querySelectorAll<HTMLElement>(
        'button, a[href], [tabindex]:not([tabindex="-1"])',
      )
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const active = document.activeElement
      if (e.shiftKey && (active === first || active === panelRef.current)) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && active === last) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.body.style.overflow = previous
      document.removeEventListener('keydown', onKey, true)
      // Back to the row's lyrics button, so a keyboard lands where it left.
      opener?.focus?.()
    }
  }, [onClose])

  const copyLyrics = useCallback(async () => {
    const text = data?.plain
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
      push('متن آهنگ کپی شد', 'success')
    } catch {
      push('کپی متن انجام نشد', 'error')
    }
  }, [data, push])

  const onPointerDown = (e: React.PointerEvent) => {
    startY.current = e.clientY
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onPointerMove = (e: React.PointerEvent) => {
    if (startY.current == null) return
    setDrag(Math.max(0, e.clientY - startY.current)) // downward only
  }
  const onPointerUp = () => {
    if (startY.current == null) return
    startY.current = null
    if (drag > DISMISS_AFTER_PX) {
      onClose() // unmounts; no point springing back first
      return
    }
    setDrag(0)
  }

  const plain = data?.plain ?? null

  // The sheet must anchor to the viewport, not to an ancestor: the view it is
  // mounted inside arrives with `animate-fade-up`, whose retained transform
  // would otherwise turn these `fixed` elements into descendants of that div
  // — `inset-0` then stretches across the whole content, not the screen.
  // Portaling to <body> is what keeps the dock and toasts viewport-fixed too.
  return createPortal(
    <>
      <div
        onClick={onClose}
        aria-hidden
        className="fixed inset-0 z-40 animate-scrim-in bg-ink-950/70 backdrop-blur-[2px]"
      />
      {/* Positioning lives on this grid so the panel's entrance animation can
          own `transform` without fighting a centering translate. */}
      <div className="pointer-events-none fixed inset-0 z-50 grid items-end justify-items-center sm:items-center">
        <section
          ref={panelRef}
          role="dialog"
          aria-modal="true"
          aria-label={`متن آهنگ ${track.title}`}
          tabIndex={-1}
          style={{ transform: drag ? `translateY(${drag}px)` : undefined }}
          className={clsx(
            'pointer-events-auto flex max-h-[85svh] w-full flex-col overflow-hidden bg-ink-900 shadow-2xl shadow-black/60 outline-none',
            // mobile: bottom sheet, sliding up from its own edge.
            'rounded-t-panel border-t border-ink-700 pb-[var(--safe-bottom)]',
            // desktop: a centered modal that fades in instead.
            'sm:w-[min(32rem,calc(100vw-2.5rem))] sm:rounded-panel sm:border sm:border-ink-700 sm:pb-0',
            // Both the entrance and the drag drive `transform`, so only one runs.
            drag ? 'transition-none' : 'animate-sheet-in sm:animate-fade-up',
          )}
        >
          {/* The handle is the drag target, not decoration — it only exists on
              the layout that is anchored to an edge it can be thrown back at.
              Padded rather than tall so the grab area clears a thumb while the
              bar itself stays a hairline. */}
          <div
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
            className="shrink-0 cursor-grab touch-none py-2.5 active:cursor-grabbing sm:hidden"
          >
            <div className="mx-auto h-1 w-9 rounded-full bg-ink-600" />
          </div>

          <header className="flex items-center gap-3 border-b border-ink-800 bg-ink-950/40 px-4 py-3 sm:px-5">
            {track.cover_url ? (
              <img
                src={track.cover_url}
                alt=""
                className="size-11 shrink-0 rounded-btn object-cover ring-1 ring-ink-700"
              />
            ) : (
              <span className="grid size-11 shrink-0 place-items-center rounded-btn bg-ink-800 text-ink-400">
                <Captions className="size-5" />
              </span>
            )}
            <div className="min-w-0 flex-1">
              <p className="text-micro font-semibold text-ink-400">متن آهنگ</p>
              <h2
                className="mt-0.5 truncate font-display text-mini font-bold text-ink-100"
                dir="auto"
              >
                {track.title}
              </h2>
              <p className="truncate text-micro text-ink-400" dir="auto">
                {track.artists.join(', ')}
              </p>
            </div>
            {plain && (
              <button
                onClick={copyLyrics}
                title="کپی متن آهنگ"
                aria-label="کپی متن آهنگ"
                className={clsx(
                  'tap-target grid size-9 shrink-0 place-items-center rounded-btn border transition duration-200 active:scale-90',
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
              aria-label="بستن متن آهنگ"
              className="tap-target grid size-9 shrink-0 place-items-center rounded-btn text-ink-400 transition hover:bg-ink-800 hover:text-ink-100"
            >
              <X className="size-4" />
            </button>
          </header>

          <div className="flex-1 overflow-y-auto overscroll-contain px-4 py-6 sm:px-6">
            {isPending && <VerseSkeleton />}

            {/* Three ways to have no words, and they are not the same news.
                A request that never landed, a source that would not answer,
                and a song the catalogs genuinely do not carry — the first two
                are worth a retry button and the third is not. */}
            {isError && (
              <Empty
                tone="danger"
                icon={<TriangleAlert className="size-5" />}
                title={apiError(error)}
                onRetry={retry}
                retrying={isFetching}
              />
            )}

            {data?.status === 'unavailable' && (
              <Empty
                icon={<CloudOff className="size-6" />}
                title="الان نشد متن رو بگیریم"
                hint="سرویس متن آهنگ جواب نداد. چند لحظه دیگه دوباره امتحان کن."
                onRetry={retry}
                retrying={isFetching}
              />
            )}

            {data?.status === 'absent' && (
              <Empty
                icon={<CaptionsOff className="size-6" />}
                title="متن این آهنگ پیدا نشد"
                hint="یا هنوز توی دیتابیس نیست، یا اسم آهنگ جور دیگه‌ای ثبت شده."
              />
            )}

            {plain && (
              <>
                <Verses text={plain} />
                <footer className="mx-auto mt-8 flex max-w-[34rem] items-center justify-between border-t border-ink-800 pt-3 text-micro text-ink-400">
                  <span>متن آهنگ</span>
                  <span dir="ltr">{SOURCE_LABEL[data?.source ?? ''] ?? data?.source ?? ''}</span>
                </footer>
              </>
            )}
          </div>
        </section>
      </div>
    </>,
    document.body,
  )
}

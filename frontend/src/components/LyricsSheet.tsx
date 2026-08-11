import { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { RefreshCcw, TriangleAlert } from 'lucide-react'
import clsx from 'clsx'
import { apiError, getLyrics, type Lyrics, type Track } from '../lib/api'
import { useToast } from '../lib/toast'
import { CheckIcon, CopyIcon, MusicNotesIcon, XIcon } from '../icons'

interface Props {
  track: Track
  onClose: () => void
}

type State =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'done'; lyrics: Lyrics }

const SOURCE_LABEL: Record<string, string> = {
  'lrclib-get': 'LRCLIB',
  'lrclib-search': 'LRCLIB',
  genius: 'Genius',
}

/** The lyric text, cut into verses by blank lines so the spacing reads like a
 *  lyric sheet rather than one unbroken wall. `dir="auto"` keeps English lines
 *  LTR and Persian lines RTL without the component knowing which it got. */
function Verses({ text }: { text: string }) {
  const verses = text.split(/\n{2,}/).map((v) => v.trim())
  return (
    <div dir="auto">
      {verses.map((verse, i) => (
        <div key={i} className="mb-5 last:mb-0">
          {verse.split('\n').map((line, j) => (
            <p key={j} className="text-body leading-8">
              {line}
            </p>
          ))}
        </div>
      ))}
    </div>
  )
}

/** Lyrics as a bottom sheet on phones, a centered panel on desktop — the same
 *  scrim + Escape + scroll-lock contract as the downloads dock. */
export function LyricsSheet({ track, onClose }: Props) {
  const { push } = useToast()
  const [state, setState] = useState<State>({ status: 'loading' })
  const [copied, setCopied] = useState(false)
  const [attempt, setAttempt] = useState(0)

  // Escape belongs to this sheet while it's up; App's global handler would
  // otherwise navigate back, which is not what "close this" means here.
  useEffect(() => {
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.stopPropagation()
      onClose()
    }
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.body.style.overflow = previous
      document.removeEventListener('keydown', onKey, true)
    }
  }, [onClose])

  useEffect(() => {
    let ignore = false
    setState({ status: 'loading' })
    getLyrics(track)
      .then((lyrics) => {
        if (!ignore) setState({ status: 'done', lyrics })
      })
      .catch((err) => {
        if (!ignore) setState({ status: 'error', message: apiError(err) })
      })
    return () => {
      ignore = true
    }
  }, [track, attempt])

  const copyLyrics = useCallback(async () => {
    if (state.status !== 'done' || !state.lyrics.plain) return
    try {
      await navigator.clipboard.writeText(state.lyrics.plain)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
      push('متن آهنگ کپی شد', 'success')
    } catch {
      push('کپی متن انجام نشد', 'error')
    }
  }, [state, push])

  const hasLyrics = state.status === 'done' && !!state.lyrics.plain

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
          role="dialog"
          aria-modal="true"
          aria-label={`متن آهنگ ${track.title}`}
          className={clsx(
            'pointer-events-auto flex max-h-[85svh] w-full flex-col overflow-hidden bg-ink-900 shadow-2xl shadow-black/60',
            // mobile: bottom sheet, sliding up from its own edge.
            'rounded-t-panel border-t border-ink-700 pb-[var(--safe-bottom)] animate-sheet-in',
            // desktop: a centered modal that fades in instead.
            'sm:w-[min(32rem,calc(100vw-2.5rem))] sm:rounded-panel sm:border sm:border-ink-700 sm:pb-0 sm:animate-fade-up',
          )}
        >
          <span className="mx-auto mt-2.5 mb-0 h-1 w-9 shrink-0 rounded-full bg-ink-600 sm:hidden" />
          <header className="flex items-center gap-3 border-b border-ink-800 bg-ink-950/40 px-4 py-3 sm:px-5">
            {track.cover_url ? (
              <img
                src={track.cover_url}
                alt=""
                className="size-11 shrink-0 rounded-btn object-cover ring-1 ring-ink-700"
              />
            ) : (
              <span className="grid size-11 shrink-0 place-items-center rounded-btn bg-ink-800 text-ink-400">
                <MusicNotesIcon className="size-5" />
              </span>
            )}
            <div className="min-w-0 flex-1">
              <p className="text-micro font-semibold text-lime-flash">متن آهنگ</p>
              <h2 className="mt-0.5 truncate font-display text-mini font-bold text-ink-100" dir="auto">
                {track.title}
              </h2>
              <p className="truncate text-micro text-ink-400" dir="auto">
                {track.artists.join(', ')}
              </p>
            </div>
            {hasLyrics && (
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
                {copied ? <CheckIcon className="size-4" /> : <CopyIcon className="size-4" />}
              </button>
            )}
            <button
              onClick={onClose}
              aria-label="بستن متن آهنگ"
              className="tap-target grid size-9 shrink-0 place-items-center rounded-btn text-ink-400 transition hover:bg-ink-800 hover:text-ink-100"
            >
              <XIcon className="size-4" />
            </button>
          </header>

          <div className="flex-1 overflow-y-auto overscroll-contain px-4 py-5 sm:px-6 sm:py-6">
            {state.status === 'loading' && (
              <div className="space-y-4 py-2" aria-busy>
                {[0, 1, 2, 3, 4].map((i) => (
                  <div key={i} className="space-y-2">
                    <div
                      className="shimmer h-4 rounded-ctl"
                      style={{ width: `${94 - (i % 3) * 9}%` }}
                    />
                    <div
                      className="shimmer h-4 rounded-ctl"
                      style={{ width: `${78 - (i % 3) * 12}%` }}
                    />
                  </div>
                ))}
              </div>
            )}

            {state.status === 'error' && (
              <div className="flex flex-col items-center gap-4 py-12 text-center">
                <span className="grid size-12 place-items-center rounded-full bg-danger/10 text-danger">
                  <TriangleAlert className="size-5" />
                </span>
                <p className="max-w-60 text-mini text-ink-300">{state.message}</p>
                <button
                  onClick={() => setAttempt((n) => n + 1)}
                  className="flex items-center gap-1.5 rounded-btn border border-ink-600 px-4 py-2 text-mini font-medium text-ink-100 transition hover:border-ink-400 active:scale-95"
                >
                  <RefreshCcw className="size-3.5" />
                  دوباره تلاش کن
                </button>
              </div>
            )}

            {state.status === 'done' && !state.lyrics.plain && (
              <div className="flex flex-col items-center gap-4 py-12 text-center">
                <span className="grid size-12 place-items-center rounded-full bg-ink-800 text-ink-400">
                  <MusicNotesIcon className="size-6" />
                </span>
                <p className="text-mini text-ink-300">متن این آهنگ پیدا نشد</p>
                <p className="max-w-56 text-micro text-ink-400">
                  یا هنوز توی دیتابیس نیست، یا اسم آهنگ جور دیگه‌ای ثبت شده.
                </p>
              </div>
            )}

            {hasLyrics && (
              <>
                <Verses text={state.lyrics.plain ?? ''} />
                <footer className="mt-6 flex items-center justify-between border-t border-ink-800 pt-3 text-micro text-ink-400">
                  <span>متن آهنگ</span>
                  <span dir="ltr">{SOURCE_LABEL[state.lyrics.source ?? ''] ?? state.lyrics.source ?? ''}</span>
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

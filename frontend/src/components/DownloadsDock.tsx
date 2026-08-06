import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import {
  Archive,
  ArrowDownToLine,
  Check,
  ChevronDown,
  Download,
  LoaderCircle,
  TriangleAlert,
  X,
} from 'lucide-react'
import clsx from 'clsx'
import { jobZipUrl, trackFileUrl, QUALITY_LABEL, type Job, type JobTrack } from '../lib/api'
import { useDownloads, type DownloadEntry } from '../lib/downloads'
import { ShareTrack } from './ShareTrack'

const STAGE_LABEL: Record<string, string> = {
  queued: 'تو صف',
  searching: 'در حال جستجو…',
  downloading: 'در حال دانلود',
  tagging: 'در حال تگ زدن…',
  retrying: 'تلاش دوباره…',
}

/** How far the tracks that haven't settled yet have got, as a track count.
 *
 *  Progress measured in settled tracks alone leaves a single-track job pinned
 *  at 0% for the whole download and then jumping to 100% — the percentage in
 *  the row was moving while the bar it belongs to was not. Tagging counts as
 *  whole: the file is downloaded by then, only metadata is still being
 *  written. Every other stage reports 0 and contributes nothing. */
function inFlightFraction(job: Job): number {
  return job.tracks.reduce(
    (sum, t) => (t.status === 'downloading' || t.status === 'tagging' ? sum + t.progress : sum),
    0,
  )
}

function formatEta(seconds: number): string {
  if (seconds < 60) return `${seconds} ثانیه`
  const minutes = Math.round(seconds / 60)
  return `${minutes} دقیقه`
}

function TrackLine({ entry, state }: { entry: DownloadEntry; state: JobTrack }) {
  const track = entry.tracks.find((t) => t.id === state.id)
  const title = track ? track.title : state.id
  const available = state.status === 'done' && !entry.expired
  // "original" can land as m4a or opus depending on the upload, so the file
  // itself is the only honest source for this label.
  const ext = state.ext ?? 'mp3'
  const active =
    state.status === 'downloading' ||
    state.status === 'searching' ||
    state.status === 'tagging' ||
    state.status === 'retrying'

  return (
    <li className="relative flex items-center gap-2.5 px-4 py-1.5">
      {state.status === 'done' ? (
        <Check className="size-3.5 shrink-0 animate-pop text-lime-flash" />
      ) : state.status === 'error' ? (
        <TriangleAlert className="size-3.5 shrink-0 text-danger" />
      ) : state.status === 'queued' ? (
        <span className="grid size-3.5 shrink-0 place-items-center">
          <span className="size-1.5 rounded-full bg-ink-600" />
        </span>
      ) : (
        <LoaderCircle className="size-3.5 shrink-0 animate-spin text-lime-flash" />
      )}
      <p
        className={clsx(
          'min-w-0 flex-1 truncate text-mini',
          state.status === 'error' ? 'text-ink-400' : 'text-ink-100',
        )}
        title={state.error ?? title}
        dir="auto"
      >
        {title}
      </p>
      {available ? (
        <>
          <ShareTrack
            jobId={entry.jobId}
            trackId={state.id}
            title={title}
            ext={ext}
            size="compact"
          />
          <a
            href={trackFileUrl(entry.jobId, state.id)}
            download
            title={`دانلود ${title}.${ext}`}
            aria-label={`دانلود ${title} با فرمت ${ext}`}
            className="tap-target flex shrink-0 items-center gap-1 rounded-ctl border border-ink-600 px-2 py-0.5 text-micro font-medium text-lime-flash transition hover:border-lime-flash/50 hover:bg-ink-800"
          >
            <Download className="size-3" />
            {ext}
          </a>
        </>
      ) : state.status === 'error' ? (
        <span className="text-xs text-danger">ناموفق</span>
      ) : entry.expired ? (
        <span className="text-xs text-ink-400">پاک شده</span>
      ) : (
        <span
          className={clsx(
            'text-xs text-ink-400 tabular-nums',
            state.status !== 'downloading' && 'animate-breathe',
          )}
        >
          {STAGE_LABEL[state.status]}
          {state.status === 'downloading' && ` ${Math.round(state.progress * 100)}%`}
        </span>
      )}

      {/* The same hairline TrackRow draws under a downloading row. The dock
          was reporting a percentage in text with nothing moving beside it,
          which reads as stalled however fast the number climbs. */}
      {active && !entry.expired && (
        <span className="absolute inset-x-0 bottom-0 h-px overflow-hidden bg-ink-800">
          {state.status === 'downloading' ? (
            <span
              className="block h-full bg-lime-flash/70 transition-[width] duration-500 ease-out"
              style={{ width: `${Math.max(2, state.progress * 100)}%` }}
            />
          ) : (
            // Searching, tagging and retrying report no percentage — a
            // travelling band says "working" without inventing a number.
            <span className="block h-full w-1/4 animate-sweep bg-lime-flash/50" />
          )}
        </span>
      )}
    </li>
  )
}

function JobCard({ entry }: { entry: DownloadEntry }) {
  const { dismiss } = useDownloads()
  const job = entry.job
  const done = job?.done ?? 0
  const failed = job?.failed ?? 0
  const total = job?.total ?? entry.tracks.length
  const expired = entry.expired === true
  const finished = expired || (job?.finished ?? false)
  const inFlight = job ? inFlightFraction(job) : 0
  const fraction = total ? Math.min(1, (done + failed + inFlight) / total) : 0
  // ZIP is for batches — a single song is just the mp3 link on its row.
  const showZip = total > 1 && done > 0 && !expired

  return (
    <div className="border-b border-ink-800 last:border-b-0">
      <div className="flex items-center gap-3 px-4 pt-3 pb-2">
        {entry.cover_url ? (
          <img src={entry.cover_url} alt="" className="size-9 shrink-0 rounded-ctl object-cover" />
        ) : (
          <div className="size-9 shrink-0 rounded-ctl bg-ink-800" />
        )}
        <div className="min-w-0 flex-1">
          {/* dir="auto" keeps a Latin name's punctuation ordered correctly,
              but the progress line under it is always Persian — physical
              right (not text-end, which would follow the name's own
              direction) keeps the two lines on the same edge. */}
          <p className="truncate text-right text-mini font-medium text-ink-100" dir="auto">
            {entry.name}
          </p>
          <p className="text-xs text-ink-400 tabular-nums">
            {expired ? (
              // The backend keeps files for 24h and then sweeps them; a
              // restart clears the job table outright. Either way the links
              // are dead, and saying so beats a row that silently 404s.
              'فایل‌ها دیگه روی سرور نیستن — دوباره دانلودش کن'
            ) : finished ? (
              <>
                {done} از {total} دانلود شد
                {failed > 0 && <span className="text-danger"> · {failed} ناموفق</span>}
              </>
            ) : (
              <>
                {done + failed}/{total}
                {failed > 0 && <span className="text-danger"> · {failed} ناموفق</span>}
                {entry.etaSeconds != null && (
                  <span className="text-ink-300"> · حدود {formatEta(entry.etaSeconds)} مونده</span>
                )}
              </>
            )}
          </p>
        </div>
        <span
          title={
            entry.quality === 'original'
              ? 'بدون انکود دوباره دانلود شده'
              : `انکود شده با ${entry.quality} kbps`
          }
          className="shrink-0 rounded-ctl border border-ink-700 px-1.5 py-0.5 text-micro font-medium text-ink-400 tabular-nums"
        >
          {QUALITY_LABEL[entry.quality]}
        </span>
        {showZip && (
          <a
            href={jobZipUrl(entry.jobId)}
            download
            title="دانلود همه به‌صورت ZIP"
            aria-label="دانلود همه‌ی آهنگ‌ها به‌صورت ZIP"
            className="tap-target grid size-7 shrink-0 place-items-center rounded-ctl border border-ink-600 text-ink-100 transition hover:border-lime-flash/50 hover:text-lime-flash"
          >
            <Archive className="size-3.5" />
          </a>
        )}
        {finished && (
          <button
            onClick={() => dismiss(entry.jobId)}
            title="حذف از لیست"
            className="tap-target grid size-7 shrink-0 place-items-center rounded-ctl text-ink-400 transition hover:bg-ink-800 hover:text-ink-100"
          >
            <X className="size-3.5" />
          </button>
        )}
      </div>
      <div className="mx-4 h-0.5 overflow-hidden rounded-full bg-ink-800">
        <div
          className={clsx(
            'h-full rounded-full transition-[width] duration-500 ease-out',
            failed > 0 && done === 0 ? 'bg-danger' : 'bg-lime-flash',
          )}
          style={{ width: `${fraction * 100}%` }}
        />
      </div>
      <ul className="max-h-44 overflow-y-auto py-1.5">
        {(job?.tracks ?? []).map((state) => (
          <TrackLine key={state.id} entry={entry} state={state} />
        ))}
        {!job && (
          <li className="flex items-center gap-2.5 px-4 py-1.5 text-mini text-ink-400">
            <LoaderCircle className="size-3.5 animate-spin" />
            در حال شروع…
          </li>
        )}
      </ul>
    </div>
  )
}

/** True from Tailwind's `sm` up. Read in JS rather than with `sm:` classes
 *  because the two layouts are different components, not one component with
 *  different padding — rendering both and hiding one would put a second copy
 *  of every job list in the DOM. */
function useIsDesktop(): boolean {
  const query = '(min-width: 40rem)'
  const [desktop, setDesktop] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const mq = window.matchMedia(query)
    const onChange = () => setDesktop(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return desktop
}

/** How far down the sheet has to be dragged before letting go dismisses it. */
const DISMISS_AFTER_PX = 90

/** The downloads list as a bottom sheet: the phone-shaped answer to a floating
 *  panel. Full width, anchored to the edge it slid in from, and dismissed the
 *  three ways a sheet is expected to be — the scrim, the close button, or a
 *  drag on the handle. */
function DownloadsSheet({
  summary,
  onClose,
  onHeight,
  children,
}: {
  summary: string
  onClose: () => void
  /** Publishes the sheet's rendered height so toasts can clear it. */
  onHeight: (px: number) => void
  children: ReactNode
}) {
  const [drag, setDrag] = useState(0)
  const startY = useRef<number | null>(null)
  const sheetRef = useRef<HTMLElement | null>(null)

  // The sheet grows as jobs arrive and shrinks as they're dismissed, so its
  // height is measured rather than assumed — a toast lands just above whatever
  // it currently is.
  useEffect(() => {
    const node = sheetRef.current
    if (!node) return
    const observer = new ResizeObserver(([entry]) => onHeight(entry.contentRect.height))
    observer.observe(node)
    return () => {
      observer.disconnect()
      onHeight(0)
    }
  }, [onHeight])

  // The page behind must not scroll under an open sheet, and Escape belongs to
  // the sheet while it is up — App's global handler would otherwise navigate
  // back, which is not what "close this" means here.
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

  const onPointerDown = (e: React.PointerEvent) => {
    startY.current = e.clientY
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onPointerMove = (e: React.PointerEvent) => {
    if (startY.current == null) return
    // Downward only: dragging up would lift the sheet off its own edge.
    setDrag(Math.max(0, e.clientY - startY.current))
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

  return (
    <>
      <div
        onClick={onClose}
        aria-hidden
        className="fixed inset-0 z-40 animate-scrim-in bg-ink-950/70 backdrop-blur-[2px]"
      />
      <section
        ref={sheetRef}
        aria-label="دانلودها"
        style={{ transform: drag ? `translateY(${drag}px)` : undefined }}
        className={clsx(
          'fixed inset-x-0 bottom-0 z-50 flex max-h-[85svh] flex-col overflow-hidden',
          'rounded-t-panel border-t border-ink-700 bg-ink-900 shadow-2xl shadow-black/60',
          // Padding, not margin: the list scrolls to the very bottom edge and
          // its last row must clear the home indicator.
          'pb-[var(--safe-bottom)]',
          // The entrance animation and the drag transform are both `transform`,
          // so the animation only runs while nothing is being dragged.
          drag ? 'transition-none' : 'animate-sheet-in',
        )}
      >
        <header
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          className="shrink-0 cursor-grab touch-none border-b border-ink-800 active:cursor-grabbing"
        >
          <div className="mx-auto mt-2.5 h-1 w-9 rounded-full bg-ink-600" />
          <div className="flex items-center gap-3 px-4 pt-2 pb-3">
            <h2 className="text-micro font-semibold text-ink-400">دانلودها</h2>
            <span className="flex-1 text-xs text-ink-400 tabular-nums">{summary}</span>
            <button
              onClick={onClose}
              aria-label="بستن پنل دانلود"
              className="tap-target -m-1 grid size-7 shrink-0 place-items-center rounded-ctl text-ink-400 transition hover:bg-ink-800 hover:text-ink-100"
            >
              <ChevronDown className="size-4" />
            </button>
          </div>
        </header>
        <div className="overflow-y-auto overscroll-contain">{children}</div>
      </section>
    </>
  )
}

export function DownloadsDock() {
  const { entries, activeCount, panelOpen, setPanelOpen } = useDownloads()
  const isDesktop = useIsDesktop()

  // Toasts are full width on phones, so whatever this component pins to the
  // bottom edge would end up underneath them. --dock-lift publishes exactly
  // how much room to leave, and this is its only writer: the FAB's fixed
  // footprint while it's the thing down there, the sheet's measured height
  // once it takes over, and nothing at all on desktop, where the toast stack
  // and the dock sit in opposite corners.
  const docked = entries.length > 0
  const sheetOpen = panelOpen && !isDesktop
  const [sheetHeight, setSheetHeight] = useState(0)
  useEffect(() => {
    const root = document.documentElement
    const lift = isDesktop ? 0 : sheetOpen ? sheetHeight : docked ? 76 : 0
    if (lift > 0) root.style.setProperty('--dock-lift', `${lift}px`)
    else root.style.removeProperty('--dock-lift')
    return () => {
      root.style.removeProperty('--dock-lift')
    }
  }, [docked, isDesktop, sheetOpen, sheetHeight])

  const close = useCallback(() => setPanelOpen(false), [setPanelOpen])

  if (!docked) return null

  // Same continuous measure the cards use, so the ring around the FAB and the
  // bar inside the panel never disagree about how far along a job is.
  const totals = entries.reduce(
    (acc, e) => ({
      settled: acc.settled + (e.job ? e.job.done + e.job.failed + inFlightFraction(e.job) : 0),
      total: acc.total + (e.job?.total ?? e.tracks.length),
    }),
    { settled: 0, total: 0 },
  )
  const fraction = totals.total ? Math.min(1, totals.settled / totals.total) : 0
  const summary = activeCount > 0 ? `${activeCount} در جریان` : `${entries.length} تمام‌شده`
  const cards = [...entries].reverse().map((entry) => <JobCard key={entry.jobId} entry={entry} />)

  return (
    <div className="fixed end-5 bottom-[calc(1.25rem+var(--safe-bottom))] z-50 flex flex-col items-end gap-3">
      {panelOpen && isDesktop && (
        <section
          aria-label="دانلودها"
          className="flex w-[min(24rem,calc(100vw-2.5rem))] animate-fade-up flex-col overflow-hidden rounded-panel border border-ink-700 bg-ink-900 shadow-2xl shadow-black/60"
        >
          <header className="flex items-center justify-between border-b border-ink-800 px-4 py-3">
            <h2 className="text-micro font-semibold text-ink-400">دانلودها</h2>
            <span className="text-xs text-ink-400 tabular-nums">{summary}</span>
          </header>
          <div className="max-h-[55vh] overflow-y-auto">{cards}</div>
        </section>
      )}

      {sheetOpen && (
        <DownloadsSheet summary={summary} onClose={close} onHeight={setSheetHeight}>
          {cards}
        </DownloadsSheet>
      )}

      <button
        onClick={() => setPanelOpen(!panelOpen)}
        aria-label={panelOpen ? 'بستن پنل دانلود' : 'نمایش دانلودها'}
        className={clsx(
          'relative grid size-14 place-items-center rounded-full bg-lime-flash text-lime-ink shadow-lg shadow-black/40 transition duration-200 hover:bg-lime-soft hover:scale-105 active:scale-95',
          // The sheet covers the bottom edge and carries its own dismissal, so
          // the FAB would just be a lime disc floating on top of it.
          sheetOpen && 'pointer-events-none opacity-0',
        )}
      >
        {/* progress ring around the button while anything is downloading */}
        {activeCount > 0 && (
          <svg viewBox="0 0 56 56" className="absolute inset-0 -rotate-90">
            <circle
              cx="28"
              cy="28"
              r="26"
              fill="none"
              className="stroke-lime-ink/20"
              strokeWidth="2.5"
            />
            <circle
              cx="28"
              cy="28"
              r="26"
              fill="none"
              className="stroke-lime-ink transition-[stroke-dashoffset] duration-500"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeDasharray={2 * Math.PI * 26}
              strokeDashoffset={2 * Math.PI * 26 * (1 - fraction)}
            />
          </svg>
        )}
        {panelOpen ? (
          <ChevronDown className="size-5" strokeWidth={2.25} />
        ) : (
          <ArrowDownToLine className="size-5" strokeWidth={2.25} />
        )}
        {activeCount > 0 && !panelOpen && (
          <span className="absolute -top-0.5 -end-0.5 grid min-w-5 animate-pop place-items-center rounded-full border border-lime-flash bg-ink-950 px-1 text-micro font-semibold text-lime-flash tabular-nums">
            {activeCount}
          </span>
        )}
      </button>
    </div>
  )
}

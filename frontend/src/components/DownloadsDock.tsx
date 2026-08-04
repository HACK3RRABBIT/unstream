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
import { jobZipUrl, trackFileUrl, type JobTrack } from '../lib/api'
import { useDownloads, type DownloadEntry } from '../lib/downloads'

const STAGE_LABEL: Record<string, string> = {
  queued: 'Queued',
  searching: 'Searching…',
  downloading: 'Downloading',
  tagging: 'Tagging…',
  retrying: 'Retrying…',
}

function formatEta(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.round(seconds / 60)
  return `${minutes} min`
}

function TrackLine({ entry, state }: { entry: DownloadEntry; state: JobTrack }) {
  const track = entry.tracks.find((t) => t.id === state.id)
  const title = track ? track.title : state.id
  return (
    <li className="flex items-center gap-2.5 px-4 py-1.5">
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
      >
        {title}
      </p>
      {state.status === 'done' ? (
        <a
          href={trackFileUrl(entry.jobId, state.id)}
          download
          title={`Download ${title}.mp3`}
          aria-label={`Download ${title} as mp3`}
          className="flex shrink-0 items-center gap-1 rounded-ctl border border-ink-600 px-2 py-0.5 text-micro font-medium text-lime-flash transition hover:border-lime-flash/50 hover:bg-ink-800"
        >
          <Download className="size-3" />
          mp3
        </a>
      ) : state.status === 'error' ? (
        <span className="text-xs text-danger">failed</span>
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
    </li>
  )
}

function JobCard({ entry }: { entry: DownloadEntry }) {
  const { dismiss } = useDownloads()
  const job = entry.job
  const done = job?.done ?? 0
  const failed = job?.failed ?? 0
  const total = job?.total ?? entry.tracks.length
  const finished = job?.finished ?? false
  // ZIP is for batches — a single song is just the mp3 link on its row.
  const showZip = total > 1 && done > 0

  return (
    <div className="border-b border-ink-800 last:border-b-0">
      <div className="flex items-center gap-3 px-4 pt-3 pb-2">
        {entry.cover_url ? (
          <img src={entry.cover_url} alt="" className="size-9 shrink-0 rounded-ctl object-cover" />
        ) : (
          <div className="size-9 shrink-0 rounded-ctl bg-ink-800" />
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate text-mini font-medium text-ink-100">{entry.name}</p>
          <p className="text-xs text-ink-400 tabular-nums">
            {finished ? (
              <>
                {done} of {total} downloaded
                {failed > 0 && <span className="text-danger"> · {failed} failed</span>}
              </>
            ) : (
              <>
                {done + failed}/{total}
                {failed > 0 && <span className="text-danger"> · {failed} failed</span>}
                {entry.etaSeconds != null && (
                  <span className="text-ink-300"> · ~{formatEta(entry.etaSeconds)} left</span>
                )}
              </>
            )}
          </p>
        </div>
        {showZip && (
          <a
            href={jobZipUrl(entry.jobId)}
            download
            title="Download all as ZIP"
            aria-label="Download all tracks as ZIP"
            className="grid size-7 shrink-0 place-items-center rounded-ctl border border-ink-600 text-ink-100 transition hover:border-lime-flash/50 hover:text-lime-flash"
          >
            <Archive className="size-3.5" />
          </a>
        )}
        {finished && (
          <button
            onClick={() => dismiss(entry.jobId)}
            title="Remove from list"
            className="grid size-7 shrink-0 place-items-center rounded-ctl text-ink-400 transition hover:bg-ink-800 hover:text-ink-100"
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
          style={{ width: `${total ? ((done + failed) / total) * 100 : 0}%` }}
        />
      </div>
      <ul className="max-h-44 overflow-y-auto py-1.5">
        {(job?.tracks ?? []).map((state) => (
          <TrackLine key={state.id} entry={entry} state={state} />
        ))}
        {!job && (
          <li className="flex items-center gap-2.5 px-4 py-1.5 text-mini text-ink-400">
            <LoaderCircle className="size-3.5 animate-spin" />
            Starting…
          </li>
        )}
      </ul>
    </div>
  )
}

export function DownloadsDock() {
  const { entries, activeCount, panelOpen, setPanelOpen } = useDownloads()
  if (entries.length === 0) return null

  const totals = entries.reduce(
    (acc, e) => ({
      settled: acc.settled + (e.job ? e.job.done + e.job.failed : 0),
      total: acc.total + (e.job?.total ?? e.tracks.length),
    }),
    { settled: 0, total: 0 },
  )
  const fraction = totals.total ? totals.settled / totals.total : 0

  return (
    <div className="fixed right-5 bottom-5 z-50 flex flex-col items-end gap-3">
      {panelOpen && (
        <section
          aria-label="Downloads"
          className="flex w-[min(24rem,calc(100vw-2.5rem))] animate-fade-up flex-col overflow-hidden rounded-panel border border-ink-700 bg-ink-900 shadow-2xl shadow-black/60"
        >
          <header className="flex items-center justify-between border-b border-ink-800 px-4 py-3">
            <h2 className="text-micro font-semibold tracking-[0.14em] text-ink-400 uppercase">
              Downloads
            </h2>
            <span className="text-xs text-ink-400 tabular-nums">
              {activeCount > 0 ? `${activeCount} in progress` : `${entries.length} finished`}
            </span>
          </header>
          <div className="max-h-[55vh] overflow-y-auto">
            {[...entries].reverse().map((entry) => (
              <JobCard key={entry.jobId} entry={entry} />
            ))}
          </div>
        </section>
      )}

      <button
        onClick={() => setPanelOpen(!panelOpen)}
        aria-label={panelOpen ? 'Hide downloads' : 'Show downloads'}
        className="relative grid size-14 place-items-center rounded-full bg-lime-flash text-lime-ink shadow-lg shadow-black/40 transition duration-200 hover:bg-lime-soft hover:scale-105 active:scale-95"
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
          <span className="absolute -top-0.5 -right-0.5 grid min-w-5 animate-pop place-items-center rounded-full border border-lime-flash bg-ink-950 px-1 text-micro font-semibold text-lime-flash tabular-nums">
            {activeCount}
          </span>
        )}
      </button>
    </div>
  )
}

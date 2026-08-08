/** The stats dashboard, at /admin.
 *
 *  English and left-to-right on purpose: docs/DESIGN.md makes the *product*
 *  Farsi-only, and this is not the product — it is the one page only the
 *  owner sees, whose screenshots are meant to travel.
 *
 *  It is mounted by a path check in main.tsx rather than a router, and
 *  loaded lazily, so none of this reaches a visitor who came for music. */

import { useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  Check,
  Copy,
  LoaderCircle,
  LogOut,
  Megaphone,
  RefreshCw,
} from 'lucide-react'
import clsx from 'clsx'
import {
  AuthError,
  clearToken,
  fetchEvents,
  fetchStats,
  readToken,
  writeToken,
  type Stats,
} from './api'
import {
  ACCENT,
  BarList,
  Card,
  DANGER,
  DataTable,
  Empty,
  Hero,
  NEUTRAL,
  OutcomeColumns,
  StatTile,
  TrendChart,
  compact,
  shortDay,
  withCommas,
} from './charts'

const RANGES = [7, 30, 90]
const FONT = 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'

export default function Admin() {
  const [token, setToken] = useState(readToken)
  const [days, setDays] = useState(30)

  useEffect(() => {
    document.title = 'Unstream · analytics'
    document.documentElement.setAttribute('dir', 'ltr')
    document.documentElement.setAttribute('lang', 'en')
    // index.html is one shared shell, so its SEO metadata describes the app.
    // robots.txt disallows this path; the tag is what covers a crawler that
    // reached it from a link anyway.
    const meta = document.createElement('meta')
    meta.name = 'robots'
    meta.content = 'noindex, nofollow'
    document.head.append(meta)
    return () => meta.remove()
  }, [])

  const query = useQuery({
    queryKey: ['admin-stats', token, days],
    queryFn: () => fetchStats(token, days),
    enabled: !!token,
    refetchInterval: 30_000,
    // Hold the previous numbers while a new range loads — a skeleton flash
    // on every refetch would make a live dashboard unreadable.
    placeholderData: keepPreviousData,
    retry: (count, error) => !(error instanceof AuthError) && count < 2,
  })

  const authFailed = query.error instanceof AuthError
  if (!token || authFailed) {
    return (
      <TokenGate
        message={authFailed ? (query.error as AuthError).message : undefined}
        onSubmit={(value) => {
          writeToken(value)
          setToken(value) // new key, so the query re-runs on its own
        }}
      />
    )
  }

  return (
    <div dir="ltr" className="min-h-dvh bg-ink-950 text-ink-100" style={{ fontFamily: FONT }}>
      <div className="mx-auto max-w-[1200px] px-5 py-6 sm:px-8">
        <Header
          days={days}
          onDays={setDays}
          stats={query.data}
          refreshing={query.isFetching}
          onRefresh={() => query.refetch()}
          onSignOut={() => {
            clearToken()
            setToken('')
          }}
        />

        {query.isLoading && <Loading />}
        {query.error && !authFailed && <Banner>{(query.error as Error).message}</Banner>}
        {query.data && !query.data.enabled && (
          <Banner>
            The analytics database could not be opened — check that the api service can write to its
            data volume.
          </Banner>
        )}
        {query.data?.enabled && <Dashboard stats={query.data} days={days} token={token} />}
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ chrome

function TokenGate({ message, onSubmit }: { message?: string; onSubmit: (token: string) => void }) {
  const [value, setValue] = useState('')
  return (
    <div
      dir="ltr"
      className="grid min-h-dvh place-items-center bg-ink-950 px-6 text-ink-100"
      style={{ fontFamily: FONT }}
    >
      <form
        onSubmit={(event) => {
          event.preventDefault()
          if (value.trim()) onSubmit(value.trim())
        }}
        className="w-full max-w-sm rounded-panel border border-ink-800 bg-ink-900 p-6"
      >
        <h1 className="text-body font-semibold">Unstream analytics</h1>
        <p className="mt-1 text-mini text-ink-400">Paste the ADMIN_TOKEN set on the api service.</p>
        <input
          type="password"
          value={value}
          autoFocus
          onChange={(event) => setValue(event.target.value)}
          placeholder="Admin token"
          className="mt-4 w-full rounded-btn border border-ink-700 bg-ink-950 px-3 py-2.5 text-body text-ink-100 outline-none placeholder:text-ink-400 focus:border-lime-flash/60"
        />
        {message && (
          <p className="mt-3 flex items-start gap-2 text-mini" style={{ color: DANGER }}>
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
            {message}
          </p>
        )}
        <button
          type="submit"
          className="mt-4 w-full rounded-btn bg-lime-flash px-4 py-2.5 text-body font-semibold text-lime-ink transition hover:bg-lime-soft active:scale-[0.99]"
        >
          Open dashboard
        </button>
      </form>
    </div>
  )
}

function Header({
  days,
  onDays,
  stats,
  refreshing,
  onRefresh,
  onSignOut,
}: {
  days: number
  onDays: (days: number) => void
  stats?: Stats
  refreshing: boolean
  onRefresh: () => void
  onSignOut: () => void
}) {
  const live = stats?.live
  return (
    <header className="mb-6 flex flex-wrap items-center justify-between gap-4">
      <div className="flex items-center gap-3">
        <h1 className="text-body font-semibold">Unstream analytics</h1>
        {live && live.active_jobs > 0 && (
          <span
            className="flex items-center gap-1.5 rounded-full border border-ink-700 px-2.5 py-1 text-micro"
            style={{ color: ACCENT }}
          >
            <span
              className="size-1.5 animate-breathe rounded-full"
              style={{ background: ACCENT }}
            />
            {live.active_jobs} job{live.active_jobs === 1 ? '' : 's'} running · {live.active_tracks}{' '}
            track{live.active_tracks === 1 ? '' : 's'}
          </span>
        )}
      </div>

      {/* One filter row, scoping every chart below it. */}
      <div className="flex items-center gap-2">
        <div className="flex rounded-ctl border border-ink-700 p-0.5">
          {RANGES.map((range) => (
            <button
              key={range}
              onClick={() => onDays(range)}
              className={clsx(
                'rounded-[6px] px-2.5 py-1 text-micro transition',
                days === range ? 'bg-ink-700 text-ink-100' : 'text-ink-400 hover:text-ink-300',
              )}
            >
              {range}d
            </button>
          ))}
        </div>
        <IconButton onClick={onRefresh} label="Refresh">
          <RefreshCw className={clsx('size-3.5', refreshing && 'animate-spin')} />
        </IconButton>
        <IconButton onClick={onSignOut} label="Forget token">
          <LogOut className="size-3.5" />
        </IconButton>
      </div>
    </header>
  )
}

function IconButton({
  onClick,
  label,
  children,
}: {
  onClick: () => void
  label: string
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      title={label}
      aria-label={label}
      className="grid size-8 place-items-center rounded-ctl border border-ink-700 text-ink-300 transition hover:border-lime-flash/50 hover:text-lime-flash active:scale-90"
    >
      {children}
    </button>
  )
}

const Loading = () => (
  <div className="grid h-64 place-items-center text-ink-400">
    <LoaderCircle className="size-5 animate-spin" />
  </div>
)

const Banner = ({ children }: { children: React.ReactNode }) => (
  <div
    className="flex items-start gap-2 rounded-btn border border-ink-800 bg-ink-900 p-4 text-mini"
    style={{ color: DANGER }}
  >
    <AlertTriangle className="mt-0.5 size-4 shrink-0" />
    <p>{children}</p>
  </div>
)

// --------------------------------------------------------------- dashboard

function Dashboard({ stats, days, token }: { stats: Stats; days: number; token: string }) {
  const { totals, series, breakdowns } = stats
  const dayLabels = series.map((row) => row.day)
  const rate = totals.success_rate === null ? '—' : `${(totals.success_rate * 100).toFixed(1)}%`

  return (
    <div className="space-y-5">
      <section className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <div className="rounded-panel border border-ink-800 bg-ink-900 p-6">
          <Hero
            value={withCommas(totals.tracks_done)}
            label="Tracks delivered"
            sub={`${shortDay(stats.from)} – ${shortDay(stats.to)} · ${days} days`}
          />
          <ShareCard stats={stats} days={days} />
        </div>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <StatTile label="Visitors" value={compact(totals.visitors)} hint="unique per day" />
          <StatTile label="Page views" value={compact(totals.page_views)} />
          <StatTile
            label="Searches"
            value={compact(totals.searches)}
            hint={`${totals.empty_searches} with no results`}
          />
          <StatTile
            label="Downloads started"
            value={compact(totals.downloads)}
            hint={`${compact(totals.tracks_delivered)} tracks queued`}
          />
          <StatTile
            label="Success rate"
            value={rate}
            hint={`${withCommas(totals.tracks_failed)} failed`}
            tone={totals.success_rate !== null && totals.success_rate < 0.9 ? 'danger' : 'accent'}
          />
          <StatTile
            label="Median track"
            value={totals.median_track_seconds ? `${totals.median_track_seconds}s` : '—'}
            hint="search → tagged file"
          />
          <StatTile
            label="Files saved"
            value={compact(totals.files_saved)}
            hint={
              totals.shares > 0 ? `${compact(totals.shares)} via share sheet` : 'left the server'
            }
          />
          <StatTile label="ZIPs" value={compact(totals.zips)} hint="album downloads" />
          <StatTile
            label="Rate limited"
            value={compact(totals.rate_limited)}
            hint="requests turned away"
          />
          <InstallTile stats={stats} />
          <SurfaceTile stats={stats} />
        </div>
      </section>

      <section className="grid gap-5 xl:grid-cols-2">
        <Card
          title="Traffic"
          hint="Unique visitors and page views per day"
          table={
            <DataTable
              head={['Day', 'Visitors', 'Views']}
              rows={series.map((r) => [shortDay(r.day), r.visitors, r.page_views])}
            />
          }
        >
          <TrendChart
            days={dayLabels}
            series={[
              {
                key: 'visitors',
                label: 'Visitors',
                color: ACCENT,
                fill: true,
                values: series.map((r) => r.visitors),
              },
              {
                key: 'views',
                label: 'Page views',
                color: NEUTRAL,
                values: series.map((r) => r.page_views),
              },
            ]}
          />
        </Card>

        <Card
          title="Delivery"
          hint="Tracks that finished, and tracks that didn't"
          table={
            <DataTable
              head={['Day', 'Delivered', 'Failed']}
              rows={series.map((r) => [shortDay(r.day), r.tracks_done, r.tracks_failed])}
            />
          }
        >
          <OutcomeColumns
            days={dayLabels}
            done={series.map((r) => r.tracks_done)}
            failed={series.map((r) => r.tracks_failed)}
          />
        </Card>
      </section>

      <section className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
        <ListCard title="Most searched" hint="What people typed" slices={breakdowns.top_searches} />
        <ListCard title="Most downloaded artists" slices={breakdowns.top_artists} />
        <ListCard title="Most downloaded tracks" slices={breakdowns.top_tracks} />
        <ListCard
          title="Links pasted"
          hint="Which service the URL came from"
          slices={breakdowns.link_providers}
        />
        <ListCard title="Quality chosen" slices={breakdowns.quality} />
        <ListCard
          title="Audio came from"
          hint="Where the file was actually fetched"
          slices={breakdowns.audio_sources}
        />
        <ListCard title="Why tracks failed" slices={breakdowns.errors} color={DANGER} />
        <ListCard
          title="Links that failed"
          hint="A provider climbing here alone means its pages changed"
          slices={breakdowns.link_errors}
          color={DANGER}
        />
        <ListCard title="Referrers" hint="What sent people here" slices={breakdowns.referrers} />
        <ListCard title="Devices" slices={breakdowns.devices} />
        <ListCard
          title="Artists browsed"
          hint={`${withCommas(totals.artist_views)} artist pages opened`}
          slices={breakdowns.artists_browsed}
        />
        <ListCard
          title="Limits hit"
          hint="Which cap turned a real request away"
          slices={breakdowns.limits_hit}
          color={DANGER}
        />
      </section>

      <RawFeed token={token} />
    </div>
  )
}

function ListCard({
  title,
  hint,
  slices,
  color,
}: {
  title: string
  hint?: string
  slices: { key: string; count: number }[]
  color?: string
}) {
  return (
    <Card
      title={title}
      hint={hint}
      table={<DataTable head={[title, 'Count']} rows={slices.map((s) => [s.key, s.count])} />}
    >
      <BarList slices={slices} color={color} limit={8} />
    </Card>
  )
}

/** Installs, against the number of people the browser offered one to. The
 *  ratio is the only read on whether the PWA is worth the service worker;
 *  hidden until a browser has actually prompted, since iOS never does. */
function InstallTile({ stats }: { stats: Stats }) {
  const { installs, install_prompts } = stats.totals
  if (installs === 0 && install_prompts === 0) return null
  return (
    <StatTile
      label="PWA installs"
      value={compact(installs)}
      hint={install_prompts > 0 ? `${compact(install_prompts)} offered` : 'no prompts shown'}
    />
  )
}

/** Only earns its space once there is more than one client — installed PWA
 *  against browser. Until then it renders nothing rather than a tile that
 *  always says "100% web". */
function SurfaceTile({ stats }: { stats: Stats }) {
  const surfaces = stats.breakdowns.surfaces
  if (surfaces.length < 2) return null
  const top = surfaces[0]
  const total = surfaces.reduce((sum, s) => sum + s.count, 0)
  return (
    <StatTile
      label="Top surface"
      value={`${Math.round((top.count / total) * 100)}%`}
      hint={`${top.key} of ${surfaces.length} clients`}
    />
  )
}

// -------------------------------------------------------------- share card

/** Turns the range into a block of text ready to paste into a post. */
function ShareCard({ stats, days }: { stats: Stats; days: number }) {
  const [copied, setCopied] = useState(false)
  const text = useMemo(() => {
    const { totals, breakdowns } = stats
    const lines = [
      `Unstream — last ${days} days`,
      '',
      `${withCommas(totals.visitors)} visitors`,
      `${withCommas(totals.searches)} searches`,
      `${withCommas(totals.tracks_done)} tracks downloaded`,
      `${withCommas(totals.files_saved)} files actually saved`,
    ]
    if (totals.success_rate !== null)
      lines.push(`${(totals.success_rate * 100).toFixed(1)}% of them succeeded`)
    if (totals.median_track_seconds) lines.push(`${totals.median_track_seconds}s median per track`)
    const top = breakdowns.top_searches.slice(0, 3).map((s) => s.key)
    if (top.length) lines.push('', `Most searched: ${top.join(', ')}`)
    return lines.join('\n')
  }, [stats, days])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div className="mt-6 border-t border-ink-800 pt-4">
      <pre className="whitespace-pre-wrap text-mini leading-relaxed text-ink-300">{text}</pre>
      <button
        onClick={copy}
        className="mt-3 flex items-center gap-2 rounded-btn border border-ink-700 px-3 py-2 text-mini text-ink-300 transition hover:border-lime-flash/50 hover:text-lime-flash"
      >
        {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
        {copied ? 'Copied' : 'Copy for socials'}
        <Megaphone className="size-3.5 opacity-50" />
      </button>
    </div>
  )
}

// ---------------------------------------------------------------- raw feed

function RawFeed({ token }: { token: string }) {
  const [open, setOpen] = useState(false)
  const { data, isLoading } = useQuery({
    queryKey: ['admin-events', token],
    queryFn: () => fetchEvents(token, 200),
    enabled: open,
    refetchInterval: open ? 15_000 : false,
  })

  return (
    <section className="rounded-panel border border-ink-800 bg-ink-900 p-5">
      <button onClick={() => setOpen(!open)} className="text-body font-semibold text-ink-100">
        Raw events{' '}
        <span className="text-mini font-normal text-ink-400">
          {open ? '— hide' : '— show the last 200, for checking the wiring'}
        </span>
      </button>
      {open && (
        <div className="mt-4">
          {isLoading && <Empty />}
          {data && (
            <DataTable
              head={['Time', 'Event', 'Source', 'Detail', 'Label', 'Value', 'ms']}
              rows={data.events.map((event) => [
                new Date(event.ts * 1000).toLocaleString('en-GB'),
                event.name,
                event.source ?? '—',
                event.detail ?? '—',
                event.label ?? '—',
                event.value ?? '—',
                event.ms ?? '—',
              ])}
            />
          )}
        </div>
      )}
    </section>
  )
}

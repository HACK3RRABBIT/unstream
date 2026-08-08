/** Charts for the stats dashboard, drawn as plain SVG.
 *
 *  No charting library: the whole dashboard is four shapes (a line, a
 *  column, a horizontal bar, a number), and a dependency to draw them
 *  would outweigh them several times over in bundle size.
 *
 *  Colour does one of two jobs here and never a third. Lime is the metric
 *  being emphasised; ink grey is the same chart's supporting series; red
 *  means failure. There is no categorical palette, because nothing here
 *  encodes identity by hue — the bar lists name every row, so their bars
 *  are all one colour. */

import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import clsx from 'clsx'

export const ACCENT = 'var(--color-lime-flash)'
export const NEUTRAL = 'var(--color-ink-300)'
export const DANGER = 'var(--color-danger)'
const SURFACE = 'var(--color-ink-900)'
const GRID = 'var(--color-ink-700)'

// ---------------------------------------------------------------- numbers

export function compact(n: number): string {
  if (Math.abs(n) < 1000) return String(n)
  if (Math.abs(n) < 1_000_000) {
    const k = n / 1000
    return `${k < 10 ? k.toFixed(1).replace(/\.0$/, '') : Math.round(k)}K`
  }
  return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`
}

export const withCommas = (n: number) => n.toLocaleString('en-US')

export function shortDay(day: string): string {
  const [, m, d] = day.split('-')
  const months = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split(' ')
  return `${months[Number(m) - 1]} ${Number(d)}`
}

/** Axis ticks on round numbers, and the top of the scale they imply. */
function scaleTicks(maxValue: number, target = 4) {
  const raw = Math.max(maxValue, 1) / target
  const mag = 10 ** Math.floor(Math.log10(raw))
  const n = raw / mag
  const step = (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * mag
  const top = Math.ceil(Math.max(maxValue, 1) / step) * step
  const values: number[] = []
  for (let v = 0; v <= top + 1e-9; v += step) values.push(v)
  return { top, values }
}

/** Width of an element as it actually renders — the charts are sized in
 *  real pixels rather than stretched from a fixed viewBox, so a 2px stroke
 *  is 2px on every screen. */
function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  return [ref, width] as const
}

// ------------------------------------------------------------------ shell

/** A dashboard card. Charts get a chart/table switch, because a tooltip
 *  must never be the only way to read a value. */
export function Card({
  title,
  hint,
  right,
  table,
  children,
}: {
  title: string
  hint?: string
  right?: ReactNode
  table?: ReactNode
  children: ReactNode
}) {
  const [view, setView] = useState<'chart' | 'table'>('chart')
  return (
    <section className="rounded-panel border border-ink-800 bg-ink-900 p-5">
      <header className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-body font-semibold text-ink-100">{title}</h2>
          {hint && <p className="mt-0.5 text-mini text-ink-400">{hint}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {right}
          {table && (
            <div className="flex rounded-ctl border border-ink-700 p-0.5">
              {(['chart', 'table'] as const).map((mode) => (
                <button
                  key={mode}
                  onClick={() => setView(mode)}
                  className={clsx(
                    'rounded-[6px] px-2 py-0.5 text-micro capitalize transition',
                    view === mode ? 'bg-ink-700 text-ink-100' : 'text-ink-400 hover:text-ink-300',
                  )}
                >
                  {mode}
                </button>
              ))}
            </div>
          )}
        </div>
      </header>
      {table && view === 'table' ? table : children}
    </section>
  )
}

export function DataTable({ head, rows }: { head: string[]; rows: (string | number)[][] }) {
  return (
    <div className="max-h-[260px] overflow-auto">
      <table className="w-full text-mini tabular-nums">
        <thead className="sticky top-0 bg-ink-900 text-left text-micro uppercase tracking-wide text-ink-400">
          <tr>
            {head.map((h, i) => (
              <th key={h} className={clsx('py-1.5 font-medium', i && 'text-right')}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="text-ink-300">
          {rows.map((row, r) => (
            <tr key={r} className="border-t border-ink-800">
              {row.map((cell, i) => (
                <td key={i} className={clsx('py-1.5', i ? 'text-right' : 'text-ink-100')}>
                  {typeof cell === 'number' ? withCommas(cell) : cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ------------------------------------------------------------- stat tiles

export function StatTile({
  label,
  value,
  hint,
  tone = 'normal',
}: {
  label: string
  value: string
  hint?: string
  tone?: 'normal' | 'accent' | 'danger'
}) {
  return (
    <div className="rounded-btn border border-ink-800 bg-ink-900 px-4 py-3.5">
      <div className="text-micro uppercase tracking-wide text-ink-400">{label}</div>
      <div
        className="mt-1.5 text-2xl font-semibold"
        style={{
          color: tone === 'accent' ? ACCENT : tone === 'danger' ? DANGER : undefined,
        }}
      >
        <span className={tone === 'normal' ? 'text-ink-100' : undefined}>{value}</span>
      </div>
      {hint && <div className="mt-0.5 text-mini text-ink-400">{hint}</div>}
    </div>
  )
}

/** The one number the page leads with. Exactly one per view. */
export function Hero({ value, label, sub }: { value: string; label: string; sub?: string }) {
  return (
    <div>
      <div className="text-micro uppercase tracking-wide text-ink-400">{label}</div>
      <div className="mt-1 text-[56px] leading-none font-semibold" style={{ color: ACCENT }}>
        {value}
      </div>
      {sub && <div className="mt-2 text-mini text-ink-300">{sub}</div>}
    </div>
  )
}

// ------------------------------------------------------------ trend chart

export interface TrendSeries {
  key: string
  label: string
  color: string
  values: number[]
  /** The emphasised series gets the area wash under its line. */
  fill?: boolean
}

const PAD = { top: 16, right: 56, bottom: 26, left: 44 }
const HEIGHT = 200

/** Multi-series daily lines with a crosshair. Arrow keys move the crosshair
 *  too, so a keyboard reaches every value the mouse can. */
export function TrendChart({ days, series }: { days: string[]; series: TrendSeries[] }) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [active, setActive] = useState<number | null>(null)
  const clipId = useId()

  if (days.length === 0) return <Empty />

  const plotW = Math.max(width - PAD.left - PAD.right, 10)
  const plotH = HEIGHT - PAD.top - PAD.bottom
  const max = Math.max(...series.flatMap((s) => s.values), 0)
  const { top, values: tickValues } = scaleTicks(max)

  const x = (i: number) =>
    PAD.left + (days.length === 1 ? plotW / 2 : (i * plotW) / (days.length - 1))
  const y = (v: number) => PAD.top + plotH - (v / top) * plotH

  const line = (vals: number[]) => vals.map((v, i) => `${x(i)},${y(v)}`).join(' ')
  const area = (vals: number[]) =>
    `${PAD.left},${PAD.top + plotH} ${line(vals)} ${x(vals.length - 1)},${PAD.top + plotH}`

  // Roughly six dates, whatever the range.
  const labelEvery = Math.max(1, Math.ceil(days.length / 6))

  const onMove = (event: React.MouseEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect()
    const rel = event.clientX - box.left - PAD.left
    const i = Math.round((rel / plotW) * (days.length - 1))
    setActive(Math.max(0, Math.min(days.length - 1, i)))
  }

  const onKey = (event: React.KeyboardEvent<SVGSVGElement>) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
    event.preventDefault()
    const step = event.key === 'ArrowLeft' ? -1 : 1
    setActive((prev) => {
      const next = (prev ?? days.length - 1) + step
      return Math.max(0, Math.min(days.length - 1, next))
    })
  }

  return (
    <div ref={ref} className="relative">
      <Legend series={series} />
      {width > 0 && (
        <svg
          width={width}
          height={HEIGHT}
          role="img"
          tabIndex={0}
          aria-label={`Daily ${series.map((s) => s.label).join(' and ')}`}
          onMouseMove={onMove}
          onMouseLeave={() => setActive(null)}
          onKeyDown={onKey}
          onBlur={() => setActive(null)}
          className="overflow-visible outline-none focus-visible:outline-1 focus-visible:outline-offset-4"
          style={{ outlineColor: ACCENT }}
        >
          <defs>
            <clipPath id={clipId}>
              <rect x={PAD.left} y={PAD.top} width={plotW} height={plotH} />
            </clipPath>
          </defs>

          {tickValues.map((v) => (
            <g key={v}>
              <line
                x1={PAD.left}
                x2={PAD.left + plotW}
                y1={y(v)}
                y2={y(v)}
                strokeWidth={1}
                style={{ stroke: GRID }}
              />
              <text
                x={PAD.left - 10}
                y={y(v) + 3.5}
                textAnchor="end"
                className="fill-ink-400 text-[10px] tabular-nums"
              >
                {compact(v)}
              </text>
            </g>
          ))}

          {days.map((day, i) =>
            i % labelEvery === 0 || i === days.length - 1 ? (
              <text
                key={day}
                x={x(i)}
                y={HEIGHT - 8}
                textAnchor="middle"
                className="fill-ink-400 text-[10px]"
              >
                {shortDay(day)}
              </text>
            ) : null,
          )}

          <g clipPath={`url(#${clipId})`}>
            {series.map((s) =>
              s.fill ? (
                <polygon
                  key={s.key}
                  points={area(s.values)}
                  style={{ fill: s.color, opacity: 0.1 }}
                />
              ) : null,
            )}
          </g>

          {series.map((s) => (
            <polyline
              key={s.key}
              points={line(s.values)}
              fill="none"
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
              style={{ stroke: s.color }}
            />
          ))}

          {/* End dots and their values — the only labels on the chart. */}
          {series.map((s) => {
            const last = s.values.length - 1
            return (
              <g key={s.key}>
                <circle
                  cx={x(last)}
                  cy={y(s.values[last])}
                  r={4}
                  strokeWidth={2}
                  style={{ fill: s.color, stroke: SURFACE }}
                />
                <text
                  x={x(last) + 10}
                  y={y(s.values[last]) + 3.5}
                  className="fill-ink-300 text-[11px] tabular-nums"
                >
                  {compact(s.values[last])}
                </text>
              </g>
            )
          })}

          {active !== null && (
            <g>
              <line
                x1={x(active)}
                x2={x(active)}
                y1={PAD.top}
                y2={PAD.top + plotH}
                strokeWidth={1}
                style={{ stroke: NEUTRAL, opacity: 0.4 }}
              />
              {series.map((s) => (
                <circle
                  key={s.key}
                  cx={x(active)}
                  cy={y(s.values[active])}
                  r={4}
                  strokeWidth={2}
                  style={{ fill: s.color, stroke: SURFACE }}
                />
              ))}
            </g>
          )}
        </svg>
      )}

      {active !== null && (
        <div
          className="pointer-events-none absolute top-0 z-10 min-w-[128px] rounded-ctl border border-ink-700 bg-ink-950/95 px-2.5 py-2 text-mini shadow-lg"
          style={{
            left: Math.min(Math.max(x(active) - 64, 0), Math.max(width - 136, 0)),
          }}
        >
          <div className="mb-1 text-micro uppercase tracking-wide text-ink-400">
            {shortDay(days[active])}
          </div>
          {series.map((s) => (
            <div key={s.key} className="flex items-center justify-between gap-3">
              <span className="flex items-center gap-1.5 text-ink-300">
                <span
                  className="inline-block size-2 rounded-full"
                  style={{ background: s.color }}
                />
                {s.label}
              </span>
              <span className="tabular-nums text-ink-100">{withCommas(s.values[active])}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function Legend({ series }: { series: TrendSeries[] }) {
  if (series.length < 2) return null // one series — the title already names it
  return (
    <div className="mb-1 flex flex-wrap gap-4">
      {series.map((s) => (
        <span key={s.key} className="flex items-center gap-1.5 text-mini text-ink-300">
          <span className="inline-block h-0.5 w-4 rounded-full" style={{ background: s.color }} />
          {s.label}
        </span>
      ))}
    </div>
  )
}

// --------------------------------------------------------- stacked columns

const BAR_MAX = 24
const GAP = 2 // surface-coloured space between touching segments

/** Daily outcome columns: delivered on the bottom, failed stacked above. */
export function OutcomeColumns({
  days,
  done,
  failed,
}: {
  days: string[]
  done: number[]
  failed: number[]
}) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [active, setActive] = useState<number | null>(null)

  if (days.length === 0) return <Empty />

  const plotW = Math.max(width - PAD.left - PAD.right, 10)
  const plotH = HEIGHT - PAD.top - PAD.bottom
  const totals = days.map((_, i) => done[i] + failed[i])
  const { top, values: tickValues } = scaleTicks(Math.max(...totals, 0))

  const band = plotW / days.length
  const barW = Math.min(BAR_MAX, Math.max(band - 4, 2))
  const cx = (i: number) => PAD.left + band * i + band / 2
  const h = (v: number) => (v / top) * plotH
  const base = PAD.top + plotH
  const labelEvery = Math.max(1, Math.ceil(days.length / 6))

  const series: TrendSeries[] = [
    { key: 'done', label: 'Delivered', color: ACCENT, values: done },
    { key: 'failed', label: 'Failed', color: DANGER, values: failed },
  ]

  return (
    <div ref={ref} className="relative">
      <Legend series={series} />
      {width > 0 && (
        <svg
          width={width}
          height={HEIGHT}
          role="img"
          aria-label="Tracks delivered and failed per day"
          onMouseLeave={() => setActive(null)}
          className="overflow-visible"
        >
          {tickValues.map((v) => (
            <g key={v}>
              <line
                x1={PAD.left}
                x2={PAD.left + plotW}
                y1={base - h(v)}
                y2={base - h(v)}
                strokeWidth={1}
                style={{ stroke: GRID }}
              />
              <text
                x={PAD.left - 10}
                y={base - h(v) + 3.5}
                textAnchor="end"
                className="fill-ink-400 text-[10px] tabular-nums"
              >
                {compact(v)}
              </text>
            </g>
          ))}

          {days.map((day, i) => {
            const dh = h(done[i])
            const fh = h(failed[i])
            const x = cx(i) - barW / 2
            // The topmost segment carries the rounded data-end; everything
            // below it stays square, and a gap keeps the two readable.
            const doneRound = failed[i] === 0
            return (
              <g
                key={day}
                onMouseEnter={() => setActive(i)}
                onFocus={() => setActive(i)}
                tabIndex={-1}
              >
                <rect
                  x={x}
                  y={PAD.top}
                  width={barW}
                  height={plotH}
                  fill="transparent"
                  onMouseEnter={() => setActive(i)}
                />
                {done[i] > 0 && (
                  <rect
                    x={x}
                    y={base - dh}
                    width={barW}
                    height={dh}
                    rx={doneRound ? 4 : 0}
                    style={{ fill: ACCENT }}
                  />
                )}
                {failed[i] > 0 && (
                  <rect
                    x={x}
                    y={base - dh - fh - (done[i] > 0 ? GAP : 0)}
                    width={barW}
                    height={fh}
                    rx={4}
                    style={{ fill: DANGER }}
                  />
                )}
              </g>
            )
          })}

          {days.map((day, i) =>
            i % labelEvery === 0 || i === days.length - 1 ? (
              <text
                key={day}
                x={cx(i)}
                y={HEIGHT - 8}
                textAnchor="middle"
                className="fill-ink-400 text-[10px]"
              >
                {shortDay(day)}
              </text>
            ) : null,
          )}
        </svg>
      )}

      {active !== null && (
        <div
          className="pointer-events-none absolute top-0 z-10 min-w-[128px] rounded-ctl border border-ink-700 bg-ink-950/95 px-2.5 py-2 text-mini shadow-lg"
          style={{ left: Math.min(Math.max(cx(active) - 64, 0), Math.max(width - 136, 0)) }}
        >
          <div className="mb-1 text-micro uppercase tracking-wide text-ink-400">
            {shortDay(days[active])}
          </div>
          {series.map((s) => (
            <div key={s.key} className="flex items-center justify-between gap-3">
              <span className="flex items-center gap-1.5 text-ink-300">
                <span
                  className="inline-block size-2 rounded-full"
                  style={{ background: s.color }}
                />
                {s.label}
              </span>
              <span className="tabular-nums text-ink-100">{withCommas(s.values[active])}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// -------------------------------------------------------------- bar lists

/** A ranked list. Every row is named, so every bar is the same colour —
 *  shading them by size would encode the length twice and say nothing new. */
export function BarList({
  slices,
  color = ACCENT,
  limit = 8,
  unit,
}: {
  slices: { key: string; count: number }[]
  color?: string
  limit?: number
  unit?: string
}) {
  if (slices.length === 0) return <Empty />
  const rows = slices.slice(0, limit)
  const max = Math.max(...rows.map((r) => r.count), 1)
  return (
    <ol className="space-y-2.5">
      {rows.map((row) => (
        <li key={row.key}>
          <div className="flex items-baseline justify-between gap-3">
            <span className="truncate text-mini text-ink-100" title={row.key}>
              {row.key}
            </span>
            <span className="shrink-0 text-mini tabular-nums text-ink-300">
              {withCommas(row.count)}
              {unit && <span className="text-ink-400"> {unit}</span>}
            </span>
          </div>
          <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-ink-800">
            <div
              className="h-full rounded-full"
              style={{ width: `${(row.count / max) * 100}%`, background: color }}
            />
          </div>
        </li>
      ))}
    </ol>
  )
}

export function Empty() {
  return (
    <div className="grid h-24 place-items-center text-mini text-ink-400">Nothing recorded yet</div>
  )
}

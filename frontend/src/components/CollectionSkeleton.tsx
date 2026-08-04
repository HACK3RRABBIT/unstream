export function CollectionSkeleton() {
  return (
    <section className="overflow-hidden rounded-panel border border-ink-700 bg-ink-900">
      <div className="flex items-center gap-5 border-b border-ink-800 p-6">
        <div className="shimmer min-w-24 size-24 rounded-btn" />
        <div className="flex-1 space-y-3">
          <div className="shimmer h-3 w-16 rounded" />
          <div className="shimmer h-6 max-w-56 w-full rounded" />
          <div className="shimmer h-3.5 w-40 rounded" />
        </div>
      </div>
      {/* Uneven title widths — a column of identical bars reads as a grid,
          not as loading content. */}
      {[52, 40, 60, 44, 48].map((width, i) => (
        <div
          key={i}
          className="flex items-center gap-4 border-b border-ink-800 px-5 py-3 last:border-b-0"
        >
          <div className="shimmer size-10 rounded-ctl" />
          <div className="flex-1 space-y-2">
            <div className="shimmer h-3.5 rounded" style={{ width: `${width}%` }} />
            <div className="shimmer h-3 rounded" style={{ width: `${width * 0.6}%` }} />
          </div>
          <div className="shimmer h-3 w-8 rounded" />
        </div>
      ))}
    </section>
  )
}

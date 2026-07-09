export function CollectionSkeleton() {
  return (
    <section className="animate-pulse overflow-hidden rounded-2xl border border-ink-700 bg-ink-900">
      <div className="flex items-center gap-5 border-b border-ink-800 p-6">
        <div className="size-24 rounded-xl bg-ink-800" />
        <div className="flex-1 space-y-3">
          <div className="h-3 w-16 rounded bg-ink-800" />
          <div className="h-6 w-56 rounded bg-ink-800" />
          <div className="h-3.5 w-40 rounded bg-ink-800" />
        </div>
      </div>
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="flex items-center gap-4 border-b border-ink-800 px-5 py-3 last:border-b-0">
          <div className="size-10 rounded-md bg-ink-800" />
          <div className="flex-1 space-y-2">
            <div className="h-3.5 w-48 rounded bg-ink-800" />
            <div className="h-3 w-32 rounded bg-ink-800" />
          </div>
          <div className="h-3 w-8 rounded bg-ink-800" />
        </div>
      ))}
    </section>
  )
}

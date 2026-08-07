import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { useMutation } from '@tanstack/react-query'
import { ArrowLeft, ArrowRight, AudioLines, Link2 as LinkIcon, Search } from 'lucide-react'
import clsx from 'clsx'
import {
  apiError,
  getArtist,
  isCatalogUrl,
  mergeResults,
  resolveUrl,
  searchCatalog,
  type ArtistDetail,
  type Collection,
  type SearchResult,
} from './lib/api'
import { UrlForm } from './components/UrlForm'
import { CollectionView } from './components/CollectionView'
import { CollectionSkeleton } from './components/CollectionSkeleton'
import { SearchResults } from './components/SearchResults'
import { ArtistView } from './components/ArtistView'
import { DownloadsDock } from './components/DownloadsDock'
import { QualityPicker } from './components/QualityPicker'
import { RecentSearches } from './components/RecentSearches'
import { DownloadsProvider, useDownloads } from './lib/downloads'
import { clearRecentSearches, recentSearches, rememberSearch } from './lib/recent'
import { ToastProvider, useToast } from './lib/toast'

/** What's on screen. A stack, so "back" walks search → artist → album. */
type View =
  | {
      type: 'search'
      query: string
      results: SearchResult[]
      /** Highest page fetched so far; infinite scroll asks for page + 1. */
      page: number
      hasMore: boolean
    }
  | { type: 'artist'; artist: ArtistDetail }
  | { type: 'collection'; url: string; collection: Collection }

const BACK_LABEL: Record<View['type'], string> = {
  search: 'برگشت به نتایج',
  artist: 'برگشت به آرتیست',
  collection: 'برگشت',
}

/** Animates its children to and from zero height. `grid-template-rows`
 *  1fr→0fr is the only way to transition to `height: auto`; the inner div does
 *  the clipping. `visibility` is in the transition list on purpose — it keeps
 *  collapsed copy out of the tab order and away from screen readers, but flips
 *  only at the end of the duration, so the content fades rather than vanishing
 *  the instant the collapse starts. */
function Collapsible({ open, children }: { open: boolean; children: ReactNode }) {
  return (
    <div
      className={clsx(
        'grid transition-[grid-template-rows] duration-300 ease-out-expo',
        open ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]',
      )}
    >
      <div
        className={clsx(
          'overflow-hidden transition-[opacity,visibility] duration-300 ease-out-expo',
          open ? 'opacity-100' : 'invisible opacity-0',
        )}
      >
        {children}
      </div>
    </div>
  )
}

const isTypingTarget = (target: EventTarget | null) => {
  const el = target as HTMLElement | null
  return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)
}

/** Toasts when a download job flips to finished, wherever the user is. */
function DownloadNotifier() {
  const { entries } = useDownloads()
  const { push } = useToast()
  const finishedState = useRef<Map<string, boolean>>(new Map())

  useEffect(() => {
    for (const entry of entries) {
      const finished = entry.job?.finished ?? false
      // First sighting records, never announces: a restored job is first seen
      // already finished and must not be toasted twice. A job started here is
      // always first seen unfinished, so nothing is lost.
      const was = finishedState.current.get(entry.jobId)
      if (finished && was === false) {
        const done = entry.job!.done
        const failed = entry.job!.failed
        if (done > 0 && failed === 0) {
          push(`${entry.name} — ${done} آهنگ آماده‌ی ذخیره‌ست`, 'success')
        } else if (done > 0) {
          push(`${entry.name} — ${done} آهنگ آماده شد، ${failed} تا دانلود نشد`, 'info')
        } else {
          push(`${entry.name} — دانلود انجام نشد`, 'error')
        }
      }
      finishedState.current.set(entry.jobId, finished)
    }
  }, [entries, push])

  return null
}

function Shell() {
  const [stack, setStack] = useState<View[]>([])
  // Deep link in the address bar (?url= / ?artist= / ?q=), captured before
  // any effect rewrites the query string.
  const [initialParams] = useState(() => new URLSearchParams(window.location.search))
  const inputRef = useRef<HTMLInputElement | null>(null)

  const { push } = useToast()

  const resolve = useMutation({ mutationFn: resolveUrl })
  const search = useMutation({
    mutationFn: ({ query, page }: { query: string; page?: number }) => searchCatalog(query, page),
  })
  const artist = useMutation({ mutationFn: getArtist })

  // Infinite scroll: appended pages must not blank the results already on
  // screen, so they never go through the `search` mutation's pending state.
  const [loadingMore, setLoadingMore] = useState(false)
  const loadingMoreRef = useRef(false)

  // Bumped whenever a shortcut focuses the search box, to flash the form.
  const [focusPulse, setFocusPulse] = useState(0)

  const [recent, setRecent] = useState(recentSearches)

  // Set when this page load came from a shared link. Landing straight in a
  // loading collection with the marketing hero above it reads as the app
  // searching on its own, so that arrival gets its own framing instead.
  const [sharedArrival, setSharedArrival] = useState<
    null | { kind: 'url' | 'artist' } | { kind: 'q'; query: string }
  >(null)

  const leaveSharedArrival = () => {
    setSharedArrival(null)
    setStack([])
    resetErrors()
  }

  // Shared mode hides the search form, so a shortcut pressed there has to
  // leave the mode first and focus once the form has actually mounted.
  const wantsFocusRef = useRef(false)
  useEffect(() => {
    if (sharedArrival || !wantsFocusRef.current) return
    wantsFocusRef.current = false
    inputRef.current?.focus()
  }, [sharedArrival])

  const resetErrors = () => {
    resolve.reset()
    search.reset()
    artist.reset()
  }

  const openCollection = (url: string, pushView: boolean) => {
    resolve.mutate(url, {
      onSuccess: (collection) => {
        const view: View = { type: 'collection', url, collection }
        setStack((s) => (pushView ? [...s, view] : [view]))
      },
    })
  }

  const handleSubmit = (input: string) => {
    resetErrors()
    setSharedArrival(null) // the user is driving now, not the link
    setRecent(rememberSearch(input))

    if (isCatalogUrl(input)) {
      openCollection(input, false)
    } else {
      search.mutate(
        { query: input },
        {
          onSuccess: (page) =>
            setStack([
              {
                type: 'search',
                query: input,
                results: page.results,
                page: page.page,
                hasMore: page.has_more,
              },
            ]),
        },
      )
    }
  }

  const handlePick = (result: SearchResult) => {
    resetErrors()
    if (result.kind === 'artist' && result.source === 'deezer') {
      artist.mutate(result.id, {
        onSuccess: (data) => setStack((s) => [...s, { type: 'artist', artist: data }]),
      })
    } else {
      // SoundCloud artists resolve their profile page — yt-dlp turns it
      // into a playlist of everything they've uploaded.
      openCollection(result.url, true)
    }
  }

  const goBack = useCallback(() => setStack((s) => s.slice(0, -1)), [])

  // Refs keep global listeners on a [] dep array without going stale.
  const openCollectionRef = useRef(openCollection)
  openCollectionRef.current = openCollection
  const stackRef = useRef(stack)
  stackRef.current = stack
  const goBackRef = useRef(goBack)
  goBackRef.current = goBack
  const pushRef = useRef(push)
  pushRef.current = push

  /** Fetch the next page and append it to the search view on top of the stack. */
  const loadMore = useCallback(async () => {
    const top = stackRef.current.at(-1)
    if (top?.type !== 'search' || !top.hasMore || loadingMoreRef.current) return

    loadingMoreRef.current = true
    setLoadingMore(true)
    try {
      const next = await searchCatalog(top.query, top.page + 1)
      setStack((s) => {
        const current = s.at(-1)
        // The user navigated (or searched again) mid-flight — drop the page.
        if (current?.type !== 'search' || current.query !== top.query) return s
        return [
          ...s.slice(0, -1),
          {
            ...current,
            results: mergeResults(current.results, next.results),
            page: next.page,
            hasMore: next.has_more,
          },
        ]
      })
    } catch (err) {
      pushRef.current(apiError(err), 'error')
    } finally {
      loadingMoreRef.current = false
      setLoadingMore(false)
    }
  }, [])

  // Deep link restore: ?url=… / ?artist=… / ?q=…
  const bootstrapped = useRef(false)
  useEffect(() => {
    if (bootstrapped.current) return
    bootstrapped.current = true
    const url = initialParams.get('url')
    const artistId = initialParams.get('artist')
    const q = initialParams.get('q')
    if (url && isCatalogUrl(url)) {
      setSharedArrival({ kind: 'url' })
      openCollection(url, false)
    } else if (artistId) {
      setSharedArrival({ kind: 'artist' })
      artist.mutate(artistId, {
        onSuccess: (data) => setStack([{ type: 'artist', artist: data }]),
      })
    } else if (q) {
      setSharedArrival({ kind: 'q', query: q })
      search.mutate(
        { query: q },
        {
          onSuccess: (page) =>
            setStack([
              {
                type: 'search',
                query: q,
                results: page.results,
                page: page.page,
                hasMore: page.has_more,
              },
            ]),
        },
      )
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Keep the address bar shareable: it always mirrors the top view.
  useEffect(() => {
    const view = stack.at(-1)
    const params = new URLSearchParams()
    if (view?.type === 'collection') params.set('url', view.url)
    else if (view?.type === 'artist') params.set('artist', view.artist.id)
    else if (view?.type === 'search') params.set('q', view.query)
    const qs = params.toString()
    window.history.replaceState(null, '', qs ? `?${qs}` : window.location.pathname)
  }, [stack])

  // A new release's service worker took over — offer a reload. (Hidden tabs
  // already reload themselves; see main.tsx.)
  useEffect(() => {
    const onUpdate = () =>
      pushRef.current('نسخه‌ی جدید آنستریم اومده', 'info', {
        label: 'رفرش',
        onClick: () => window.location.reload(),
      })
    window.addEventListener('unstream:update', onUpdate)
    return () => window.removeEventListener('unstream:update', onUpdate)
  }, [])

  // Smart paste + keyboard shortcuts.
  useEffect(() => {
    // Focus alone is easy to miss — pulse the form so the shortcut lands.
    const focusSearch = (select: boolean) => {
      setFocusPulse((n) => n + 1)
      if (!inputRef.current) {
        // Shared-link mode: swap in the search UI, then focus (see effect).
        wantsFocusRef.current = true
        setSharedArrival(null)
        return
      }
      inputRef.current.focus()
      if (select) inputRef.current.select()
    }

    const onPaste = (e: ClipboardEvent) => {
      if (isTypingTarget(e.target)) return
      const text = e.clipboardData?.getData('text')?.trim()
      if (text && isCatalogUrl(text)) {
        e.preventDefault()
        setSharedArrival(null)
        setRecent(rememberSearch(text))
        pushRef.current('لینک پیدا شد — در حال باز کردن…', 'info')
        openCollectionRef.current(text, false)
      }
    }
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        focusSearch(true)
        return
      }
      if (e.key === 'Escape') {
        if (isTypingTarget(e.target)) {
          ;(e.target as HTMLElement).blur()
          return
        }
        goBackRef.current()
        return
      }
      if (e.key === '/' && !e.metaKey && !e.ctrlKey && !e.altKey && !isTypingTarget(e.target)) {
        e.preventDefault()
        focusSearch(false)
      }
    }
    document.addEventListener('paste', onPaste)
    window.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('paste', onPaste)
      window.removeEventListener('keydown', onKey)
    }
  }, [])

  const busy = resolve.isPending || search.isPending || artist.isPending
  const error = resolve.error ?? search.error ?? artist.error
  const view = stack.at(-1)
  const previous = stack.at(-2)
  // Nothing asked for yet: the only time the hero earns its screen space.
  // `busy` counts as landed — the skeleton is already below, and holding the
  // hero up through the first search then dropping it reads as a jump. An
  // error counts too: re-expanding would push the message the user needs to
  // read down below a screen of copy they don't.
  const landing = stack.length === 0 && !busy && !error

  const goHome = () => {
    setStack([])
    setSharedArrival(null)
    resetErrors()
  }

  const viewKey = !view
    ? 'home'
    : view.type === 'collection'
      ? `collection:${view.url}`
      : view.type === 'artist'
        ? `artist:${view.artist.id}`
        : `search:${view.query}`

  return (
    <div className="safe-x flex min-h-screen flex-col bg-ink-950">
      <header className="mx-auto flex w-full max-w-3xl items-center gap-2.5 px-5 pt-[calc(2rem+var(--safe-top))]">
        {/* With the hero collapsed, the wordmark is the only way back to it —
            and the first thing anyone tries. */}
        <button
          onClick={goHome}
          disabled={landing}
          aria-label="خانه"
          className="group flex items-center gap-2.5 rounded-ctl transition disabled:cursor-default"
        >
          <span className="grid size-8 place-items-center rounded-ctl bg-lime-flash text-lime-ink transition duration-200 group-enabled:group-active:scale-95">
            <AudioLines className="size-4.5" strokeWidth={2.25} />
          </span>
          <span className="font-display text-lg font-semibold transition-colors duration-200 group-enabled:group-hover:text-lime-flash">
            آنستریم
          </span>
        </button>
        <QualityPicker className="ms-auto" />
      </header>

      <main className="mx-auto w-full max-w-3xl flex-1 px-5 pb-24">
        {sharedArrival ? (
          <section className="pt-10 pb-8">
            <div className="animate-fade-up rounded-panel border border-lime-flash/25 bg-lime-flash/[0.06] p-4 sm:p-5">
              <p className="flex items-center gap-2 text-micro font-semibold text-lime-flash">
                <LinkIcon className="size-3.5" />
                لینک اشتراکی
              </p>
              <h1 className="mt-2 font-display text-2xl font-bold text-balance">
                {error
                  ? 'این لینک اشتراکی باز نشد'
                  : sharedArrival.kind === 'q'
                    ? 'یکی یه جستجو برات فرستاده'
                    : busy
                      ? 'در حال باز کردن چیزی که برات فرستادن…'
                      : 'این رو یکی برات فرستاده'}
              </h1>
              <p className="mt-2 text-mini text-ink-300">
                {error ? (
                  'شاید لینک خراب یا خصوصی باشه، یا از منبعی باشه که آنستریم نمی‌تونه بخونتش.'
                ) : sharedArrival.kind === 'q' ? (
                  <>
                    نتایج برای{' '}
                    <span className="text-lime-flash" dir="auto">
                      «{sharedArrival.query}»
                    </span>{' '}
                    — خودکار از لینکی که دنبال کردی باز شد.
                  </>
                ) : (
                  'آنستریم این رو خودکار از روی لینکت باز کرد. آهنگ‌هایی که می‌خوای رو انتخاب کن، یا از اول شروع کن.'
                )}
              </p>
              <button
                onClick={leaveSharedArrival}
                className="group mt-4 flex items-center gap-1.5 rounded-btn border border-ink-600 px-3.5 py-2 text-mini font-medium text-ink-100 transition duration-200 hover:border-ink-400 active:scale-[0.98]"
              >
                <Search className="size-3.5" />
                جستجوی یه چیز دیگه
                <ArrowLeft className="size-3.5 transition-transform duration-200 group-hover:-translate-x-0.5" />
              </button>
            </div>
            {error && (
              <p
                role="alert"
                className="mt-4 animate-fade-up rounded-btn border border-danger/25 bg-danger/10 px-4 py-3 text-sm text-danger"
              >
                {apiError(error)}
              </p>
            )}
          </section>
        ) : (
          // The hero is a landing state. Its copy answers questions someone has
          // *before* they try the app ("no account needed"), so once they have
          // searched it is a screen of read-once marketing between them and
          // every result. It collapses; the form stays and rises to the top.
          <section
            className={clsx(
              'transition-[padding] duration-300 ease-out-expo',
              landing ? 'pt-14 pb-10 sm:pt-16 sm:pb-12' : 'pt-6 pb-5',
            )}
          >
            {/* grid-rows 1fr→0fr is the one way to transition to height:auto;
                the inner wrapper does the clipping. */}
            <Collapsible open={landing}>
              <h1 className="animate-fade-up font-display text-[clamp(2.5rem,7.5vw,4.5rem)] leading-[1.15] font-bold text-balance">
                دانلود موزیک،
                <br />
                <span className="text-lime-flash">آلبوم و پلی‌لیست</span>
              </h1>
              <p className="mt-5 max-w-md animate-fade-up text-body leading-relaxed text-ink-300 [animation-delay:80ms]">
                لینک اسپاتیفای، یوتیوب، ساندکلاد، دیزر یا اپل موزیک رو بذار — یا همه‌ی کاتالوگ‌ها رو
                یکجا جستجو کن. فایل MP3 تگ‌خورده با کاور و کیفیت دلخواهت رو بگیر؛ نه اکانت می‌خواد،
                نه ثبت‌نام.
              </p>
            </Collapsible>

            <UrlForm
              className={clsx(
                'animate-fade-up transition-[margin] duration-300 ease-out-expo [animation-delay:160ms]',
                landing && 'mt-8',
              )}
              loading={busy}
              onSubmit={handleSubmit}
              inputRef={inputRef}
              focusPulse={focusPulse}
            />

            {/* The shortcut hint is a discovery aid and the chips are a
                cold-start affordance — both belong to the empty page only. */}
            <Collapsible open={landing}>
              <p className="mt-3 animate-fade-up text-mini text-ink-400 [animation-delay:220ms]">
                برای جستجو{' '}
                <kbd className="rounded-[5px] border border-ink-700 bg-ink-900 px-1.5 py-0.5 font-sans text-micro text-ink-300">
                  /
                </kbd>{' '}
                رو بزن، یا هر جای صفحه یه لینک پیست کن.
              </p>
              <RecentSearches
                items={recent}
                onPick={handleSubmit}
                onClear={() => setRecent(clearRecentSearches())}
              />
            </Collapsible>

            {error && (
              <p
                role="alert"
                className="mt-4 animate-fade-up rounded-btn border border-danger/25 bg-danger/10 px-4 py-3 text-sm text-danger"
              >
                {apiError(error)}
              </p>
            )}
          </section>
        )}

        {busy && <CollectionSkeleton />}
        {!busy && view && (
          <div key={viewKey} className="animate-fade-up">
            {previous && (
              <button
                onClick={goBack}
                className="group mb-3 flex items-center gap-1.5 rounded-ctl px-2 py-1.5 text-mini font-medium text-ink-300 transition hover:bg-ink-800 hover:text-ink-100 active:scale-[0.98]"
              >
                <ArrowRight className="size-4 transition-transform duration-200 group-hover:translate-x-0.5" />
                {BACK_LABEL[previous.type]}
              </button>
            )}
            {view.type === 'search' && (
              <SearchResults
                query={view.query}
                results={view.results}
                hasMore={view.hasMore}
                loadingMore={loadingMore}
                onLoadMore={loadMore}
                onPick={handlePick}
              />
            )}
            {view.type === 'artist' && <ArtistView artist={view.artist} onPick={handlePick} />}
            {view.type === 'collection' && (
              <CollectionView key={view.url} url={view.url} collection={view.collection} />
            )}
          </div>
        )}
      </main>

      <footer className="mx-auto w-full max-w-3xl px-5 pb-[calc(2.5rem+var(--safe-bottom))]">
        <div
          dir="ltr"
          className="mt-5 flex flex-wrap items-center justify-center gap-x-1.5 gap-y-2 text-sm text-ink-400"
        >
          <span>Built by</span>
          <a
            href="https://x.com/_amiralibgi"
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1.5 font-medium text-ink-300 underline decoration-ink-600 underline-offset-2 transition hover:text-lime-flash hover:decoration-lime-flash/60"
          >
            <img
              src="/amirali.jpg"
              alt=""
              loading="lazy"
              className="size-5 rounded-full object-cover"
            />
            amiralibgi
          </a>
          <span>and</span>
          <a
            href="https://x.com/yazdanctx"
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1.5 font-medium text-ink-300 underline decoration-ink-600 underline-offset-2 transition hover:text-lime-flash hover:decoration-lime-flash/60"
          >
            <img
              src="/yazdan.jpg"
              alt=""
              loading="lazy"
              className="size-5 rounded-full object-cover"
            />
            yazdanctx
          </a>
        </div>
      </footer>

      <DownloadNotifier />
      <DownloadsDock />
    </div>
  )
}

export default function App() {
  return (
    <ToastProvider>
      <DownloadsProvider>
        <Shell />
      </DownloadsProvider>
    </ToastProvider>
  )
}

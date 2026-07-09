import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { ArrowLeft, AudioLines } from 'lucide-react'
import {
  apiError,
  getArtist,
  isCatalogUrl,
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
import { DownloadsProvider } from './lib/downloads'

/** What's on screen. A stack, so "back" walks search → artist → album. */
type View =
  | { type: 'search'; results: SearchResult[] }
  | { type: 'artist'; artist: ArtistDetail }
  | { type: 'collection'; url: string; collection: Collection }

const BACK_LABEL: Record<View['type'], string> = {
  search: 'Back to results',
  artist: 'Back to artist',
  collection: 'Back',
}

export default function App() {
  const [stack, setStack] = useState<View[]>([])

  const resolve = useMutation({ mutationFn: resolveUrl })
  const search = useMutation({ mutationFn: searchCatalog })
  const artist = useMutation({ mutationFn: getArtist })

  const resetErrors = () => {
    resolve.reset()
    search.reset()
    artist.reset()
  }

  const openCollection = (url: string, push: boolean) => {
    resolve.mutate(url, {
      onSuccess: (collection) => {
        const view: View = { type: 'collection', url, collection }
        setStack((s) => (push ? [...s, view] : [view]))
      },
    })
  }

  const handleSubmit = (input: string) => {
    resetErrors()
    if (isCatalogUrl(input)) {
      openCollection(input, false)
    } else {
      search.mutate(input, {
        onSuccess: (results) => setStack([{ type: 'search', results }]),
      })
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

  const busy = resolve.isPending || search.isPending || artist.isPending
  const error = resolve.error ?? search.error ?? artist.error
  const view = stack.at(-1)
  const previous = stack.at(-2)

  return (
    <DownloadsProvider>
    <div className="min-h-screen bg-ink-950">
      <header className="mx-auto flex max-w-3xl items-center gap-2.5 px-5 pt-8">
        <span className="grid size-8 place-items-center rounded-lg bg-lime-flash text-lime-ink">
          <AudioLines className="size-4.5" strokeWidth={2.25} />
        </span>
        <span className="font-display text-lg font-semibold tracking-tight">
          Unstream
        </span>
      </header>

      <main className="mx-auto max-w-3xl px-5 pb-24">
        <section className="pt-16 pb-12">
          <h1 className="font-display text-[clamp(2.5rem,7vw,4rem)] leading-[1.02] font-bold tracking-tight text-balance">
            Your music library,
            <br />
            <span className="text-lime-flash">as files.</span>
          </h1>
          <p className="mt-5 max-w-md text-[15px] leading-relaxed text-ink-300">
            Paste a Spotify, Deezer, Apple Music, YouTube or SoundCloud link —
            or search every catalog at once. Unstream finds the audio and tags
            every mp3 for you. No accounts, no keys.
          </p>
          <UrlForm className="mt-8" loading={busy} onSubmit={handleSubmit} />
          {error && (
            <p
              role="alert"
              className="mt-4 rounded-xl border border-danger/25 bg-danger/10 px-4 py-3 text-sm text-danger"
            >
              {apiError(error)}
            </p>
          )}
        </section>

        {busy && <CollectionSkeleton />}
        {!busy && view && (
          <>
            {previous && (
              <button
                onClick={() => setStack((s) => s.slice(0, -1))}
                className="mb-3 flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm font-medium text-ink-300 transition hover:bg-ink-800 hover:text-ink-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-lime-flash"
              >
                <ArrowLeft className="size-4" />
                {BACK_LABEL[previous.type]}
              </button>
            )}
            {view.type === 'search' && (
              <SearchResults results={view.results} onPick={handlePick} />
            )}
            {view.type === 'artist' && (
              <ArtistView artist={view.artist} onPick={handlePick} />
            )}
            {view.type === 'collection' && (
              <CollectionView
                key={view.url}
                url={view.url}
                collection={view.collection}
              />
            )}
          </>
        )}
      </main>

      <footer className="mx-auto max-w-3xl px-5 pb-10 text-xs text-ink-400">
        Educational project — audio is sourced from public YouTube and
        SoundCloud uploads, not streaming services. Only download what you
        have the rights to.
      </footer>

      <DownloadsDock />
    </div>
    </DownloadsProvider>
  )
}

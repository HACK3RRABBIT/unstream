import { useMutation } from '@tanstack/react-query'
import { AudioLines } from 'lucide-react'
import {
  apiError,
  isCatalogUrl,
  resolveUrl,
  searchCatalog,
  type SearchResult,
} from './lib/api'
import { UrlForm } from './components/UrlForm'
import { CollectionView } from './components/CollectionView'
import { CollectionSkeleton } from './components/CollectionSkeleton'
import { SearchResults } from './components/SearchResults'

export default function App() {
  const resolve = useMutation({ mutationFn: resolveUrl })
  const search = useMutation({ mutationFn: searchCatalog })

  const handleSubmit = (input: string) => {
    if (isCatalogUrl(input)) {
      search.reset()
      resolve.mutate(input)
    } else {
      resolve.reset()
      search.mutate(input)
    }
  }

  const handlePick = (result: SearchResult) => {
    search.reset()
    resolve.mutate(result.url)
  }

  const busy = resolve.isPending || search.isPending
  const error = resolve.error ?? search.error

  return (
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
            Your Spotify library,
            <br />
            <span className="text-lime-flash">as files.</span>
          </h1>
          <p className="mt-5 max-w-md text-[15px] leading-relaxed text-ink-300">
            Paste a track, album or playlist link — or search for one by name.
            Unstream finds the audio and tags every mp3 for you.
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
        {search.isSuccess && (
          <SearchResults results={search.data} onPick={handlePick} />
        )}
        {resolve.isSuccess && (
          <CollectionView
            key={resolve.variables}
            url={resolve.variables}
            collection={resolve.data}
          />
        )}
      </main>

      <footer className="mx-auto max-w-3xl px-5 pb-10 text-xs text-ink-400">
        Educational project — audio is sourced from public YouTube uploads, not
        Spotify streams. Only download what you have the rights to.
      </footer>
    </div>
  )
}

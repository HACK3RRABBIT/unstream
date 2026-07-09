import { useState } from 'react'
import { ArrowRight, LoaderCircle, Search } from 'lucide-react'
import clsx from 'clsx'
import { isCatalogUrl } from '../lib/api'

interface Props {
  loading: boolean
  onSubmit: (input: string) => void
  className?: string
}

export function UrlForm({ loading, onSubmit, className }: Props) {
  const [input, setInput] = useState('')
  const isUrl = isCatalogUrl(input)

  return (
    <form
      className={clsx(
        'flex items-center gap-2 rounded-2xl border border-ink-700 bg-ink-900 p-2 pl-4',
        'transition-colors focus-within:border-lime-flash/60',
        className,
      )}
      onSubmit={(e) => {
        e.preventDefault()
        if (input.trim() && !loading) onSubmit(input.trim())
      }}
    >
      <Search className="size-4 shrink-0 text-ink-400" />
      <input
        type="text"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        placeholder="Search songs, albums, playlists — or paste a Spotify / Deezer link"
        spellCheck={false}
        autoFocus
        className="min-w-0 flex-1 bg-transparent text-[15px] text-ink-100 placeholder:text-ink-600 focus:outline-none"
      />
      <button
        type="submit"
        disabled={loading || !input.trim()}
        className={clsx(
          'flex shrink-0 items-center gap-1.5 rounded-xl px-4 py-2.5 text-sm font-medium',
          'bg-lime-flash text-lime-ink transition',
          'hover:bg-lime-soft focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-lime-flash',
          'disabled:cursor-not-allowed disabled:opacity-40',
        )}
      >
        {loading ? (
          <LoaderCircle className="size-4 animate-spin" />
        ) : (
          <>
            {isUrl ? 'Fetch' : 'Search'}
            <ArrowRight className="size-4" />
          </>
        )}
      </button>
    </form>
  )
}

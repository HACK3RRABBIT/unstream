import { Disc3, MicVocal, Music2 } from 'lucide-react'
import type { ArtistDetail, SearchResult } from '../lib/api'
import { QuickDownload } from './QuickDownload'

interface Props {
  artist: ArtistDetail
  onPick: (result: SearchResult) => void
}

const formatFans = (n: number) =>
  n >= 1_000_000
    ? `${(n / 1_000_000).toFixed(1)}M fans`
    : n >= 1_000
      ? `${Math.round(n / 1_000)}K fans`
      : `${n} fans`

export function ArtistView({ artist, onPick }: Props) {
  return (
    <section className="overflow-hidden rounded-2xl border border-ink-700 bg-ink-900">
      <div className="flex flex-wrap items-center gap-5 border-b border-ink-800 p-5 sm:p-6">
        {artist.picture_url ? (
          <img
            src={artist.picture_url}
            alt=""
            className="size-20 rounded-full object-cover shadow-lg sm:size-24"
          />
        ) : (
          <div className="grid size-20 place-items-center rounded-full bg-ink-800 sm:size-24">
            <MicVocal className="size-8 text-ink-400" />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <span className="text-[11px] font-semibold tracking-[0.14em] text-lime-flash uppercase">
            Artist
          </span>
          <h2 className="mt-1 truncate font-display text-2xl font-bold tracking-tight">
            {artist.name}
          </h2>
          <p className="mt-1 text-sm text-ink-300">
            {artist.albums.length}{' '}
            {artist.albums.length === 1 ? 'release' : 'releases'}
            {artist.fan_count ? (
              <>
                <span className="mx-1.5 text-ink-600">·</span>
                {formatFans(artist.fan_count)}
              </>
            ) : null}
          </p>
        </div>
      </div>

      {artist.top_tracks.length > 0 && (
        <div className="border-b border-ink-800">
          <h3 className="flex items-center gap-2 px-5 pt-4 pb-1 text-[11px] font-semibold tracking-[0.14em] text-ink-400 uppercase">
            <Music2 className="size-3.5" />
            Top songs
          </h3>
          <ol className="pb-2">
            {artist.top_tracks.map((track, i) => (
              <li
                key={track.id}
                className="flex items-center gap-3 pr-5 transition-colors hover:bg-ink-800/60 focus-within:bg-ink-800/60"
              >
                <button
                  onClick={() => onPick(track)}
                  className="flex min-w-0 flex-1 items-center gap-4 py-2.5 pl-5 text-left focus-visible:outline-none"
                >
                  <span className="w-5 shrink-0 text-right text-[13px] tabular-nums text-ink-400">
                    {i + 1}
                  </span>
                  {track.cover_url ? (
                    <img
                      src={track.cover_url}
                      alt=""
                      loading="lazy"
                      className="size-10 shrink-0 rounded-md object-cover"
                    />
                  ) : (
                    <div className="grid size-10 shrink-0 place-items-center rounded-md bg-ink-800">
                      <Music2 className="size-4 text-ink-400" />
                    </div>
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[15px] font-medium text-ink-100">
                      {track.name}
                    </p>
                    {track.subtitle && (
                      <p className="truncate text-[13px] text-ink-400">
                        {track.subtitle}
                      </p>
                    )}
                  </div>
                </button>
                <QuickDownload result={track} />
              </li>
            ))}
          </ol>
        </div>
      )}

      {artist.albums.length > 0 && (
        <div>
          <h3 className="flex items-center gap-2 px-5 pt-4 pb-3 text-[11px] font-semibold tracking-[0.14em] text-ink-400 uppercase">
            <Disc3 className="size-3.5" />
            Discography
          </h3>
          <ul className="grid grid-cols-2 gap-x-4 gap-y-5 px-5 pb-5 sm:grid-cols-3 md:grid-cols-4">
            {artist.albums.map((album) => (
              <li key={album.id}>
                <button
                  onClick={() => onPick(album)}
                  className="group w-full text-left focus-visible:outline-none"
                >
                  <div className="relative aspect-square overflow-hidden rounded-xl bg-ink-800 transition group-hover:brightness-110 group-focus-visible:ring-2 group-focus-visible:ring-lime-flash">
                    {album.cover_url ? (
                      <img
                        src={album.cover_url}
                        alt=""
                        loading="lazy"
                        className="size-full object-cover"
                      />
                    ) : (
                      <div className="grid size-full place-items-center">
                        <Disc3 className="size-6 text-ink-400" />
                      </div>
                    )}
                  </div>
                  <p className="mt-2 truncate text-[13.5px] font-medium text-ink-100">
                    {album.name}
                  </p>
                  {album.subtitle && (
                    <p className="truncate text-xs text-ink-400">{album.subtitle}</p>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

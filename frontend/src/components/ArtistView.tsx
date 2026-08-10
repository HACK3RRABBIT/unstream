import type { CSSProperties } from 'react'
import { Disc3, MicVocal, Music2 } from 'lucide-react'
import clsx from 'clsx'
import type { ArtistDetail, SearchResult } from '../lib/api'
import { faNumerals, localizeSubtitle, useMessages } from '../lib/i18n'
import { QuickDownload } from './QuickDownload'

interface Props {
  artist: ArtistDetail
  onPick: (result: SearchResult) => void
}

export function ArtistView({ artist, onPick }: Props) {
  const m = useMessages()
  return (
    <section className="overflow-hidden rounded-panel border border-ink-700 bg-ink-900">
      <div className="flex flex-wrap items-center gap-5 border-b border-ink-800 p-5 sm:p-6">
        {artist.picture_url ? (
          <img
            src={artist.picture_url}
            alt=""
            className="size-20 rounded-full object-cover ring-1 ring-ink-700 sm:size-24"
          />
        ) : (
          <div className="grid size-20 place-items-center rounded-full bg-ink-800 ring-1 ring-ink-700 sm:size-24">
            <MicVocal className="size-8 text-ink-400" />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <span className="text-micro font-semibold text-lime-flash">{m.artist.badge}</span>
          <h2
            className={clsx(
              'mt-1 truncate font-display text-2xl font-bold',
              faNumerals(artist.name),
            )}
            dir="auto"
          >
            {artist.name}
          </h2>
          <p className="mt-1 text-mini text-ink-300">
            {m.artist.releases(artist.albums.length)}
            {artist.fan_count ? (
              <>
                <span className="mx-1.5 text-ink-600">·</span>
                {m.artist.fans(artist.fan_count)}
              </>
            ) : null}
          </p>
        </div>
      </div>

      {artist.top_tracks.length > 0 && (
        <div className="border-b border-ink-800">
          <h3 className="flex items-center gap-2 px-5 pt-4 pb-1 text-micro font-semibold text-ink-400">
            <Music2 className="size-3.5" />
            {m.artist.topTracks}
          </h3>
          <ol className="stagger pb-2">
            {artist.top_tracks.map((track, i) => (
              <li
                key={track.id}
                style={{ '--i': i } as CSSProperties}
                className="group flex items-center gap-3 pe-5 transition-colors hover:bg-ink-800/60 focus-within:bg-ink-800/60"
              >
                <button
                  onClick={() => onPick(track)}
                  className="flex min-w-0 flex-1 items-center gap-4 py-2.5 ps-5 text-start focus-visible:outline-none"
                >
                  <span className="w-5 shrink-0 text-end text-mini tabular-nums text-ink-600">
                    {m.app.num(i + 1)}
                  </span>
                  {track.cover_url ? (
                    <img
                      src={track.cover_url}
                      alt=""
                      loading="lazy"
                      className="size-10 shrink-0 rounded-ctl object-cover transition-transform duration-300 group-hover:scale-105"
                    />
                  ) : (
                    <div className="grid size-10 shrink-0 place-items-center rounded-ctl bg-ink-800">
                      <Music2 className="size-4 text-ink-400" />
                    </div>
                  )}
                  <div className="min-w-0 flex-1" dir="auto">
                    <p
                      className={clsx(
                        'truncate text-body font-medium text-ink-100',
                        faNumerals(track.name),
                      )}
                    >
                      {track.name}
                    </p>
                    {track.subtitle && (
                      <p className="truncate text-mini text-ink-400">
                        {localizeSubtitle(track.subtitle, m)}
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
          <h3 className="flex items-center gap-2 px-5 pt-4 pb-3 text-micro font-semibold text-ink-400">
            <Disc3 className="size-3.5" />
            {m.artist.discography}
          </h3>
          <ul className="stagger grid grid-cols-2 gap-x-4 gap-y-5 px-5 pb-5 sm:grid-cols-3 md:grid-cols-4">
            {artist.albums.map((album, i) => (
              <li key={album.id} style={{ '--i': i } as CSSProperties}>
                <button
                  onClick={() => onPick(album)}
                  className="group w-full text-start focus-visible:outline-none"
                  dir="auto"
                >
                  <div className="relative aspect-square overflow-hidden rounded-btn bg-ink-800 ring-1 ring-ink-700/60 transition duration-300 group-hover:-translate-y-1 group-hover:ring-ink-600 group-focus-visible:ring-2 group-focus-visible:ring-lime-flash">
                    {album.cover_url ? (
                      <img
                        src={album.cover_url}
                        alt=""
                        loading="lazy"
                        className="size-full object-cover transition-transform duration-500 group-hover:scale-110"
                      />
                    ) : (
                      <div className="grid size-full place-items-center">
                        <Disc3 className="size-6 text-ink-400" />
                      </div>
                    )}
                  </div>
                  <p
                    className={clsx(
                      'mt-2 truncate text-mini font-medium text-ink-100 transition-colors group-hover:text-lime-flash',
                      faNumerals(album.name),
                    )}
                  >
                    {album.name}
                  </p>
                  {album.subtitle && (
                    <p className="truncate text-xs text-ink-400" dir="auto">
                      {localizeSubtitle(album.subtitle, m)}
                    </p>
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

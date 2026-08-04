import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowRight,
  AudioLines,
  Link2 as LinkIcon,
  Search,
} from "lucide-react";
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
} from "./lib/api";
import { UrlForm } from "./components/UrlForm";
import { CollectionView } from "./components/CollectionView";
import { CollectionSkeleton } from "./components/CollectionSkeleton";
import { SearchResults } from "./components/SearchResults";
import { ArtistView } from "./components/ArtistView";
import { DownloadsDock } from "./components/DownloadsDock";
import { DownloadsProvider, useDownloads } from "./lib/downloads";
import { ToastProvider, useToast } from "./lib/toast";

/** What's on screen. A stack, so "back" walks search → artist → album. */
type View =
  | {
      type: "search";
      query: string;
      results: SearchResult[];
      /** Highest page fetched so far; infinite scroll asks for page + 1. */
      page: number;
      hasMore: boolean;
    }
  | { type: "artist"; artist: ArtistDetail }
  | { type: "collection"; url: string; collection: Collection };

const BACK_LABEL: Record<View["type"], string> = {
  search: "Back to results",
  artist: "Back to artist",
  collection: "Back",
};

const isTypingTarget = (target: EventTarget | null) => {
  const el = target as HTMLElement | null;
  return (
    !!el &&
    (el.tagName === "INPUT" ||
      el.tagName === "TEXTAREA" ||
      el.isContentEditable)
  );
};

/** Toasts when a download job flips to finished, wherever the user is. */
function DownloadNotifier() {
  const { entries } = useDownloads();
  const { push } = useToast();
  const finishedState = useRef<Map<string, boolean>>(new Map());

  useEffect(() => {
    for (const entry of entries) {
      const finished = entry.job?.finished ?? false;
      const was = finishedState.current.get(entry.jobId) ?? false;
      if (finished && !was) {
        const done = entry.job!.done;
        const failed = entry.job!.failed;
        if (done > 0 && failed === 0) {
          push(
            `${entry.name} — ${done} ${done === 1 ? "track" : "tracks"} ready to save`,
            "success",
          );
        } else if (done > 0) {
          push(`${entry.name} — ${done} ready, ${failed} failed`, "info");
        } else {
          push(`${entry.name} — download failed`, "error");
        }
      }
      finishedState.current.set(entry.jobId, finished);
    }
  }, [entries, push]);

  return null;
}

function Shell() {
  const [stack, setStack] = useState<View[]>([]);
  // Deep link in the address bar (?url= / ?artist= / ?q=), captured before
  // any effect rewrites the query string.
  const [initialParams] = useState(
    () => new URLSearchParams(window.location.search),
  );
  const inputRef = useRef<HTMLInputElement | null>(null);

  const { push } = useToast();

  const resolve = useMutation({ mutationFn: resolveUrl });
  const search = useMutation({
    mutationFn: ({ query, page }: { query: string; page?: number }) =>
      searchCatalog(query, page),
  });
  const artist = useMutation({ mutationFn: getArtist });

  // Infinite scroll: appended pages must not blank the results already on
  // screen, so they never go through the `search` mutation's pending state.
  const [loadingMore, setLoadingMore] = useState(false);
  const loadingMoreRef = useRef(false);

  // Bumped whenever a shortcut focuses the search box, to flash the form.
  const [focusPulse, setFocusPulse] = useState(0);

  // Set when this page load came from a shared link. Landing straight in a
  // loading collection with the marketing hero above it reads as the app
  // searching on its own, so that arrival gets its own framing instead.
  const [sharedArrival, setSharedArrival] = useState<
    null | { kind: "url" | "artist"; } | { kind: "q"; query: string }
  >(null);

  const leaveSharedArrival = () => {
    setSharedArrival(null);
    setStack([]);
    resetErrors();
  };

  // Shared mode hides the search form, so a shortcut pressed there has to
  // leave the mode first and focus once the form has actually mounted.
  const wantsFocusRef = useRef(false);
  useEffect(() => {
    if (sharedArrival || !wantsFocusRef.current) return;
    wantsFocusRef.current = false;
    inputRef.current?.focus();
  }, [sharedArrival]);

  const resetErrors = () => {
    resolve.reset();
    search.reset();
    artist.reset();
  };

  const openCollection = (url: string, pushView: boolean) => {
    resolve.mutate(url, {
      onSuccess: (collection) => {
        const view: View = { type: "collection", url, collection };
        setStack((s) => (pushView ? [...s, view] : [view]));
      },
    });
  };

  const handleSubmit = (input: string) => {
    resetErrors();
    setSharedArrival(null); // the user is driving now, not the link

    if (isCatalogUrl(input)) {
      openCollection(input, false);
    } else {
      search.mutate(
        { query: input },
        {
          onSuccess: (page) =>
            setStack([
              {
                type: "search",
                query: input,
                results: page.results,
                page: page.page,
                hasMore: page.has_more,
              },
            ]),
        },
      );
    }
  };

  const handlePick = (result: SearchResult) => {
    resetErrors();
    if (result.kind === "artist" && result.source === "deezer") {
      artist.mutate(result.id, {
        onSuccess: (data) =>
          setStack((s) => [...s, { type: "artist", artist: data }]),
      });
    } else {
      // SoundCloud artists resolve their profile page — yt-dlp turns it
      // into a playlist of everything they've uploaded.
      openCollection(result.url, true);
    }
  };

  const goBack = useCallback(() => setStack((s) => s.slice(0, -1)), []);

  // Refs keep global listeners on a [] dep array without going stale.
  const openCollectionRef = useRef(openCollection);
  openCollectionRef.current = openCollection;
  const stackRef = useRef(stack);
  stackRef.current = stack;
  const goBackRef = useRef(goBack);
  goBackRef.current = goBack;
  const pushRef = useRef(push);
  pushRef.current = push;

  /** Fetch the next page and append it to the search view on top of the stack. */
  const loadMore = useCallback(async () => {
    const top = stackRef.current.at(-1);
    if (top?.type !== "search" || !top.hasMore || loadingMoreRef.current) return;

    loadingMoreRef.current = true;
    setLoadingMore(true);
    try {
      const next = await searchCatalog(top.query, top.page + 1);
      setStack((s) => {
        const current = s.at(-1);
        // The user navigated (or searched again) mid-flight — drop the page.
        if (current?.type !== "search" || current.query !== top.query) return s;
        return [
          ...s.slice(0, -1),
          {
            ...current,
            results: mergeResults(current.results, next.results),
            page: next.page,
            hasMore: next.has_more,
          },
        ];
      });
    } catch (err) {
      pushRef.current(apiError(err), "error");
    } finally {
      loadingMoreRef.current = false;
      setLoadingMore(false);
    }
  }, []);

  // Deep link restore: ?url=… / ?artist=… / ?q=…
  const bootstrapped = useRef(false);
  useEffect(() => {
    if (bootstrapped.current) return;
    bootstrapped.current = true;
    const url = initialParams.get("url");
    const artistId = initialParams.get("artist");
    const q = initialParams.get("q");
    if (url && isCatalogUrl(url)) {
      setSharedArrival({ kind: "url" });
      openCollection(url, false);
    } else if (artistId) {
      setSharedArrival({ kind: "artist" });
      artist.mutate(artistId, {
        onSuccess: (data) => setStack([{ type: "artist", artist: data }]),
      });
    } else if (q) {
      setSharedArrival({ kind: "q", query: q });
      search.mutate(
        { query: q },
        {
          onSuccess: (page) =>
            setStack([
              {
                type: "search",
                query: q,
                results: page.results,
                page: page.page,
                hasMore: page.has_more,
              },
            ]),
        },
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keep the address bar shareable: it always mirrors the top view.
  useEffect(() => {
    const view = stack.at(-1);
    const params = new URLSearchParams();
    if (view?.type === "collection") params.set("url", view.url);
    else if (view?.type === "artist") params.set("artist", view.artist.id);
    else if (view?.type === "search") params.set("q", view.query);
    const qs = params.toString();
    window.history.replaceState(
      null,
      "",
      qs ? `?${qs}` : window.location.pathname,
    );
  }, [stack]);

  // A new release's service worker took over — offer a reload. (Hidden tabs
  // already reload themselves; see main.tsx.)
  useEffect(() => {
    const onUpdate = () =>
      pushRef.current("A new version of Unstream is available", "info", {
        label: "Reload",
        onClick: () => window.location.reload(),
      });
    window.addEventListener("unstream:update", onUpdate);
    return () => window.removeEventListener("unstream:update", onUpdate);
  }, []);

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
      if (isTypingTarget(e.target)) return;
      const text = e.clipboardData?.getData("text")?.trim();
      if (text && isCatalogUrl(text)) {
        e.preventDefault();
        setSharedArrival(null);
        pushRef.current("Link detected — fetching…", "info");
        openCollectionRef.current(text, false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        focusSearch(true);
        return;
      }
      if (e.key === "Escape") {
        if (isTypingTarget(e.target)) {
          (e.target as HTMLElement).blur();
          return;
        }
        goBackRef.current();
        return;
      }
      if (
        e.key === "/" &&
        !e.metaKey &&
        !e.ctrlKey &&
        !e.altKey &&
        !isTypingTarget(e.target)
      ) {
        e.preventDefault();
        focusSearch(false);
      }
    };
    document.addEventListener("paste", onPaste);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("paste", onPaste);
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  const busy = resolve.isPending || search.isPending || artist.isPending;
  const error = resolve.error ?? search.error ?? artist.error;
  const view = stack.at(-1);
  const previous = stack.at(-2);

  const viewKey = !view
    ? "home"
    : view.type === "collection"
      ? `collection:${view.url}`
      : view.type === "artist"
        ? `artist:${view.artist.id}`
        : `search:${view.query}`;

  return (
    <div className="min-h-screen bg-ink-950">
      <header className="mx-auto flex max-w-3xl items-center gap-2.5 px-5 pt-8">
        <span className="grid size-8 place-items-center rounded-ctl bg-lime-flash text-lime-ink">
          <AudioLines className="size-4.5" strokeWidth={2.25} />
        </span>
        <span className="font-display text-lg font-semibold tracking-tight">
          Unstream
        </span>
      </header>

      <main className="mx-auto max-w-3xl px-5 pb-24">
        {sharedArrival ? (
          <section className="pt-10 pb-8">
            <div className="animate-fade-up rounded-panel border border-lime-flash/25 bg-lime-flash/[0.06] p-4 sm:p-5">
              <p className="flex items-center gap-2 text-micro font-semibold tracking-[0.14em] text-lime-flash uppercase">
                <LinkIcon className="size-3.5" />
                Shared link
              </p>
              <h1 className="mt-2 font-display text-2xl font-bold tracking-[-0.02em] text-balance">
                {error
                  ? "That shared link didn’t open"
                  : sharedArrival.kind === "q"
                    ? "Someone shared a search with you"
                    : busy
                      ? "Opening what someone shared with you…"
                      : "Here’s what someone shared with you"}
              </h1>
              <p className="mt-2 text-mini text-ink-300">
                {error ? (
                  "The link may be broken, private, or from a source Unstream can’t read."
                ) : sharedArrival.kind === "q" ? (
                  <>
                    Results for{" "}
                    <span className="text-lime-flash">
                      “{sharedArrival.query}”
                    </span>{" "}
                    — loaded automatically from the link you followed.
                  </>
                ) : (
                  "Unstream loaded this automatically from the link you followed. Pick the tracks you want, or start fresh."
                )}
              </p>
              <button
                onClick={leaveSharedArrival}
                className="group mt-4 flex items-center gap-1.5 rounded-btn border border-ink-600 px-3.5 py-2 text-mini font-medium text-ink-100 transition duration-200 hover:border-ink-400 active:scale-[0.98]"
              >
                <Search className="size-3.5" />
                Search for something else
                <ArrowRight className="size-3.5 transition-transform duration-200 group-hover:translate-x-0.5" />
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
        <section className="pt-14 pb-10 sm:pt-16 sm:pb-12">
          <h1 className="animate-fade-up font-display text-[clamp(2.5rem,7.5vw,4.5rem)] leading-[0.98] font-bold tracking-[-0.035em] text-balance">
            Your music library,
            <br />
            <span className="text-lime-flash">as files.</span>
          </h1>
          <p className="mt-5 max-w-md animate-fade-up text-body leading-relaxed text-ink-300 [animation-delay:80ms]">
            Paste a Spotify, Deezer, Apple Music, YouTube or SoundCloud link —
            or search every catalog at once. Unstream finds the audio and tags
            every mp3 for you. No accounts, no keys.
          </p>
          <UrlForm
            className="mt-8 animate-fade-up [animation-delay:160ms]"
            loading={busy}
            onSubmit={handleSubmit}
            inputRef={inputRef}
            focusPulse={focusPulse}
          />
          <p className="mt-3 animate-fade-up text-mini text-ink-400 [animation-delay:220ms]">
            Press{" "}
            <kbd className="rounded-[5px] border border-ink-700 bg-ink-900 px-1.5 py-0.5 font-sans text-micro text-ink-300">
              /
            </kbd>{" "}
            to search, or just paste a link anywhere on the page.
          </p>
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
                <ArrowLeft className="size-4 transition-transform duration-200 group-hover:-translate-x-0.5" />
                {BACK_LABEL[previous.type]}
              </button>
            )}
            {view.type === "search" && (
              <SearchResults
                query={view.query}
                results={view.results}
                hasMore={view.hasMore}
                loadingMore={loadingMore}
                onLoadMore={loadMore}
                onPick={handlePick}
              />
            )}
            {view.type === "artist" && (
              <ArtistView artist={view.artist} onPick={handlePick} />
            )}
            {view.type === "collection" && (
              <CollectionView
                key={view.url}
                url={view.url}
                collection={view.collection}
              />
            )}
          </div>
        )}
      </main>

      <footer className="mx-auto max-w-3xl space-y-1.5 px-5 pb-10 text-xs text-ink-400">
        <p>
          Educational project — audio is sourced from public YouTube and
          SoundCloud uploads, not streaming services. Only download what you
          have the rights to.
        </p>
        <p className="text-sm">
          Built by{" "}
          <a
            href="https://github.com/amiralibg"
            target="_blank"
            rel="noreferrer"
            className="font-medium text-ink-300 underline decoration-ink-600 underline-offset-2 transition hover:text-lime-flash hover:decoration-lime-flash/60"
          >
            Amirali Beigi
          </a>
        </p>
      </footer>

      <DownloadNotifier />
      <DownloadsDock />
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <DownloadsProvider>
        <Shell />
      </DownloadsProvider>
    </ToastProvider>
  );
}

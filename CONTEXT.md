# Unstream

A web app for searching songs, albums and playlists — or pasting a Spotify/Deezer link — and downloading them as tagged MP3 files. The UI ships in Farsi and English, Farsi by default (see `docs/DESIGN.md`); this glossary fixes the canonical Farsi rendering of each domain term so the copy stays consistent.

It stays a Farsi glossary on purpose. English is the language a dictionary is _translated into_ here — `locales/en.ts` is the canonical shape and its wording is its own record — while Farsi is the language the product was written in and the one where two words for the same thing keep suggesting themselves. Terms below are what `locales/fa.ts` must use.

## Language

**آنستریم** (Unstream):
The product's name as written in all user-facing copy.
_Avoid_: Unstream (in UI copy), آن‌استریم

**آهنگ** (Track):
A single downloadable song.
_Avoid_: ترک، قطعه، موزیک — with one exception: «موزیک» is permitted in the hero headline and `<head>` metadata (title, meta description, OG, JSON-LD, manifest), where it carries search intent («دانلود موزیک»). In-app copy — counts, labels, tabs, toasts — always uses آهنگ.

**آلبوم** (Album):
A release-grouped collection of tracks fetched from a source link or search.
_Avoid_: —

**پلی‌لیست** (Playlist):
A user-curated collection of tracks from Spotify/Deezer.
_Avoid_: فهرست پخش، لیست پخش

**آرتیست** (Artist):
The performer a track or album belongs to; also a browsable view of their releases.
_Avoid_: هنرمند، خواننده

**دانلود** (Download):
Fetching a track as a tagged MP3 file; also the queue item tracking that job.
_Avoid_: بارگیری، دریافت

**جستجو** (Search):
Finding tracks, albums, artists or playlists by free text.
_Avoid_: سرچ، پیدا کردن

**لغو** (Cancel):
Stopping a download that is already running, or a search still being waited on.
One word for both, even though only the download is really called off — what
the person means is the same in each case.
_Avoid_: کنسل، توقف، متوقف کردن

**کیفیت** (Audio quality):
The bitrate chosen for a download.
_Avoid_: کوالیتی

**لینک** (Link):
A pasted Spotify or Deezer URL that resolves to a track or collection.
_Avoid_: پیوند، آدرس

**دانلود همه** (Download all):
Downloading every track in an album or playlist in one action, which yields a ZIP.
_Avoid_: دانلود کامل

**متن آهنگ** (Lyrics):
The words of a song, shown in the app and embedded into downloaded files.
_Avoid_: لیریک، شعر، کلمه‌های آهنگ

**انیمه** (Anime):
A Japanese animation series browsable and downloadable in the anime section.
_Avoid_: آنیمی، کارتون (except in marketing copy where کارتون carries search intent)

**فصل** (Season):
One run of an anime — the grouping a franchise's episodes are shown under.
_Avoid_: سِیسن، سری

**قسمت** (Episode):
A single downloadable video of a season.
_Avoid_: اپیزود

**کیفیت ویدیو** (Video quality):
The resolution chosen for an anime download (360 / 480 / 720 / 1080).
Distinct from **کیفیت** (Audio quality), which stays the mp3 bitrate.
_Avoid_: کوالیتی، رزولوشن

**زیرنویس** (Subtitle):
The text track carried by a downloaded episode (soft-sub, muxed in).
_Avoid_: ساب، سابتایتل

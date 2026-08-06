import { useState } from 'react'
import { LoaderCircle, Share2 } from 'lucide-react'
import clsx from 'clsx'
import { trackFileUrl } from '../lib/api'
import { canShareFiles, shareFilename, shareTrackFile } from '../lib/share'
import { useToast } from '../lib/toast'

interface Props {
  jobId: string
  trackId: string
  title: string
  ext: string
  /** `compact` is the dock's line height; `row` sits next to a track's save
   *  link in a collection. */
  size?: 'compact' | 'row'
}

/** Sends a finished track to the OS share sheet.
 *
 *  Rendered alongside the download link rather than instead of it: on a
 *  desktop the link is the better control, and on Android both work. This
 *  exists for iOS Safari, where `<a download>` regularly plays the file
 *  instead of saving it and the share sheet is the only way out.
 *
 *  Nothing renders at all where the browser can't share files, so the check
 *  is a feature probe rather than a guess at the platform. */
export function ShareTrack({ jobId, trackId, title, ext, size = 'row' }: Props) {
  const { push } = useToast()
  const [busy, setBusy] = useState(false)

  if (!canShareFiles()) return null

  const handleClick = async () => {
    if (busy) return
    setBusy(true)
    try {
      const outcome = await shareTrackFile(
        trackFileUrl(jobId, trackId),
        shareFilename(title, ext),
        `audio/${ext === 'mp3' ? 'mpeg' : ext}`,
      )
      if (outcome === 'unsupported')
        push('این مرورگر اشتراک‌گذاری فایل رو پشتیبانی نمی‌کنه', 'info')
    } catch {
      push('فایل برای اشتراک‌گذاری آماده نشد', 'error')
    } finally {
      setBusy(false)
    }
  }

  const compact = size === 'compact'
  return (
    <button
      onClick={handleClick}
      disabled={busy}
      title={`اشتراک‌گذاری یا ذخیره‌ی ${title}`}
      aria-label={`اشتراک‌گذاری ${title}`}
      className={clsx(
        'tap-target grid shrink-0 place-items-center rounded-ctl border border-ink-600 text-ink-300 transition duration-200 hover:border-lime-flash/50 hover:text-lime-flash active:scale-90 disabled:opacity-50',
        compact ? 'size-5' : 'size-8',
      )}
    >
      {busy ? (
        <LoaderCircle className={clsx('animate-spin', compact ? 'size-3' : 'size-4')} />
      ) : (
        <Share2 className={compact ? 'size-3' : 'size-4'} />
      )}
    </button>
  )
}

"""What resolution a video actually *is* — the rung, not the pixel height.

Every quality decision in the anime pipeline used to compare raw pixel heights
for equality, which is wrong for the releases anime actually ships in:

  * A 1080p release of a 2.39:1 film is 1920x804. Its height is 804, but no
    viewer, torrent title or streaming ladder calls that "804p" — it is the
    1080p release, and asking for 1080 must find it.
  * A 4:3 series remaster is 1440x1080 — 1080p at a non-16:9 width.
  * Encoders round: 1912x1072, 1918x1080, 848x480 are all standard rungs a
    frame or two off.

So a frame is classified into the rung a human would name it: the larger of
its own height and the height its width implies at 16:9, snapped to the
nearest standard rung when it lands close enough to one. A frame that is
genuinely off-ladder (1600x900) keeps its own number and only matches a
request for that number — never a neighbour's.

This is the single source of truth shared by the post-download ffprobe check,
the HLS master-ladder reader, and the yt-dlp format selector, so "is this the
quality that was asked for?" gets one answer everywhere.
"""

# The resolutions releases are actually cut at. A frame near one of these is
# named by it; a frame near none of them keeps its own implied height.
STANDARD_RUNGS = (144, 240, 360, 480, 540, 576, 720, 1080, 1440, 2160, 4320)

# How far from a standard rung a frame may land and still be called by its
# name. 6% covers encoder rounding (1072 -> 1080, 476 -> 480) without letting
# adjacent rungs bleed into each other — the closest pair, 540 and 576, stay
# 6.6% apart.
_SNAP_TOLERANCE = 0.06


def implied_height(width: int | None, height: int | None) -> int | None:
    """The height this frame represents, before snapping.

    The larger of the real height and the height a 16:9 frame of this width
    would have — which is what makes a letterboxed 1920x804 read as 1080 while
    a 4:3 1440x1080 still reads as 1080 from its height.
    """
    if not height or height <= 0:
        return None
    if width and width > 0:
        return max(height, round(width * 9 / 16))
    return height


def rung(width: int | None, height: int | None) -> int | None:
    """The resolution rung a frame belongs to, or None if it has no height.

    >>> rung(1920, 1080), rung(1920, 804), rung(1440, 1080)
    (1080, 1080, 1080)
    >>> rung(1280, 536), rung(848, 480), rung(1600, 900)
    (720, 480, 900)
    """
    value = implied_height(width, height)
    if value is None:
        return None
    nearest = min(STANDARD_RUNGS, key=lambda r: abs(r - value))
    if abs(nearest - value) <= nearest * _SNAP_TOLERANCE:
        return nearest
    return value


def matches(requested: str, width: int | None, height: int | None) -> bool:
    """Is a frame of these dimensions the resolution that was requested?

    `original` matches anything (it asks for whatever the source released).
    An explicit request matches only its own rung — never a neighbour's, so a
    720p file can still not be passed off as the 1080p that was asked for.
    """
    if requested == "original":
        return True
    if not requested.isdigit():
        return True
    served = rung(width, height)
    if served is None:
        return False
    return served == int(requested)


def band(requested: int) -> tuple[int, int]:
    """The (min, max) heights that count as `requested`, for a format filter."""
    return (
        int(requested * (1 - _SNAP_TOLERANCE)),
        int(round(requested * (1 + _SNAP_TOLERANCE))),
    )


def width_band(requested: int) -> tuple[int, int]:
    """The (min, max) *widths* a 16:9 frame at `requested` can have.

    The other half of the format filter: a letterboxed release is selected by
    its width (1920 for 1080p) because its height (804) is nowhere near the
    rung it belongs to.
    """
    nominal = round(requested * 16 / 9)
    return (
        int(nominal * (1 - _SNAP_TOLERANCE)),
        int(round(nominal * (1 + _SNAP_TOLERANCE))),
    )

"""Shared FFmpeg filter-graph helpers."""
import os


def escape_ffmpeg_filter_path(path: str) -> str:
    """Escapes a filesystem path for safe use inside an FFmpeg filter option
    value (e.g. `subtitles=filename='...'`).

    FFmpeg's filter-graph parser treats `:` as an option separator, so a
    Windows absolute path (`C:\\...`) breaks the `subtitles` filter unless
    the drive-letter colon is escaped -- this was a real, previously
    untested bug (add_subtitles had never been exercised with a real
    absolute path before Fase 5).
    """
    path = os.path.abspath(path).replace("\\", "/")
    path = path.replace(":", "\\:")
    path = path.replace("'", "\\'")
    return path

"""General application helper functions."""

from typing import Tuple, Optional


def parse_range_header(range_header: str, file_size: int) -> Optional[Tuple[int, int]]:
    """
    Parses HTTP Range header according to RFC 7233.
    Returns (start, end) byte tuple or None if range is unsatisfiable.
    """
    if not range_header or not range_header.startswith("bytes="):
        return 0, max(0, file_size - 1)

    try:
        bytes_str = range_header.split("=")[1].strip()
        if "," in bytes_str:  # Multipart ranges not supported
            return 0, max(0, file_size - 1)

        start_str, end_str = bytes_str.split("-")

        # Handle suffix byte ranges: e.g., bytes=-500 (last 500 bytes)
        if not start_str and end_str:
            suffix_len = int(end_str)
            if suffix_len == 0 or file_size == 0:
                return None
            start = max(0, file_size - suffix_len)
            end = file_size - 1
            return start, end

        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else file_size - 1

        # Validation for boundaries
        if start >= file_size or start > end or start < 0:
            return None  # Triggers HTTP 416 Range Not Satisfiable

        end = min(end, file_size - 1)
        return start, end
    except Exception:
        return 0, max(0, file_size - 1)


def format_bytes(bytes_num: int) -> str:
    """Formats byte count into human-readable string (e.g., 1024 -> '1 KB')."""
    if bytes_num <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(bytes_num)
    while size >= 1024.0 and i < len(units) - 1:
        size /= 1024.0
        i += 1
    return f"{size:.2f} {units[i]}"

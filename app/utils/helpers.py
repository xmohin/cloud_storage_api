from typing import Tuple, Optional

def parse_range_header(range_header: str, file_size: int) -> Tuple[int, int]:
    try:
        unit, bytes_str = range_header.split("=")
        if unit.strip().lower() != "bytes":
            return 0, file_size - 1
        start_str, end_str = bytes_str.split("-")
        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else file_size - 1
        if start >= file_size or end >= file_size or start > end:
            return 0, file_size - 1
        return start, end
    except Exception:
        return 0, file_size - 1

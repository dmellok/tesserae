"""Local return paths shared by editor flows."""

from urllib.parse import unquote


def local_return_path(raw: str | None) -> str | None:
    """Accept a local absolute path, including its query string and fragment."""
    if not raw:
        return None
    decoded = unquote(raw)
    if (
        not decoded.startswith("/")
        or decoded.startswith("//")
        or "\\" in decoded
        or any(ord(c) < 32 or ord(c) == 127 for c in decoded)
    ):
        return None
    return raw

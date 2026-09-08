import re
import unicodedata


def sanitize_filename(
    name: str | None,
    default: str = "file",
    max_length: int = 100,
    keep_unicode: bool = False,
) -> str:
    """Return a platform-safe filename with no path separators or traversal tricks.

    With keep_unicode=False the result is limited to ASCII [A-Za-z0-9._-], which is
    the safest option for storage keys and auto-generated output names.
    """
    if not name:
        return default
    # Neutralize separators so "..\..\x" or "a/../../x" can not escape a directory.
    name = name.replace("\\", "/")
    name = name.split("/")[-1]
    name = name.strip(" .")

    # Always strip control characters.
    name = "".join(ch for ch in name if not unicodedata.category(ch).startswith("C"))

    if not keep_unicode:
        name = unicodedata.normalize("NFKD", name)
        name = "".join(ch for ch in name if ord(ch) < 128)
        name = re.sub(r"[^A-Za-z0-9._-]", "_", name)

    name = name[:max_length] or default
    return name
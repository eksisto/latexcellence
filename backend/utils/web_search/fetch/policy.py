"""Shared network policy for structured page fetching."""

from urllib.parse import parse_qsl, urlsplit

from backend.utils.web_search.url_utils import url_is_safe as _url_is_safe_core


async def url_is_safe(url: str) -> bool:
    """Allow only public HTTPS URLs, including DNS validation."""
    return await _url_is_safe_core(url)


def url_has_credentials(url: str) -> bool:
    """Reject URLs whose authority or query likely contains reusable credentials."""
    parsed = urlsplit(url)
    if parsed.username or parsed.password:
        return True
    sensitive = {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "key",
        "password",
        "secret",
        "signature",
        "sig",
        "token",
    }
    for key, _value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized = key.casefold()
        if (
            normalized in sensitive
            or normalized.startswith(("x-amz-", "x-goog-"))
            or normalized.endswith(("_credential", "_secret", "_signature", "_token"))
        ):
            return True
    return False

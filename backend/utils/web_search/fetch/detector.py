"""Deterministic blocked-page and JavaScript-shell detection."""

from typing import Literal

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field

BlockageCode = Literal[
    "rate_limited",
    "challenge_required",
    "captcha_required",
    "javascript_required",
    "access_controlled",
    "paywall_detected",
]


class BlockageDetection(BaseModel):
    """Classification result with auditable signals."""

    model_config = ConfigDict(extra="forbid")

    code: BlockageCode
    confidence: float = Field(ge=0, le=1)
    signals: list[str] = Field(default_factory=list)
    retryable: bool = False


def detect_blockage(
    status_code: int,
    headers: dict[str, str],
    body: bytes,
    mime_type: str,
) -> BlockageDetection | None:
    """Classify explicit access barriers without treating keyword mentions as proof."""
    if status_code == 429:
        return BlockageDetection(
            code="rate_limited",
            confidence=1,
            signals=["http_status:429"],
            retryable=True,
        )
    if status_code == 401:
        return BlockageDetection(
            code="access_controlled",
            confidence=1,
            signals=["http_status:401"],
        )
    if "html" not in mime_type and "xhtml" not in mime_type:
        if status_code == 403:
            return BlockageDetection(
                code="access_controlled",
                confidence=0.95,
                signals=["http_status:403"],
            )
        return None

    text = body.decode("utf-8", errors="replace")
    soup = BeautifulSoup(text, "html.parser")
    title = soup.title.get_text(" ", strip=True).casefold() if soup.title else ""
    visible = soup.get_text(" ", strip=True).casefold()
    markup = text.casefold()
    signals: list[str] = []

    captcha_widget = any(
        token in markup
        for token in (
            "g-recaptcha",
            "h-captcha",
            "cf-turnstile",
            "captcha-container",
            'id="captcha"',
            "id='captcha'",
        )
    )
    captcha_heading = "captcha" in title or "verify you are human" in title
    if captcha_widget or captcha_heading:
        if captcha_widget:
            signals.append("captcha_widget")
        if captcha_heading:
            signals.append("captcha_title")
        return BlockageDetection(
            code="captcha_required", confidence=0.98, signals=signals
        )

    challenge_markers = (
        "cf-chl-",
        "cloudflare ray id",
        "checking your browser",
        "just a moment...",
        "datadome",
    )
    challenge_hits = [item for item in challenge_markers if item in markup]
    if challenge_hits and (status_code in {403, 429, 503} or len(visible) < 1500):
        return BlockageDetection(
            code="challenge_required",
            confidence=0.95,
            signals=[f"challenge:{item}" for item in challenge_hits[:3]],
        )

    password_form = soup.select_one('form input[type="password"]') is not None
    login_title = any(term in title for term in ("sign in", "log in", "login"))
    if password_form and (login_title or status_code in {401, 403}):
        return BlockageDetection(
            code="access_controlled",
            confidence=0.98,
            signals=["password_form", *(["login_title"] if login_title else [])],
        )

    paywall_node = soup.select_one(
        "[class*='paywall'], [id*='paywall'], [class*='subscription-wall']"
    )
    paywall_phrase = any(
        phrase in visible
        for phrase in (
            "subscribe to continue reading",
            "subscription required",
            "this article is for subscribers",
            "purchase access to read",
        )
    )
    if paywall_node is not None and paywall_phrase:
        return BlockageDetection(
            code="paywall_detected",
            confidence=0.97,
            signals=["paywall_container", "paywall_instruction"],
        )

    if status_code == 403:
        return BlockageDetection(
            code="access_controlled",
            confidence=0.95,
            signals=["http_status:403"],
        )

    no_script = "enable javascript" in visible or "javascript is required" in visible
    scripts = len(soup.find_all("script"))
    meaningful = " ".join(visible.split())
    app_shell = bool(soup.select_one("#root, #app, [data-reactroot]"))
    if no_script and (app_shell or scripts > 0) and len(meaningful) < 800:
        return BlockageDetection(
            code="javascript_required",
            confidence=0.92,
            signals=["javascript_notice", "empty_app_shell"],
            retryable=True,
        )

    return None

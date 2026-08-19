"""Structured web fetching, policy, and cache orchestration."""

from .cache import WebCache
from .browser import BrowserFetchResult, BrowserHealth, BrowserPool
from .coordinator import FetchCoordinator, FetchResult
from .detector import BlockageDetection, detect_blockage
from .policy import url_is_safe
from .scheduler import DomainCircuitOpen, DomainScheduler

__all__ = [
    "BlockageDetection",
    "BrowserFetchResult",
    "BrowserHealth",
    "BrowserPool",
    "DomainCircuitOpen",
    "DomainScheduler",
    "FetchCoordinator",
    "FetchResult",
    "WebCache",
    "detect_blockage",
    "url_is_safe",
]

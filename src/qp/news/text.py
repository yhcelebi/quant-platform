"""Text helpers for news (ADR 0012, 9-10): Turkish-aware folding and canonical URLs."""

from __future__ import annotations

import html
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING = re.compile(r"^(utm_[a-z]+|fbclid|gclid|mc_cid|mc_eid|ref|cmpid)$", re.IGNORECASE)
_SPACE = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)


def fold(text: str) -> str:
    """Lower-case with Turkish dotted/dotless i (İ -> i, I -> ı), then NFC."""
    text = text.replace("İ", "i").replace("I", "ı")
    return unicodedata.normalize("NFC", text.lower())


def clean(text: str) -> str:
    """Unescape HTML entities, drop tags, collapse whitespace."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return _SPACE.sub(" ", text).strip()


def normalized_title(title: str) -> str:
    """Folded, punctuation-free title for duplicate detection."""
    return _SPACE.sub(" ", _NON_WORD.sub(" ", fold(clean(title)))).strip()


def canonical_url(url: str) -> str:
    """https, lower-case host, no fragment, no tracking parameters, no duplicate slashes."""
    parts = urlsplit(url.strip())
    scheme = "https" if parts.scheme in ("http", "https") else parts.scheme
    path = re.sub(r"/{2,}", "/", parts.path) or "/"
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not _TRACKING.match(k)])
    return urlunsplit((scheme, parts.netloc.lower(), path, query, ""))

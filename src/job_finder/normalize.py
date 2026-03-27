from __future__ import annotations

import re
from hashlib import sha256
from html import unescape
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


WHITESPACE_RE = re.compile(r"\s+")
HTML_TAG_RE = re.compile(r"<[^>]+>")

US_LOCATION_HINTS = {
    "united states",
    "united states of america",
    "usa",
    "u.s.",
    "u.s.a.",
    "us",
    "remote-us",
    "remote usa",
    "remote us",
}

US_STATE_HINTS = {
    "alabama",
    "alaska",
    "arizona",
    "arkansas",
    "california",
    "colorado",
    "connecticut",
    "delaware",
    "florida",
    "georgia",
    "hawaii",
    "idaho",
    "illinois",
    "indiana",
    "iowa",
    "kansas",
    "kentucky",
    "louisiana",
    "maine",
    "maryland",
    "massachusetts",
    "michigan",
    "minnesota",
    "mississippi",
    "missouri",
    "montana",
    "nebraska",
    "nevada",
    "new hampshire",
    "new jersey",
    "new mexico",
    "new york",
    "north carolina",
    "north dakota",
    "ohio",
    "oklahoma",
    "oregon",
    "pennsylvania",
    "rhode island",
    "south carolina",
    "south dakota",
    "tennessee",
    "texas",
    "utah",
    "vermont",
    "virginia",
    "washington",
    "west virginia",
    "wisconsin",
    "wyoming",
    "dc",
    "district of columbia",
}

NON_US_HINTS = {
    "canada",
    "europe",
    "emea",
    "apac",
    "latam",
    "australia",
    "new zealand",
    "india",
    "germany",
    "france",
    "united kingdom",
    "uk",
}

GENERIC_REMOTE_HINTS = {
    "remote",
    "remote only",
    "fully remote",
    "remote available",
    "work from home",
    "wfh",
}


def normalize_whitespace(value: str | None) -> str:
    return WHITESPACE_RE.sub(" ", (value or "").strip())


def strip_html(value: str | None) -> str:
    return normalize_whitespace(unescape(HTML_TAG_RE.sub(" ", value or "")))


def canonicalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    filtered_query = [(key, value) for key, value in parse_qsl(parsed.query) if not key.startswith("utm_")]
    clean_path = parsed.path.rstrip("/") or "/"
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            clean_path,
            parsed.params,
            urlencode(filtered_query),
            "",
        )
    )


def detect_remote_type(location_text: str, remote_hint: bool = False) -> str:
    lowered = normalize_whitespace(location_text).lower()
    if remote_hint or "remote" in lowered:
        if "hybrid" in lowered:
            return "hybrid"
        return "remote"
    if "hybrid" in lowered:
        return "hybrid"
    return "onsite"


def classify_us_location(location_text: str, remote_type: str) -> str:
    lowered = normalize_whitespace(location_text).lower()
    if not lowered:
        return "assumed" if remote_type == "remote" else "unknown"
    if any(hint in lowered for hint in NON_US_HINTS):
        return "non_us"
    if any(hint in lowered for hint in US_LOCATION_HINTS):
        return "confirmed"
    if any(state in lowered for state in US_STATE_HINTS):
        return "confirmed"
    if remote_type == "remote":
        if lowered in GENERIC_REMOTE_HINTS:
            return "assumed"
        if lowered.startswith("remote -") or lowered.startswith("remote,"):
            return "assumed"
    return "unknown"


def is_us_location(location_text: str, remote_type: str = "onsite") -> bool:
    return classify_us_location(location_text, remote_type) in {"confirmed", "assumed"}


def description_fingerprint(title: str, company: str, description_text: str) -> str:
    base = "::".join([normalize_whitespace(title), normalize_whitespace(company), normalize_whitespace(description_text)])
    return sha256(base.lower().encode("utf-8")).hexdigest()

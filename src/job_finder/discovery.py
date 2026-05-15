from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from html import unescape
from typing import Iterable
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen

from job_finder.models import DiscoverySettings, SourceConfig


ATS_DISCOVERY_CONFIG: dict[str, dict[str, object]] = {
    "ashby": {
        "queries": ["site:jobs.ashbyhq.com {keyword}"],
        "allowed_hosts": {"jobs.ashbyhq.com"},
        "blocked_identifiers": {"remote"},
    },
    "greenhouse": {
        "queries": ["site:boards.greenhouse.io {keyword}", "site:job-boards.greenhouse.io {keyword}"],
        "allowed_hosts": {"boards.greenhouse.io", "www.boards.greenhouse.io", "job-boards.greenhouse.io"},
        "blocked_identifiers": {"embed"},
    },
    "lever": {
        "queries": ["site:jobs.lever.co {keyword}"],
        "allowed_hosts": {"jobs.lever.co"},
        "blocked_identifiers": set(),
    },
    "smartrecruiters": {
        "queries": ["site:jobs.smartrecruiters.com {keyword}"],
        "allowed_hosts": {"jobs.smartrecruiters.com"},
        "blocked_identifiers": {"oneclick-ui"},
    },
    "workable": {
        "queries": ["site:apply.workable.com {keyword}"],
        "allowed_hosts": {"apply.workable.com"},
        "blocked_identifiers": {"j", "jobs", "api"},
    },
}

DEFAULT_DISCOVERY_KEYWORDS = [
    "devops",
    "sre",
    "\"site reliability\"",
    "\"platform engineer\"",
    "\"cloud infrastructure\"",
    "terraform",
    "aws",
    "observability",
    "ansible",
    "automation",
]

RESULT_LINK_RE = re.compile(r'class="result__a" href="([^"]+)"')


def _fetch_text(url: str, timeout: float = 20.0) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "ignore")


def _duckduckgo_result_url(href: str) -> str | None:
    if href.startswith("//duckduckgo.com/l/?"):
        query = parse_qs(urlparse(f"https:{href}").query)
        target = query.get("uddg", [None])[0]
        return unquote(target) if target else None
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return None


def extract_source_from_url(source_type: str, url: str) -> SourceConfig | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path_parts = [part for part in parsed.path.split("/") if part]
    config = ATS_DISCOVERY_CONFIG[source_type]
    allowed_hosts = config["allowed_hosts"]
    if host not in allowed_hosts:
        return None

    identifier: str | None = None
    careers_url: str | None = None
    if source_type == "greenhouse":
        if host in {"boards.greenhouse.io", "www.boards.greenhouse.io", "job-boards.greenhouse.io"}:
            if path_parts and path_parts[0] != "embed":
                identifier = path_parts[0]
            else:
                identifier = parse_qs(parsed.query).get("for", [None])[0]
        if identifier:
            careers_url = f"https://boards.greenhouse.io/{identifier}"
    elif source_type == "lever":
        if path_parts:
            identifier = path_parts[0]
            careers_url = f"https://jobs.lever.co/{identifier}"
    elif source_type == "ashby":
        if path_parts:
            identifier = path_parts[0]
            careers_url = f"https://jobs.ashbyhq.com/{identifier}"
    elif source_type == "smartrecruiters":
        if path_parts:
            identifier = path_parts[0]
            careers_url = f"https://jobs.smartrecruiters.com/{identifier}"
    elif source_type == "workable":
        if path_parts and path_parts[0] not in {"jobs"}:
            identifier = path_parts[0]
            careers_url = f"https://apply.workable.com/{identifier}"

    if not identifier:
        return None

    identifier = identifier.strip().strip("/")
    if not identifier:
        return None
    if identifier.lower() in config["blocked_identifiers"]:
        return None

    return SourceConfig(
        company=identifier,
        source_type=source_type,
        identifier=identifier,
        careers_url=careers_url,
        metadata={"discovered_from": url},
    )


def discover_urls_via_duckduckgo(source_type: str, *, keywords: Iterable[str], pages_per_query: int) -> list[str]:
    queries = ATS_DISCOVERY_CONFIG[source_type]["queries"]
    allowed_hosts = ATS_DISCOVERY_CONFIG[source_type]["allowed_hosts"]
    discovered: list[str] = []
    seen: set[str] = set()
    for query_template in queries:
        for keyword in keywords:
            query = str(query_template).format(keyword=keyword)
            for page_index in range(pages_per_query):
                offset = page_index * 30
                url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}&s={offset}"
                body = _fetch_text(url)
                matches = RESULT_LINK_RE.findall(body)
                if not matches:
                    break
                for href in matches:
                    target = _duckduckgo_result_url(unescape(href))
                    if not target:
                        continue
                    parsed = urlparse(target)
                    if parsed.netloc.lower() not in allowed_hosts:
                        continue
                    normalized = target.rstrip("/")
                    if normalized not in seen:
                        seen.add(normalized)
                        discovered.append(normalized)
    return discovered


def discover_urls_via_bing_rss(source_type: str, *, keywords: Iterable[str], pages_per_query: int) -> list[str]:
    queries = ATS_DISCOVERY_CONFIG[source_type]["queries"]
    allowed_hosts = ATS_DISCOVERY_CONFIG[source_type]["allowed_hosts"]
    discovered: list[str] = []
    seen: set[str] = set()
    for query_template in queries:
        for keyword in keywords:
            query = str(query_template).format(keyword=keyword)
            # Bing RSS does not expose stable deep paging in a way we can rely on here,
            # so one request per query is the predictable default.
            for _page_index in range(min(pages_per_query, 1)):
                url = f"https://www.bing.com/search?format=rss&q={quote_plus(query)}"
                body = _fetch_text(url)
                root = ET.fromstring(body)
                for item in root.findall("./channel/item/link"):
                    if not item.text:
                        continue
                    target = item.text.strip()
                    parsed = urlparse(target)
                    if parsed.netloc.lower() not in allowed_hosts:
                        continue
                    normalized = target.rstrip("/")
                    if normalized not in seen:
                        seen.add(normalized)
                        discovered.append(normalized)
    return discovered


def discover_sources(settings: DiscoverySettings) -> list[SourceConfig]:
    if not settings.enabled:
        return []
    strategies = settings.strategies or [settings.strategy]
    unsupported = [strategy for strategy in strategies if strategy not in {"bing_rss", "duckduckgo"}]
    if unsupported:
        raise ValueError(f"Unsupported discovery strategy: {', '.join(unsupported)}")

    keywords = settings.keywords or list(DEFAULT_DISCOVERY_KEYWORDS)
    deduped_sources: dict[tuple[str, str], SourceConfig] = {}
    for source_type in settings.source_types:
        if source_type not in ATS_DISCOVERY_CONFIG:
            continue
        source_count = 0
        for strategy in strategies:
            try:
                if strategy == "bing_rss":
                    urls = discover_urls_via_bing_rss(
                        source_type,
                        keywords=keywords,
                        pages_per_query=settings.pages_per_query,
                    )
                else:
                    urls = discover_urls_via_duckduckgo(
                        source_type,
                        keywords=keywords,
                        pages_per_query=settings.pages_per_query,
                    )
            except Exception:
                continue
            for url in urls:
                source = extract_source_from_url(source_type, url)
                if source is None:
                    continue
                key = (source.source_type, source.identifier.lower())
                if key in deduped_sources:
                    continue
                deduped_sources[key] = source
                source_count += 1
                if source_count >= settings.max_sources_per_ats:
                    break
            if source_count >= settings.max_sources_per_ats:
                break

    sources = list(deduped_sources.values())
    sources.sort(key=lambda item: (item.source_type, item.identifier.lower()))
    return sources

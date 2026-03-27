from __future__ import annotations

import argparse
import json
from pathlib import Path

from job_finder.api import get_digest, list_sources
from job_finder.collector import collect_sources
from job_finder.discovery import discover_sources
from job_finder.models import DiscoverySettings, SourceConfig
from job_finder.storage import LocalJsonRepository, LocalPayloadStore


def _load_sources(path: str) -> list[SourceConfig]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [SourceConfig.from_dict(item) for item in payload]


def main() -> int:
    parser = argparse.ArgumentParser(description="Job finder local workflow")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_parser = subparsers.add_parser("collect", help="Collect jobs from ATS sources")
    collect_parser.add_argument("--sources", default=None, help="Optional manual source override file")
    collect_parser.add_argument("--jobs", default="data/jobs.json")
    collect_parser.add_argument("--runs", default="data/crawl_runs.json")
    collect_parser.add_argument("--source-catalog", default=None, help="Optional local source catalog file")
    collect_parser.add_argument("--raw", default="data/raw_payloads")
    collect_parser.add_argument("--discovery-pages", type=int, default=1)
    collect_parser.add_argument("--discovery-max-sources", type=int, default=40)
    collect_parser.add_argument("--discovery-max-run", type=int, default=60)
    collect_parser.add_argument("--discovery-strategies", default=None, help="Optional JSON array of discovery strategies")
    collect_parser.add_argument(
        "--discovery-keywords",
        default=None,
        help="Optional JSON array of discovery keywords",
    )

    discover_parser = subparsers.add_parser("discover", help="Discover ATS boards automatically")
    discover_parser.add_argument("--pages", type=int, default=1)
    discover_parser.add_argument("--max-sources", type=int, default=40)
    discover_parser.add_argument("--max-run", type=int, default=60)
    discover_parser.add_argument("--strategies", default=None, help="Optional JSON array of discovery strategies")
    discover_parser.add_argument("--keywords", default=None, help="Optional JSON array of discovery keywords")

    list_parser = subparsers.add_parser("list", help="List locally stored jobs")
    list_parser.add_argument("--jobs", default="data/jobs.json")
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--min-score", type=int, default=None)

    digest_parser = subparsers.add_parser("digest", help="Build a local digest of top matches")
    digest_parser.add_argument("--jobs", default="data/jobs.json")
    digest_parser.add_argument("--limit", type=int, default=20)
    digest_parser.add_argument("--min-score", type=int, default=40)

    sources_parser = subparsers.add_parser("sources", help="Inspect local source catalog quality")
    sources_parser.add_argument("--jobs", default="data/jobs.json")
    sources_parser.add_argument("--sources-path", default=None)
    sources_parser.add_argument("--limit", type=int, default=50)
    sources_parser.add_argument("--statuses", default="active,candidate")

    args = parser.parse_args()
    if args.command == "collect":
        keywords = json.loads(args.discovery_keywords) if args.discovery_keywords else []
        strategies = json.loads(args.discovery_strategies) if args.discovery_strategies else []
        discovery_settings = DiscoverySettings(
            strategies=strategies,
            pages_per_query=args.discovery_pages,
            max_sources_per_ats=args.discovery_max_sources,
            max_sources_per_run=args.discovery_max_run,
            keywords=keywords,
        )
        sources = _load_sources(args.sources) if args.sources else []
        repository = LocalJsonRepository(jobs_path=args.jobs, runs_path=args.runs, sources_path=args.source_catalog)
        payload_store = LocalPayloadStore(base_path=args.raw)
        run = collect_sources(sources, repository, payload_store, discovery_settings=discovery_settings)
        print(json.dumps(run.to_item(), indent=2))
        return 0
    if args.command == "discover":
        keywords = json.loads(args.keywords) if args.keywords else []
        strategies = json.loads(args.strategies) if args.strategies else []
        discovery_settings = DiscoverySettings(
            strategies=strategies,
            pages_per_query=args.pages,
            max_sources_per_ats=args.max_sources,
            max_sources_per_run=args.max_run,
            keywords=keywords,
        )
        print(json.dumps([source.to_dict() for source in discover_sources(discovery_settings)], indent=2))
        return 0
    if args.command == "digest":
        repository = LocalJsonRepository(jobs_path=args.jobs)
        print(json.dumps(get_digest(repository, {"limit": str(args.limit), "min_score": str(args.min_score)}), indent=2))
        return 0
    if args.command == "sources":
        repository = LocalJsonRepository(jobs_path=args.jobs, sources_path=args.sources_path)
        print(json.dumps(list_sources(repository, {"limit": str(args.limit), "statuses": args.statuses}), indent=2))
        return 0

    repository = LocalJsonRepository(jobs_path=args.jobs)
    jobs = repository.list_jobs(limit=args.limit, min_score=args.min_score)
    print(json.dumps([job.to_item() for job in jobs], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

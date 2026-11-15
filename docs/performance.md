# Measured feed read-path improvement

On November 15, 2026, the deployed default feed and shortlist switched from full-table scans to compact DynamoDB global secondary index queries. The stored jobs and API filters were retained; this was a read-model migration rather than a database replacement.

## Public API observations

The same client made three sequential requests to each endpoint before and after the switch. These are client-observed elapsed times, including network and Lambda overhead. The feed returned 100 matches and the shortlist returned 4 picks in every sample.

| Request | Before median | After median | Speedup |
| --- | ---: | ---: | ---: |
| Ranked feed, 100 jobs | 9.896 s | 0.444 s | 22.3× |
| Daily shortlist, up to 8 jobs | 15.903 s | 0.203 s | 78.3× |

All elapsed samples, in request order:

| Request | Before (seconds) | After (seconds) |
| --- | --- | --- |
| Ranked feed | 18.307, 9.896, 8.617 | 1.567, 0.444, 0.388 |
| Daily shortlist | 21.744, 15.903, 14.284 | 0.198, 0.206, 0.203 |

Exact requests:

```text
GET /jobs?limit=100&min_score=30&eligible_only=true&confirmed_us_only=true&only_new=true&sort=ranked
GET /digest?limit=8&min_score=60&hours=24
```

Three samples describe this release observation. They do not establish p95, a sustained load limit, a cold-start distribution or a latency SLA. The roadmap's representative p95 target still needs a larger measurement.

## DynamoDB work

A separate SDK parity check measured consumed read capacity for the same 100-job filter in each sort order. Every returned summary matched the legacy reader, including ordering. These are single-request observations against the production dataset; consumed capacity is not an end-to-end AWS bill.

| Sort | Scan read capacity units | Index read capacity units |
| --- | ---: | ---: |
| ranked | 122,413.5 | 44.0 |
| newest | 122,414.0 | 2,154.0 |
| newest_posted | 122,415.5 | 1,957.0 |

The indexes add storage and write amplification: each collection or review update maintains three ordering keys and a compact projected summary. Full descriptions remain in the base table. Query pages, consumed read capacity, returned counts and duration are recorded in `jobs_index_read` logs without descriptions or notes.

## Migration evidence

- Reviewed implementation: [PR #3](https://github.com/SBellamy/job-finder/pull/3), deployed source revision `b4783ff`.
- All three indexes reached ACTIVE before data migration. The table was updated in place; no data stores were replaced or destroyed.
- A dry run preceded conditional writes. Four bounded scan segments used the same migration function and independent checkpoints; previously completed updates were safe to replay. Transient write throttles were observed during backfill; adaptive SDK retries completed the migration with zero conditional conflicts.
- An independent consistent base-table scan checked **113,573 records** against index keys and summaries derived from their original attributes: **zero mismatches**.
- Each of the three indexes contained **113,573 records** across the four review statuses.
- Content-free hashes confirmed that status, score, description, notes, metadata and first/last-seen timestamps were unchanged in **10 sampled records**. The migration conditions protect captured ordering and summary inputs from concurrent changes.
- Ranked, newest-found and newest-posted results matched the old reader for the default 100-job filter. Review-history fields also matched (9 reviewed records).
- Indexed reads stayed disabled through backfill and verification. A second reviewed Terraform plan switched the read flag; dual writes stay enabled independently of that flag.
- Deployed smoke checks covered all three sort orders, distinct cursor pages, filter-bound cursors, US/score/eligibility filters, detail reads, export, review-state views, full-description search, shortlist, health and source endpoints.
- CI passed 83 Python regression tests, five frontend behavior tests and Terraform validation for the implementation release.

## Tradeoffs and next work

Global indexes are eventually consistent, so a just-reviewed job can briefly reflect older state after refresh. Deep offset pagination replays earlier pages. Full-description text search still scans the base table; the deployed `search=Terraform` smoke request took 17.965 seconds. It remains slow, and filtered broad exports need a bounded or asynchronous search path. The digest reads a recent window and has candidate/review-history caps. None of these timings imply that arbitrary searches are equally fast.

The API still needs authentication for private review data, and closed-job reconciliation is unfinished. These remain release gates for a shared production service. See the [roadmap](roadmap.md), [architecture](architecture.md), and [migration and rollback procedure](feed-indexes.md).

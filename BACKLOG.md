# Backlog

## Next: trust and coverage

- Reconcile closed jobs after complete successful board fetches; preserve saved/applied history.
- Add more verified direct employer boards and periodically audit source yield.
- Show job availability checks and source failures without implying every completed crawl was healthy.
- Add state-residency, salary and seniority preferences after the actual requirements are specified.
- Build a small labeled relevance dataset from reviewed real postings and measure ranking changes.

## Release and portfolio

- Review and merge the improvements branch so the default branch contains the current implementation and CI.
- Verify Terraform drift, release to AWS and record a deployed smoke test.
- Add authentication for review/source writes before sharing the live application broadly.
- Keep a reproducible demo and current dashboard screenshot in the README.
- Record observed collection counts, latency and AWS costs when measured; avoid illustrative performance claims.

## Later

- Query-oriented dashboard read model to replace full-table scans.
- Separate discovery and collection schedules.
- Optional email delivery for the daily shortlist.
- More explicit candidate profile and application-priority scoring.
- Direct careers-page coverage beyond supported ATS providers.

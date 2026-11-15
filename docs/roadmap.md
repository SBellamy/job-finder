# Roadmap

The project has two jobs: produce a useful daily shortlist for its owner, and demonstrate a system that is designed, shipped and operated thoughtfully. The AWS deployment stays the primary architecture. A working demo makes the project inspectable without credentials.

A new product name is intentionally undecided. Rebrand the application and README first; keep AWS resource identifiers stable unless a separately reviewed migration requires otherwise.

## What success looks like

These are targets to measure, not claims about the current deployment:

| Outcome | Initial acceptance target |
| --- | --- |
| Daily usefulness | Review the day's shortlist in 10 minutes or less |
| Relevance | At least 7 of the top 10 reviewed recommendations are worth considering, measured on 50 labeled postings |
| Geography | No known non-US or hybrid-only role in the default confirmed-US shortlist |
| Freshness | Last successful collection within 24 hours, with per-source success/failure evidence |
| Repetition | Reviewed duplicate postings do not reappear in the shortlist |
| Responsiveness | Default feed p95 below 1 second at the measured current dataset size |
| Recovery | Restore/backfill and release rollback have been exercised and documented |
| Portfolio clarity | A visitor can run the demo and explain the architecture and tradeoffs within 5 minutes |

Measure the current baseline before setting harder targets. Relevance and coverage must improve together: an empty perfect-looking feed is not success.

## R0 — November 14 release (completed)

**Status:** released through PRs #1 and #2. The local demo and live workflow were verified; the indexed read-path follow-up was deployed on November 15; see [measured performance](performance.md).

**Scope:** merge the tested improvements into `main`, deploy the exact revision with Terraform, seed verified employer boards, run a bounded collection, invalidate stale frontend cache, and verify the live workflow.

Release checks:

- Python and frontend behavior suites pass; Terraform validates.
- GitHub CI is green on the PR head to be merged.
- Inspect a saved Terraform plan, including drift; no unexpected replacement or data deletion.
- Check API health, country-confirmed filtering, search, pagination, shortlist, export and CORS from the real dashboard origin.
- Follow an employer, collect it, and verify useful matches exist; distinguish returned listings from actual good opportunities.
- Verify a review write and persistence using an existing record without changing its review choice.
- Record the source revision and smoke-test results. Keep rollback instructions alongside the release.

**Done when:** `main`, deployed Lambda code and dashboard assets represent the same reviewed revision and the smoke-test report is recorded. Do not treat a completed Terraform command as proof the product works.

## Measured release baseline

The November 14, 2026 pre-release probe found `/jobs?limit=3&eligible_only=true` at roughly 16 seconds, `/digest?limit=5` at roughly 8 seconds, and an unfiltered small export failing at the 30-second API timeout. These are individual observations, not p95 measurements. They establish that the scan-based read path is already a user-facing problem. The indexed feed work below is an immediate priority, not a hypothetical scaling exercise.

## R1 — fast daily use, private data and trustworthy availability

**Suggested effort:** 1–2 focused weeks. These are the first production-readiness gates.

1. **Indexed default feed — deployed November 15.** Three compact indexes serve ranked/newest/status views after guarded backfill, coverage and result-parity checks. [Measured latency and read capacity](performance.md) document the release. Remaining: representative p95 measurement and a bounded or asynchronous path for broad full-text searches/exports.
2. **Authentication and private review state.** Use managed sign-in and API authorization for personal jobs, notes, statuses and source controls. Public demo fixtures use a separate read-only/disposable surface. CORS is not authorization. Add tests for unauthenticated requests, denied cross-user access and log redaction.
3. **Closed-job reconciliation.** Record a complete successful board snapshot. Mark missing jobs as potentially closed, then confirm through repeat observations or the provider. Never mark jobs closed after a partial/failed fetch. Keep saved/applied records and their history.
4. **Honest collection health.** Distinguish attempted/completed/successful runs, partial failures, all-source failures and zero-job success. Track last successful fetch per board. An all-failed crawl must not keep the freshness alarm green.
5. **Reliable source controls.** Honor blocked/manual source state even during rediscovery and explicit overrides. Show recently failing sources and make recovery/retry behavior clear.

**Acceptance:** the default feed meets the latency target on representative data and broad exports cannot time out synchronously; personal notes cannot be read anonymously; a synthetic failed/partial crawl cannot close jobs or report healthy ingestion; closed roles disappear from fresh recommendations but remain in application history.

## R2 — make the shortlist fit the actual search

**Suggested effort:** 1–2 weeks after R1, using daily feedback throughout.

- Set salary, seniority, state-residency, employment type, on-call/travel and authorization preferences with the owner. Unspecified preferences stay unknown.
- Build a versioned 50-posting evaluation set: strong fits, near misses, known false positives and location edge cases. Track precision at 10 and false exclusions across ranking changes.
- Extract structured compensation, workplace restrictions and seniority where the source provides them. Preserve evidence and explicitly show unknowns rather than guessing.
- Broaden verified employers using measured yield. Favor boards that produce actual reviewable roles, not boards with the largest job counts.
- Improve duplicate identity using employer, ATS IDs, canonical URL and description evidence, with an explanation and manual override for distinct same-title roles.
- Add a once-daily opt-in digest with a persisted send ledger, so retries cannot send duplicates. Saved/applied roles stay out of new-role delivery.

**Acceptance:** the relevance target is measured against labeled records; a week of daily use produces understandable recommendations; digest retries do not resend jobs.

## R3 — release and operate it predictably

**Suggested effort:** one focused week, followed by ongoing measurement.

- Remote Terraform state with locking, encryption and a tested recovery procedure.
- GitHub Actions deployment using OIDC, constrained AWS roles and a protected release environment. Plan and approval precede apply; deploy an identifiable artifact.
- Separate app release from schema/backfill changes. Record a versioned artifact and test rollback without deleting job or application data.
- Add API latency/error and collector-yield dashboards, actionable alarms and a brief incident runbook. Include alert delivery, not only alarm resources.
- Configure appropriate data backup/retention and run a restore drill using sanitized data.
- Record actual monthly cost and request volume. Scope IAM roles by API/collector responsibility and verify provider dependencies and artifact contents.

**Acceptance:** a clean checkout can reproduce a release, a failed release has a tested rollback path, and at least one recovery/incident exercise is written up with evidence.

## R4 — extend search and collection after the first indexed feed

**Suggested effort:** one week once the workload and access patterns are known.

- Review table size, consumed reads and p50/p95 after R1.
- Extend indexed access patterns using actual filter/search usage. Preserve API contracts.
- Keep backfills resumable and reconcile counts before switching traffic.
- Choose a separate bounded search path if substring scans become expensive; do not add a search cluster for an unmeasured personal workload.
- Split discovery and collection scheduling. Add bounded work queues and idempotent retries if actual collection volume needs them.

**Acceptance:** default feed routes no longer scan the full jobs table, the latency target is verified on representative data, and a before/after cost/read comparison is documented.

## Additional coverage and portfolio tasks

- Expand beyond the supported ATS providers to direct career pages when the collection and ranking basics are trustworthy.
- Keep the demo, dashboard screenshot and observed collection/cost evidence current.
- Keep source health controls and the architecture/runbook aligned with deployed behavior.

## Work that should stay out of the critical path

- Rewriting the dashboard in a framework without a concrete user benefit.
- Adding Kubernetes/EKS just to add another technology to this application's diagram.
- LLM ranking or auto-generated application claims before deterministic relevance and evidence are trustworthy.
- Automatic applications or outreach without a separate user-authorized workflow.
- Renaming Terraform resources for branding.

## Portfolio evidence to publish as work lands

Keep the README, architecture decisions and screenshot current. Link passing CI and a reproducible demo. Write short case studies with measured before/after behavior: a ranking false positive, an ingestion failure, a read-path improvement and a recovery drill. Résumé bullets should describe implemented behavior and measured results, not the backlog.

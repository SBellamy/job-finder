# Sprint Planning

This plan is written for the current shape of `job-finder`: a personal-scale AWS job discovery system with a static dashboard, API Lambda, collector Lambda, DynamoDB storage, S3 payload capture, and Terraform-managed infrastructure.

The main product goal is simple: fresh, relevant jobs every day without needing to babysit the system.

## Current Baseline

The app can now collect fresh jobs again, and the dashboard can sort by newest found jobs. Recent work also improved API error handling, dashboard cache behavior, and collector headroom.

That said, the project still has several structural risks:

| Area | Current State | Risk |
| --- | --- | --- |
| Freshness | Collector runs on a schedule and records crawl runs | A stalled collector can go unnoticed without explicit alarms |
| Ranking quality | Scoring is rule-based with feedback adjustments | Noisy companies and broad software roles can still surface too high |
| Storage | DynamoDB tables use hash keys only; reads still rely on scans | Growth will keep making API and collector paths more expensive |
| Observability | CloudWatch logs exist, but there is no dashboard or alarm layer | Failures require manual log inspection |
| Deployment | Terraform exists, but live hotfixes have happened directly | Drift can return if Terraform is not the source of truth |
| Frontend | Static dashboard works and has core review actions | It lacks clear crawl status, paging, and recent-run visibility |
| Security | Public-release cleanup started | Credentials, CORS, and IAM should be tightened before broader use |

## Priority Update

The next iterations should bias more heavily toward coverage and review speed:

- Deeper scraping and discovery move ahead of lower-value workflow polish. Adding stable ATS providers, richer provider detail fetches, and resilient source discovery should happen before fragile one-off page scraping.
- Frontend modernization moves into the active sprint. The dashboard should feel like a daily review cockpit: fast to scan, clearer about freshness/source quality, and less visually cluttered.
- Read-model work remains important, but projected reads and pagination have reduced the immediate pain enough to focus on better results and review ergonomics first.

## Progress Update: 2026-05-15

Completed and deployed:

- Freshness endpoint, crawl freshness alarm, collector metrics, and Terraform drift checks.
- Collector concurrency guard and structured JSON collector logs.
- Dismiss reasons, source block/boost controls, compact list responses, detail endpoint, cursor pagination, and CSV/JSON export.
- Dashboard freshness panel, source quality controls, larger page loading, and a cleaner visual refresh.
- SmartRecruiters provider coverage with paged list collection and detail enrichment.
- Discovery fallback so known catalog sources still collect when search discovery has a transient failure.

In progress:

- Better posted-date handling, including a newest-posted dashboard sort.
- Source seeding for known-good boards so coverage does not depend entirely on search.

Next highest-value work:

- Backfill or seed a small known-good source set and watch source quality after the next scheduled crawls.
- Add a query-friendly dashboard read model once the table grows enough for projected scans to become a real bottleneck.
- Add a daily digest delivery path after result quality stabilizes.

## Planning Assumptions

- Sprint length: 1 week.
- Target capacity: 8 to 12 focused engineering hours per sprint.
- Primary user: one person reviewing and applying to remote infrastructure/platform roles.
- Preferred bias: reliability and result quality before adding new ATS providers.
- Deployment target: current AWS dev environment.
- Definition of done: merged code, tests updated where relevant, deployed or deploy-ready, and operational behavior verified.

## Goals By Horizon

| Horizon | Goal |
| --- | --- |
| Next 1 to 2 days | Make daily freshness visible and alertable |
| Next sprint | Reduce noisy results and make collector failures self-evident |
| Next 2 to 3 sprints | Replace scan-heavy read paths with query-friendly models |
| Later | Add delivery workflows such as email digests and application tracking |

## Sprint 0: Stabilize Freshness

Objective: make sure the app collects fresh jobs daily and surfaces failures quickly.

### 0.1 Add Collector Freshness Alarm

Priority: P0

Problem: the collector previously stopped producing fresh results while scheduled invocations continued. The system needs to alert on missing successful crawl runs, not only Lambda invocation errors.

Scope:

- Add a freshness check that treats "no completed crawl in 24 hours" as unhealthy.
- Expose the check through an API endpoint such as `GET /health/freshness` or include it in `/health`.
- Add a CloudWatch alarm or scheduled monitor against the freshness state.
- Document how to inspect the latest crawl run.

Acceptance criteria:

- A completed crawl within the threshold returns healthy.
- No completed crawl within the threshold returns unhealthy.
- The check includes latest run ID, start time, finish time, inserted count, updated count, error count, and age.
- The alarm can be tested without waiting a full day.

Implementation notes:

- Use the `crawl_runs` table as the source of truth.
- The current table is keyed by `run_id`, so the first version may scan the small crawl-runs table.
- If crawl run volume grows, add a date-based or status-based index.

### 0.2 Add Collector Concurrency Guard

Priority: P0

Problem: manual invokes or retries can overlap with scheduled runs, causing duplicate work and unnecessary DynamoDB/S3 churn.

Scope:

- Prevent overlapping collector runs with a lightweight lock.
- Use DynamoDB conditional writes or a dedicated lock item.
- Expire stale locks after the Lambda timeout window.

Acceptance criteria:

- If a collector run is active, a second collector invocation exits cleanly with a skipped run result.
- Stale locks are ignored after the configured TTL.
- Skipped runs are recorded separately from failed runs.

Implementation notes:

- This can live in the existing `crawl_runs` table or a small dedicated lock table.
- Prefer a clear `run_status` field if extending crawl-run records.

### 0.3 Make Collector Logs Useful By Default

Priority: P1

Problem: CloudWatch currently shows duration and memory, but run-level progress is hard to reason about without better structured messages.

Scope:

- Emit structured JSON logs for run start, source start, source result, run complete, and run failure.
- Include run ID, source ID, jobs found, inserted, updated, eligible, high-score count, and elapsed time.
- Avoid logging full job descriptions or raw payloads.

Acceptance criteria:

- A single CloudWatch Logs Insights query can show the latest 20 source results.
- A single query can show failed sources by error message.
- Logs do not include raw payload bodies.

### 0.4 Verify Terraform Matches Live

Priority: P0

Problem: recent live fixes changed Lambda code and collector resource settings. Terraform now needs to remain the deployment source of truth.

Scope:

- Run `terraform plan` against the current environment.
- Confirm collector timeout and memory match live values.
- Confirm dashboard cache-control settings match live S3 metadata.
- Resolve any unexpected drift.

Acceptance criteria:

- Terraform plan shows only intended changes or no changes.
- Any intentional drift is codified.
- Deployment instructions describe the expected workflow.

## Sprint 1: Improve Result Quality

Objective: make the daily feed more relevant, not just fresher.

### 1.1 Add Review Feedback Signals To Dashboard

Priority: P1

Problem: status alone is too coarse. A dismissed role may be dismissed because of company, title, location, seniority, tech stack, or just timing.

Scope:

- Add lightweight dismiss reasons.
- Suggested reasons: off target, not infrastructure, not US, too senior, too junior, weak company fit, already seen, low priority.
- Store reasons in job metadata or a dedicated feedback field.
- Feed dismiss reasons back into scoring.

Acceptance criteria:

- Dismiss action can include a reason.
- Existing dismiss behavior still works without a reason.
- Feedback profile can distinguish title-level, company-level, and reason-level signals.
- Tests cover dismiss reason persistence and scoring adjustment.

### 1.2 Tighten Generic Software Role Filtering

Priority: P1

Problem: broad engineering roles can score well from scattered keywords even when they are not a strong infrastructure/platform fit.

Scope:

- Add stronger title/description alignment requirements.
- Require infrastructure intent when the title is generic.
- Add explicit handling for finance, accounting, tax, product, full-stack, and generic AI platform false positives.
- Expand tests using recent noisy examples from the live feed.

Acceptance criteria:

- Recent false positives drop below the default visible threshold.
- Strong platform/SRE/DevOps jobs still rank high.
- Tests include both false-positive and true-positive cases.

### 1.3 Source-Level Blocklist And Priority Overrides

Priority: P1

Problem: some ATS boards repeatedly generate noise or stale roles. Source quality scoring helps, but manual controls are still needed.

Scope:

- Add source metadata fields for `blocked`, `priority`, and optional note.
- Prevent blocked sources from being selected.
- Prefer priority sources during source selection.
- Add CLI or API support to update source controls.

Acceptance criteria:

- Blocked sources do not run.
- Priority sources are selected before ordinary active sources.
- Dashboard exposes source status and lets the user see why a source was deprioritized.

### 1.4 Better Posted-Date Handling

Priority: P1

Problem: "newest found" is useful, but users also care about newly posted jobs. ATS providers expose posted dates inconsistently.

Scope:

- Normalize provider-specific posted dates into a comparable field.
- Track both `first_seen_at` and `posted_date`.
- Add dashboard sorting for newest found and newest posted.

Acceptance criteria:

- Newest found sorts by `first_seen_at`.
- Newest posted sorts by normalized posted date, falling back to `first_seen_at`.
- Tests cover missing, malformed, and provider-specific dates.

## Sprint 2: Reduce Scan-Heavy Reads

Objective: keep performance stable as the jobs table grows.

### 2.1 Add Dashboard Read Model

Priority: P0

Problem: the dashboard API still relies on DynamoDB scans plus filtering. The recent projected-scan work made it survivable, but it is not the right long-term model.

Scope:

- Create a query-friendly read model for dashboard jobs.
- Options:
  - Add GSIs to the existing jobs table.
  - Add a separate `dashboard_jobs` table maintained during upsert.
- Support the core access patterns: newest found, ranked, status, eligible, source type, and minimum score.

Acceptance criteria:

- `GET /jobs?sort=newest` does not scan the full jobs table.
- `GET /jobs?sort=ranked` does not scan the full jobs table.
- API p95 latency target is under 1 second for default dashboard requests.
- Backfill script or migration path exists for current data.

Implementation notes:

- DynamoDB does not support arbitrary compound filtering without careful key design.
- A separate read model may be simpler than overloading the raw job table.

### 2.2 Add Pagination

Priority: P1

Problem: the dashboard asks for up to 200 jobs at once and renders them all. That is workable now, but it increases payload size and browser work.

Scope:

- Add cursor-based pagination to `/jobs`.
- Return `next_cursor`.
- Update the dashboard with "Load more" behavior.
- Preserve current filters and sort order across pages.

Acceptance criteria:

- Initial dashboard load fetches 50 or fewer jobs.
- Loading more appends jobs without resetting filters or notes.
- Cursor is opaque to the frontend.
- API validates cursor shape and handles expired/invalid cursors gracefully.

### 2.3 Split Full Job Detail From List Summary

Priority: P1

Problem: list responses include full job descriptions and metadata even when the dashboard only needs a short preview.

Scope:

- Add summary shape for list responses.
- Add detail endpoint such as `GET /jobs/{job_id}` for full description and metadata.
- Render compact cards from summary data.
- Fetch detail only when expanding a card or opening a detail panel.

Acceptance criteria:

- Default `/jobs` payload size drops significantly.
- Search behavior remains acceptable, either through server-side search or summary fields.
- Job notes and status updates still work from the card.

## Sprint 3: Product Workflow Improvements

Objective: make the dashboard more useful for daily review and application tracking.

### 3.1 Recent Crawl Panel

Priority: P1

Problem: the user needs to know whether the dashboard is fresh without leaving the app.

Scope:

- Add a dashboard panel for latest crawl status.
- Show latest run time, duration, pages checked, jobs found, inserted, updated, and errors.
- Link source errors to source quality data.

Acceptance criteria:

- Dashboard clearly shows last successful crawl time.
- Failed or stale crawl state is visible at first glance.
- The panel does not block job list rendering.

### 3.2 Daily Digest Delivery

Priority: P2

Problem: the dashboard requires active checking. A daily digest would make the app useful even without opening it first.

Scope:

- Generate a daily digest from top new jobs.
- Send via email using SES or another simple provider.
- Include dashboard links and direct apply links.
- Include enough metadata to decide whether to open the dashboard.

Acceptance criteria:

- Digest sends only after a successful crawl.
- Digest includes top jobs, company, title, score reasons, and links.
- Duplicate jobs are not resent once dismissed/applied unless explicitly configured.

### 3.3 Application Pipeline View

Priority: P2

Problem: statuses exist, but the dashboard is still mostly a review feed, not an application tracker.

Scope:

- Add views for saved, applied, dismissed, and all jobs.
- Add applied date.
- Add next-action notes.
- Add export to CSV or JSON.

Acceptance criteria:

- Applied jobs can be reviewed separately from the new feed.
- Notes survive collector updates.
- Export contains enough data for spreadsheet tracking.

## Sprint 4: Discovery And Coverage

Objective: improve source coverage while reducing stale/noisy discovery.

### 4.1 Split Discovery From Collection

Priority: P1

Problem: discovery and collection happen together. If discovery is slow or noisy, it can affect the collection run.

Scope:

- Create separate scheduled Lambda handlers for discovery and collection.
- Discovery updates the source catalog.
- Collection uses the source catalog and focuses on known-good sources.

Acceptance criteria:

- Collection can run even if discovery fails.
- Discovery errors do not prevent known source collection.
- Source catalog records last discovered time separately from last checked time.

### 4.2 Add Stable Provider Coverage

Priority: P1

Problem: some relevant companies use stable ATS platforms not currently collected, so search can find them but collection cannot turn them into scored jobs.

Scope:

- Add provider adapters for stable public APIs before brittle HTML scraping.
- Fetch provider detail payloads when list APIs omit full descriptions.
- Keep per-source limits so one large company cannot consume the whole collector run.
- Avoid fragile direct careers page scraping unless the value is clear.

Acceptance criteria:

- At least one new provider source type works end to end.
- Detail enrichment improves scoring inputs without logging raw payloads.
- Source health tracking applies to the new provider.
- Failures are isolated to the source adapter.

### 4.4 Deeper Discovery Tuning

Priority: P1

Problem: discovery is currently keyword-driven and can miss good boards if a search provider returns shallow or noisy results.

Scope:

- Expand the provider list where stable APIs exist.
- Track which strategy and URL produced a source.
- Increase discovery depth selectively by source type.
- Keep collection resilient when discovery is slow or partially failing.

Acceptance criteria:

- Collector can still process known-good sources if discovery fails.
- New sources include enough metadata to debug where they came from.
- Live discovery produces new candidates without materially increasing collector failures.

### 4.3 Source Seeding Workflow

Priority: P1

Problem: discovery is search-dependent, and search results are inherently inconsistent.

Scope:

- Add an easy workflow to seed known-good companies.
- Support importing from `config/sources.example.json` or a private local file.
- Make seeded sources `manual` or priority by default.

Acceptance criteria:

- Manual source import can be run locally and in Lambda.
- Manual sources are not accidentally marked dead after one transient failure.
- Dashboard distinguishes manual, active, candidate, low-value, and dead sources.

## Technical Debt Cleanup

### D1 Convert Tests Into Focused Test Modules

Priority: P2

Problem: `tests/test_core.py` is doing too much. It covers scoring, repository behavior, discovery, collector behavior, and Lambda behavior in one file.

Scope:

- Split into `test_scoring.py`, `test_storage.py`, `test_api.py`, `test_collector.py`, and `test_discovery.py`.
- Keep shared fixtures in a small helper module.

Acceptance criteria:

- Test names and files make failures easier to locate.
- Existing test coverage is preserved.
- `python3 -m unittest discover -s tests -v` still passes.

### D2 Add CI

Priority: P1

Problem: tests currently run manually.

Scope:

- Add GitHub Actions for unit tests.
- Run `python3 -m unittest discover -s tests -v`.
- Run frontend syntax check with `node --check frontend/app.js` if Node is available.
- Optionally add Terraform validate with no secrets.

Acceptance criteria:

- Pull requests show test status.
- CI does not require AWS credentials.
- Terraform validation uses example variables or no variable file.

### D3 Package Lambda Reproducibly

Priority: P1

Problem: Lambda packaging currently zips source from Terraform, and direct updates have happened manually. The project needs a repeatable path for code deploys.

Scope:

- Define one deployment workflow.
- Consider a `make package` or script that builds the Lambda zip.
- Exclude `__pycache__` and local artifacts.
- Document direct hotfix procedure separately from normal deploy.

Acceptance criteria:

- A clean checkout can package the Lambda.
- Package hash is deterministic for unchanged source.
- Terraform and manual package flow do not fight each other.

### D4 Dependency Management

Priority: P2

Problem: `pyproject.toml` declares package metadata but no runtime dependencies. Lambda relies on the runtime-provided `boto3`.

Scope:

- Decide whether to vendor or pin `boto3`.
- Add optional development dependencies for tests and tooling.
- Document supported Python version and local setup.

Acceptance criteria:

- Local setup is a short, documented sequence.
- Runtime dependency assumptions are explicit.
- Lambda package remains small.

### D5 Normalize Error Handling

Priority: P2

Problem: API errors are now caught, but collector/source errors and API validation errors should be more consistent.

Scope:

- Add typed exceptions where useful.
- Return consistent JSON error shapes from API.
- Track source errors in a structured way rather than only free-form strings.

Acceptance criteria:

- API clients always get `{ "error": "...", "details": ... }` for failures.
- Source records can be grouped by error category.
- Tests cover invalid query params and malformed update payloads.

## Security And Public Repo Readiness

### S1 Credential Hygiene

Priority: P0

Scope:

- Rotate any AWS access keys that have been used outside normal credential management.
- Prefer AWS SSO, assumed roles, or short-lived credentials for future operations.
- Keep `terraform.tfvars`, state files, and plans ignored.

Acceptance criteria:

- No long-lived key is required for day-to-day development.
- No local state or tfvars files are tracked.
- README explains expected local config without exposing live values.

### S2 Tighten CORS

Priority: P1

Problem: `allowed_origin` defaults to `*`. This is convenient, but not ideal for a deployed dashboard.

Scope:

- Set `allowed_origin` to the CloudFront dashboard URL in live config.
- Keep `*` only for local development if needed.
- Document the expected value.

Acceptance criteria:

- Live API allows the dashboard origin.
- Live API does not allow arbitrary browser origins.
- Local preview still has a straightforward path.

### S3 Narrow IAM

Priority: P2

Problem: Lambda IAM permissions are shared across API and collector. The API does not need raw-payload S3 writes, and the collector does not need all API-like permissions.

Scope:

- Split API and collector Lambda roles.
- Grant least-privilege DynamoDB and S3 permissions per function.

Acceptance criteria:

- API role cannot write raw payloads to S3.
- Collector role can write payloads and job/source/run data.
- Terraform clearly owns both roles.

## Operational Metrics

Track these after each sprint:

| Metric | Target |
| --- | --- |
| Successful crawl age | Less than 24 hours |
| Collector duration | Less than 5 minutes |
| Collector memory | Less than 70% of configured memory |
| Jobs inserted per successful run | Non-zero on at least one daily run, unless market/source conditions explain otherwise |
| API `/jobs` p95 latency | Less than 1 second after read model work |
| Dashboard first useful render | Less than 2 seconds after static assets load |
| Result quality | Fewer than 20% obvious false positives in top 50 newest eligible jobs |

## Proposed Sprint Order

| Sprint | Theme | Must Finish |
| --- | --- | --- |
| Sprint 0 | Freshness reliability | freshness alarm, concurrency guard, Terraform drift check |
| Sprint 1 | Result quality | feedback reasons, generic role filtering, source blocklist |
| Sprint 2 | Performance foundation | read model, pagination, summary/detail split |
| Sprint 3 | Daily workflow | crawl panel, digest delivery, application pipeline |
| Sprint 4 | Coverage | split discovery/collection, source seeding, direct-source prototype |

## Immediate Next Actions

1. Add freshness health endpoint and alarm.
2. Add collector concurrency guard.
3. Run Terraform plan and resolve drift.
4. Build recent-crawl dashboard panel.
5. Collect a labeled sample of false positives from the latest feed and turn them into scoring tests.

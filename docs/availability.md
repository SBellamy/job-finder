# Freshness and posting availability

The dashboard defaults to postings seen on an employer board in the last seven days. This uses `last_seen_at`, independently of the first-discovery window. “All stored postings” includes older and missing/closed records for investigation. Saved, applied and dismissed views automatically include their history regardless of the availability selection. No records are deleted or automatically dismissed.

The API accepts `seen_after` (an ISO timestamp with timezone) and `available_only=true` for feed and export. Both run before pagination and are part of the cursor's filter signature. The daily shortlist excludes records missing from the employer's latest complete board check, confirmed closed records and records not seen within seven days. Index reads continue through filtered pages until enough matches are found.

## Availability observations

Availability is separate from personal review status:

- `open`: the collector received the posting in a successful employer-board response.
- `missing`: absent from a complete successful board snapshot. Hidden from the default feed while availability is uncertain.
- `closed`: absent from at least two distinct complete snapshots, with at least 12 hours between the first absence and confirmation.
- No observation: legacy data; recency can still be checked, but the application does not invent a closed status.

Seeing a posting again resets availability to open. Its original discovery time, notes and application/review history survive. Repeating the same check cannot manufacture a second absence. A check older than a record's last observation cannot downgrade it.

Ashby and Greenhouse publish complete board lists. Their adapters validate list shape and job identity; Greenhouse also checks any reported total. An invalid, truncated or ambiguous response cannot authorize absence reconciliation. Lever, Workable and SmartRecruiters continue to collect jobs, but do not yet authorize absence checks because full pagination/completeness has not been established in those adapters. Their recency still affects the default view.

The collector reconciles only after all returned jobs have been stored successfully. Failed fetches and failed/partial writes cannot close missing jobs. An all-failed crawl is recorded as failed and does not emit a healthy completed-crawl signal.

## Storage and concurrency

The `jobs-source-v1` global secondary index uses the existing `source_name` and `job_id` attributes, projecting only provider, last-seen time and availability. It avoids scanning the whole jobs table for each board. Terraform builds it online before deploying the new collector; no job-data backfill is needed for these existing keys.

Each board reconciliation handles up to 100 conditional updates or ten query pages per run, with eight request workers. A cursor in the source catalog resumes larger boards on later runs. A board's absence confirmations therefore reflect completed visits, not an assumption that every stored row was processed on each crawl. Source-label renames can leave older rows outside the current source partition; recency still hides them, and they must be reconciled explicitly during a source-identity migration.

Availability updates condition on the observed timestamp and previous availability, and update only the base availability and its feed-summary map. Concurrent notes and review status are untouched. Collection/review writes also use conditional retries so they preserve a fresher review or availability update rather than overwriting it. Eventual index propagation can briefly show older state on refresh.

## Normalization and rollout

Descriptions decode entity-escaped HTML, remove markup and omit script/style content. Explicit hybrid/on-site restrictions in the title/location, or a role-specific description statement, override a provider's generic remote flag. A technology reference such as “hybrid cloud” alone is not an office requirement. City/state postal forms such as “Austin, TX” provide US evidence; a bare “CA” does not.

New collections stamp the quality version and recalculate derived fields from the current posting. Refresh boards that populate the top matches after deploying this change. Old records retain honest discovery/last-seen times until an actual employer fetch observes them; a data cleanup must never make an old posting look newly verified.

Rollback requires code that understands the availability field. Keep the source index and existing data; disable the recent-results selection if investigating exclusions. Pre-availability deserializers cannot safely read enriched records.

Provider references: [Ashby public job postings](https://developers.ashbyhq.com/docs/public-job-posting-api), [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html), [Lever Postings API](https://github.com/lever/postings-api).

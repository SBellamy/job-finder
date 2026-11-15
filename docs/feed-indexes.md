# Indexed feed rollout

## Read model

The jobs table keeps its existing `job_id` primary key. Three global secondary indexes partition by review `status` and sort by deterministic composite keys for ranked, newest-found and newest-posted ordering. Negative scores, timestamp fallbacks and job-ID tie breaks use the same ordering as the local repository.

Each index projects a compact `feed_summary` map plus the attributes needed for server-side filtering. Descriptions remain in the base table; detail reads still return the original job. Query reads can stop after enough matching rows instead of scanning every description-bearing item. Cross-status views merge a bounded result from each status partition. Indexed feedback reads keep the daily digest off the full-table feedback scan too.

Writes maintain index keys and summaries whether or not the read flag is enabled. Changing a review status updates the partition as part of the same base-item write. Global indexes are eventually consistent, so a just-reviewed job can briefly reflect older state on a refresh. The browser updates its own review state immediately.

Full-description text search deliberately retains the legacy scan path. Deep offset pages also replay earlier index pages. These are explicit follow-on limits; neither should be described as a fully indexed search engine or native database cursor.

## Stage 1: indexes and dual writes

1. Leave `jobs_use_feed_indexes = false` in the deployment variables.
2. Deploy the reviewed code, IAM Query permissions and three indexes with Terraform. Inspect the saved plan; the jobs table must update in place, not be replaced.
3. Wait until all indexes report ACTIVE and both Lambdas have the new code. Existing read behavior is retained during this stage.

DynamoDB builds indexes online. Monitor index progress and write throttles; the app keeps using the old read path until the backfill is verified.

## Stage 2: guarded backfill

Install the optional AWS dependencies in an isolated environment:

```bash
python3 -m pip install -e ".[aws]"
```

Preview (no table/checkpoint writes):

```bash
PYTHONPATH=src python3 -m job_finder.migrations \
  --jobs-table job-finder-dev-jobs --region us-west-2
```

Apply, with a local checkpoint:

```bash
PYTHONPATH=src python3 -m job_finder.migrations \
  --jobs-table job-finder-dev-jobs --region us-west-2 \
  --apply --checkpoint data/feed-index-backfill.json --workers 16
```

The script updates only index attributes and normalizes missing legacy status to `new`. It conditions each update on the captured ordering and summary inputs, including notes and metadata. A concurrent review/collection wins rather than being overwritten. Notes, descriptions, application metadata and scrape timestamps are preserved. Checkpoints bind to the exact table ARN, are saved atomically after each completed scan page, and allow idempotent retry after interruption. Use `--restart` for a fresh full pass after a completed checkpoint.

Do not switch reads based only on the number of updates. Independently verify key/summary coverage, index readiness and representative result parity with the legacy reader. If a conflict did not come from a current dual writer, repair/recheck that record before switching.

## Stage 3: verify and switch

- Compare ranked/newest-found/newest-posted pages and review-state views with the legacy reader using identical filters.
- Verify cursor pages, confirmed-US/minimum-score filters, exports, full-description search and the daily digest.
- Set `jobs_use_feed_indexes = true` in the actual deployment variables, save a new plan and deploy the flag.
- Measure the same feed/digest requests before and after. Keep small-sample timings distinct from a p95 benchmark.
- Inspect `jobs_index_read` logs for query pages, returned count, duration and consumed read capacity. Logs contain no job descriptions or notes.

## Rollback

Set the deployment variable back to false and apply the resulting environment change. Keep the indexes and shadow attributes so migration writes and review history do not need reversing. Current code can read records with index attributes through either path. Do not deploy pre-migration deserializers against enriched rows or remove indexes as part of a quick rollback.

## References

- [DynamoDB secondary indexes and projections](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/GSI.html)
- [Online index operations](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/GSI.OnlineOps.html)

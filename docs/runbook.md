# Release and incident runbook

## Release checklist

1. Start from the reviewed `main` revision and run the Python/frontend checks.
2. Authenticate the deployment account and verify `aws sts get-caller-identity` against the account already recorded in Terraform state.
3. From `infra/terraform`, validate and save a plan to a private temporary path. Inspect resource actions and drift before applying that exact plan.
4. Verify that changed resources match the release. Stop on unexpected destroys, data-store replacement or account/region mismatch.
5. Apply the saved plan. Retrieve the API and dashboard outputs. Invalidate CloudFront paths for changed static assets and configuration, then wait for invalidation completion.
6. Seed desired boards through `POST /sources`. Trigger one bounded collection with an explicit source list and discovery disabled. Check the invocation payload and CloudWatch results; Lambda invocation HTTP status alone does not establish collector success.
7. Check `/health`, `/health/freshness`, `/jobs`, `/digest`, `/sources` and `/jobs/export`. Verify browser CORS preflight permits `POST /sources` from the deployed dashboard origin.
8. Verify review persistence without altering an existing review decision, and check the actual dashboard. Record the commit, Terraform summary and smoke-test evidence.

Current resources use the `job-finder` name even if the product branding changes. Do not rename data stores as part of a branding release.

## Rollback

Before applying, record both Lambda revision identifiers, current `CodeSha256` values and the previous Git revision. Keep the prior source commit available.

For an application rollback, restore the previous reviewed source revision in an isolated checkout, preserving the same Terraform variable/state configuration, and inspect a new plan. Apply only the intended code/static-asset updates. Invalidate the affected CloudFront paths again. Do not overwrite or delete job/source/application data, and do not use `terraform destroy` for rollback.

The current stack does not use Lambda aliases with immutable release artifacts, so rollback requires a rebuild/redeploy. Adding versioned artifacts and aliases belongs to R3; do not claim instant rollback until it exists.

## Stale or empty feed

- Check the latest completed run and each source's last successful fetch. A recent attempted run can still have failed every source.
- Compare scheduler activity with the collector log group, error metrics and source catalog. Confirm the scheduler points to the intended function.
- Run a bounded explicit-source collection to distinguish discovery failures from ATS/provider failures.
- Inspect country-confirmation, minimum score, review status and time-window filters before interpreting an empty shortlist as missing data.
- Check pagination/export/API latency and Lambda logs for timeouts. The default feed uses compact indexes; full-description search still scans. Inspect `jobs_index_read` query-page/read-capacity logs and check the deployed read flag. See [performance evidence](performance.md).

## Privacy and access

The current API does not authenticate users. Personal review notes and source controls require proper authorization before treating this as a shared production service. Keep the résumé PDF, conversation transcripts, credentials, state files and local variables out of Git. The public demo uses illustrative data.

## After an incident

Record symptoms, user impact, detection, root cause, recovery and one verified preventive change. Include timestamps and measured evidence; omit raw credentials, private notes and full job payloads from public incident reports.

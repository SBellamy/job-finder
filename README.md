# job-finder

Job discovery pipeline for remote US DevOps, SRE, platform, and infrastructure roles.

It collects jobs from public ATS boards, normalizes and scores them, stores them in AWS, and serves a small review dashboard.

## What It Covers

- ATS providers: `ashby`, `greenhouse`, `lever`, `workable`
- Discovery-first collection from public ATS boards
- Persistent source catalog so discovered boards accumulate over time
- Scoring for AWS, Terraform, CI/CD, automation, observability, Linux, and related platform work
- Filters for remote and US-based roles
- Dashboard support for `new`, `saved`, `dismissed`, and `applied`

## Repository Layout

```text
config/                  sample local config
frontend/                static dashboard
infra/terraform/         AWS infrastructure
src/job_finder/          collectors, discovery, scoring, storage, API, CLI
tests/                   unit tests
```

## How It Works

Collection runs in two steps:

1. Discover ATS boards from public search results.
2. Fetch jobs from those boards, normalize them, score them, and upsert them.

Discovered boards are stored in a source catalog. Later runs reuse that catalog, so coverage grows over time instead of depending entirely on the latest search results.

The default scoring favors:

- AWS
- Terraform
- CI/CD
- observability
- automation
- DevOps / SRE / platform / infrastructure titles

It penalizes:

- non-remote roles
- non-US roles
- Kubernetes-heavy roles
- clearly off-target title families

## Local Use

Collect jobs locally:

```bash
PYTHONPATH=src python3 -m job_finder collect \
  --discovery-keywords '["devops","sre","platform engineer","terraform","aws"]'
```

Discover boards without collecting jobs:

```bash
PYTHONPATH=src python3 -m job_finder discover \
  --keywords '["devops","sre","platform engineer","terraform","aws"]'
```

List locally stored jobs:

```bash
PYTHONPATH=src python3 -m job_finder list --limit 25 --min-score 30
```

Run tests:

```bash
python3 -m unittest discover -s tests -v
```

Local data is written under `data/`.

Manual ATS overrides are still supported:

```bash
PYTHONPATH=src python3 -m job_finder collect --sources config/sources.example.json
```

## Dashboard

The frontend is plain static HTML, CSS, and JavaScript.

- `frontend/index.html` renders the review UI
- `frontend/app.js` calls the API
- Terraform uploads the static assets and a generated `config.json` to S3

For local preview, create `frontend/config.json` with an `apiBaseUrl` and serve `frontend/` with any static file server.

## AWS Deployment

Terraform provisions:

- Lambda for collection and API
- EventBridge Scheduler for recurring collection
- DynamoDB for jobs, crawl runs, and the source catalog
- S3 for raw payloads and dashboard assets
- API Gateway for the dashboard API
- CloudFront for the static frontend
- AWS Budgets for a monthly cost alarm

Basic deploy flow:

```bash
cd infra/terraform
terraform init
terraform validate
terraform plan
terraform apply
```

Start from `infra/terraform/terraform.tfvars.example` and set:

- AWS region
- environment name
- discovery keywords and limits
- collection schedule
- optional budget alert email

Useful outputs:

- API base URL
- dashboard URL
- jobs table name
- raw payload bucket name

## Discovery Notes

Discovery is based on public search indexing. That means:

- it is not exhaustive
- results depend on what search engines expose
- some ATS boards will be stale or dead

To reduce that problem, the app now keeps a source catalog and tracks source health. Boards that work are reused. Boards that repeatedly fail are marked dead and stop being scheduled.

## Known Limits

- Direct non-ATS careers pages are not covered yet.
- DynamoDB access still uses scans and is only suitable for personal-scale usage.
- Search-driven discovery is still the weakest part of the system.
- Some broad software roles still need ranking cleanup even after the recent scoring pass.
- AWS Budgets is an alert, not a hard spend cap.

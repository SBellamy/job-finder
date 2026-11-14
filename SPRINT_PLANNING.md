# Implementation status and release plan

## Current pass

Improve daily usefulness while keeping AWS infrastructure as credible portfolio evidence.

Implemented:

- A disposable local demo and a local dashboard/API path without AWS credentials.
- A verified starter source catalog and board following from an employer URL.
- Daily shortlist windows, company diversity, duplicate/review-history suppression and old-posting filtering.
- Geography boundaries, provider workplace fields, publication dates and clearer uncertainty.
- Role relevance guards, non-stacking title bonuses and feedback that respects dismissal intent.
- Server-side search/source/first-seen filters shared by pagination and export.
- Browser request ordering, independent insight failures and review-state updates.
- Architecture, tradeoffs and search requirements documented for a new contributor.

## Release work

1. Run the behavioral tests and frontend checks.
2. Review the diff against the existing improvements branch.
3. Merge tested work into the public default branch through normal review.
4. Run Terraform plan in the deployment environment and resolve intended drift.
5. Deploy, collect again, and verify freshness, ranked listings, review writes and CSV export.
6. Add actual operational evidence to the README once measured.

## Definition of useful

- A daily visit yields a small set of relevant, plausibly fresh direct-employer roles.
- Reviewed copies do not crowd the shortlist.
- Location uncertainty and role tradeoffs are visible.
- Collection failure is discoverable.
- Another developer can run the demo and understand why the system was designed this way.

The backlog contains follow-on work. Deployment and measured production behavior must not be inferred from local tests.

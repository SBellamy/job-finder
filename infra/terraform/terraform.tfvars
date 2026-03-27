aws_region     = "us-west-2"
environment    = "dev"
allowed_origin = "*"
log_retention_days         = 7
raw_payload_retention_days = 14
monthly_cost_budget_usd    = 50

collector_schedule_expressions = [
  "cron(0 0,6,12,18 * * ? *)",
]

collector_discovery = {
  enabled                = true
  strategy               = "bing_rss"
  strategies             = ["bing_rss", "duckduckgo"]
  keywords               = ["devops", "sre", "site reliability", "platform engineer", "platform engineering", "infrastructure engineer", "developer experience", "terraform", "aws", "observability", "release engineering", "build engineer"]
  pages_per_query        = 2
  max_sources_per_ats    = 25
  max_sources_per_run    = 60
  source_error_threshold = 2
  max_low_value_rechecks_per_run = 3
  low_value_retry_hours  = 48
  source_types           = ["ashby", "greenhouse", "lever", "workable"]
}

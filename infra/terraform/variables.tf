variable "aws_region" {
  description = "AWS region for all infrastructure."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name prefix."
  type        = string
  default     = "job-finder"
}

variable "environment" {
  description = "Environment suffix used in resource naming."
  type        = string
  default     = "dev"
}

variable "allowed_origin" {
  description = "CORS origin allowed to call the API."
  type        = string
  default     = "*"
}

variable "force_destroy_buckets" {
  description = "Allow Terraform to destroy non-empty S3 buckets."
  type        = bool
  default     = false
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days."
  type        = number
  default     = 7
}

variable "raw_payload_retention_days" {
  description = "How long to keep raw ATS payloads in S3."
  type        = number
  default     = 14
}

variable "monthly_cost_budget_usd" {
  description = "Optional account-level monthly AWS cost budget in USD. Set to 0 to disable."
  type        = number
  default     = 50
}

variable "budget_alert_email" {
  description = "Optional email address for monthly AWS budget alerts."
  type        = string
  default     = null
}

variable "raw_payload_bucket_name" {
  description = "Optional explicit bucket name for raw payloads."
  type        = string
  default     = null
}

variable "dashboard_bucket_name" {
  description = "Optional explicit bucket name for dashboard assets."
  type        = string
  default     = null
}

variable "collector_schedule_expressions" {
  description = "EventBridge Scheduler expressions in UTC."
  type        = list(string)
  default = [
    "cron(0 15,22 ? * MON-FRI *)",
  ]
}

variable "collector_sources" {
  description = "Optional manual ATS source overrides. Leave empty for discovery-first collection."
  type = list(object({
    company      = string
    source_type  = string
    identifier   = string
    company_size = optional(string, "unknown")
    company_tags = optional(list(string), [])
    careers_url  = optional(string, null)
    enabled      = optional(bool, true)
    metadata     = optional(map(string), {})
  }))
  default = []
}

variable "collector_discovery" {
  description = "Discovery-first settings used to find ATS boards automatically."
  type = object({
    enabled                        = optional(bool, true)
    strategy                       = optional(string, "bing_rss")
    strategies                     = optional(list(string), [])
    keywords                       = optional(list(string), ["devops", "sre", "site reliability", "platform engineer", "cloud infrastructure", "terraform", "aws", "observability", "ansible", "automation"])
    pages_per_query                = optional(number, 1)
    max_sources_per_ats            = optional(number, 40)
    max_sources_per_run            = optional(number, 60)
    source_error_threshold         = optional(number, 2)
    max_low_value_rechecks_per_run = optional(number, 3)
    low_value_retry_hours          = optional(number, 48)
    source_types                   = optional(list(string), ["ashby", "greenhouse", "lever", "workable"])
  })
  default = {}
}

variable "tags" {
  description = "Additional resource tags."
  type        = map(string)
  default     = {}
}

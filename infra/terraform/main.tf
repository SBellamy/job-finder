locals {
  name_prefix = "${var.project_name}-${var.environment}"
  common_tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
    },
    var.tags,
  )
  dashboard_bucket_name   = coalesce(var.dashboard_bucket_name, "${local.name_prefix}-${data.aws_caller_identity.current.account_id}-${var.aws_region}-dashboard")
  raw_payload_bucket_name = coalesce(var.raw_payload_bucket_name, "${local.name_prefix}-${data.aws_caller_identity.current.account_id}-${var.aws_region}-raw-payloads")
  lambda_environment = {
    JOBS_TABLE_NAME         = aws_dynamodb_table.jobs.name
    CRAWL_RUNS_TABLE_NAME   = aws_dynamodb_table.crawl_runs.name
    SOURCES_TABLE_NAME      = aws_dynamodb_table.sources.name
    RAW_PAYLOAD_BUCKET_NAME = aws_s3_bucket.raw_payloads.id
    ALLOWED_ORIGIN          = var.allowed_origin
    PROJECT_NAME            = var.project_name
    ENVIRONMENT             = var.environment
  }
  dashboard_assets = {
    "index.html" = {
      path          = "${path.module}/../../frontend/index.html"
      content_type  = "text/html; charset=utf-8"
      cache_control = "no-cache, no-store, must-revalidate"
    }
    "styles.css" = {
      path          = "${path.module}/../../frontend/styles.css"
      content_type  = "text/css; charset=utf-8"
      cache_control = "no-cache, no-store, must-revalidate"
    }
    "app.js" = {
      path          = "${path.module}/../../frontend/app.js"
      content_type  = "application/javascript; charset=utf-8"
      cache_control = "no-cache, no-store, must-revalidate"
    }
  }
  schedule_map = {
    for index, expression in var.collector_schedule_expressions :
    format("schedule-%02d", index + 1) => expression
  }
}

data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../../src"
  output_path = "${path.module}/build/job_finder_lambda.zip"
}

data "aws_caller_identity" "current" {}

data "aws_cloudfront_cache_policy" "caching_optimized" {
  name = "Managed-CachingOptimized"
}

resource "aws_dynamodb_table" "jobs" {
  name         = "${local.name_prefix}-jobs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "job_id"

  attribute {
    name = "job_id"
    type = "S"
  }

  tags = local.common_tags
}

resource "aws_dynamodb_table" "crawl_runs" {
  name         = "${local.name_prefix}-crawl-runs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "run_id"

  attribute {
    name = "run_id"
    type = "S"
  }

  tags = local.common_tags
}

resource "aws_dynamodb_table" "sources" {
  name         = "${local.name_prefix}-sources"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "source_id"

  attribute {
    name = "source_id"
    type = "S"
  }

  tags = local.common_tags
}

resource "aws_s3_bucket" "raw_payloads" {
  bucket        = local.raw_payload_bucket_name
  force_destroy = var.force_destroy_buckets
  tags          = local.common_tags
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw_payloads" {
  bucket = aws_s3_bucket.raw_payloads.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "raw_payloads" {
  bucket = aws_s3_bucket.raw_payloads.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "raw_payloads" {
  bucket = aws_s3_bucket.raw_payloads.id

  rule {
    id     = "expire-raw-payloads"
    status = "Enabled"

    filter {}

    expiration {
      days = var.raw_payload_retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = var.raw_payload_retention_days
    }
  }
}

resource "aws_s3_bucket" "dashboard" {
  bucket        = local.dashboard_bucket_name
  force_destroy = var.force_destroy_buckets
  tags          = local.common_tags
}

resource "aws_s3_bucket_server_side_encryption_configuration" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_ownership_controls" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "dashboard" {
  bucket                  = aws_s3_bucket.dashboard.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_iam_role" "lambda" {
  name = "${local.name_prefix}-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "lambda" {
  name = "${local.name_prefix}-lambda-policy"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:Scan",
          "dynamodb:UpdateItem",
        ]
        Resource = [
          aws_dynamodb_table.jobs.arn,
          aws_dynamodb_table.crawl_runs.arn,
          aws_dynamodb_table.sources.arn,
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
        ]
        Resource = "${aws_s3_bucket.raw_payloads.arn}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = "JobFinder"
          }
        }
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "collector" {
  name              = "/aws/lambda/${local.name_prefix}-collector"
  retention_in_days = var.log_retention_days
  tags              = local.common_tags
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${local.name_prefix}-api"
  retention_in_days = var.log_retention_days
  tags              = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "collector_freshness" {
  alarm_name          = "${local.name_prefix}-collector-freshness"
  alarm_description   = "No completed collector crawl has emitted in the last 24 hours."
  namespace           = "JobFinder"
  metric_name         = "CompletedCrawls"
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  period              = 86400
  statistic           = "Sum"
  treat_missing_data  = "breaching"

  dimensions = {
    Project     = var.project_name
    Environment = var.environment
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "collector_errors" {
  alarm_name          = "${local.name_prefix}-collector-errors"
  alarm_description   = "Collector emitted one or more crawl errors during the latest 24-hour window."
  namespace           = "JobFinder"
  metric_name         = "CrawlErrors"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  period              = 86400
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    Project     = var.project_name
    Environment = var.environment
  }

  tags = local.common_tags
}

resource "aws_lambda_function" "collector" {
  function_name    = "${local.name_prefix}-collector"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "job_finder.lambda_handlers.collector_handler"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  timeout          = 300
  memory_size      = 1024
  architectures    = ["arm64"]

  environment {
    variables = merge(
      local.lambda_environment,
      {
        COLLECTOR_SOURCES_JSON   = jsonencode(var.collector_sources)
        COLLECTOR_DISCOVERY_JSON = jsonencode(var.collector_discovery)
      },
    )
  }

  depends_on = [aws_cloudwatch_log_group.collector]
  tags       = local.common_tags
}

resource "aws_lambda_function" "api" {
  function_name    = "${local.name_prefix}-api"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "job_finder.lambda_handlers.api_handler"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  timeout          = 30
  memory_size      = 512
  architectures    = ["arm64"]

  environment {
    variables = local.lambda_environment
  }

  depends_on = [aws_cloudwatch_log_group.api]
  tags       = local.common_tags
}

resource "aws_apigatewayv2_api" "http" {
  name          = "${local.name_prefix}-http"
  protocol_type = "HTTP"

  cors_configuration {
    allow_headers = ["content-type"]
    allow_methods = ["GET", "PATCH", "OPTIONS"]
    allow_origins = [var.allowed_origin]
  }

  tags = local.common_tags
}

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_method     = "POST"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "health" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "GET /health"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_apigatewayv2_route" "health_freshness" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "GET /health/freshness"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_apigatewayv2_route" "jobs" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "GET /jobs"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_apigatewayv2_route" "digest" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "GET /digest"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_apigatewayv2_route" "sources" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "GET /sources"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_apigatewayv2_route" "job_update" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "PATCH /jobs/{job_id}"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_apigatewayv2_route" "options" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "OPTIONS /{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gateway.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      httpMethod     = "$context.httpMethod"
      routeKey       = "$context.routeKey"
      status         = "$context.status"
      responseLength = "$context.responseLength"
    })
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "api_gateway" {
  name              = "/aws/apigateway/${local.name_prefix}-http"
  retention_in_days = var.log_retention_days
  tags              = local.common_tags
}

resource "aws_budgets_budget" "monthly_account_budget" {
  count        = var.monthly_cost_budget_usd > 0 ? 1 : 0
  name         = "${local.name_prefix}-monthly-budget"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_cost_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = var.budget_alert_email == null || trimspace(var.budget_alert_email) == "" ? [] : [80, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [trimspace(var.budget_alert_email)]
    }
  }
}

resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}

resource "aws_iam_role" "scheduler" {
  name = "${local.name_prefix}-scheduler-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "scheduler.amazonaws.com"
        }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "scheduler" {
  name = "${local.name_prefix}-scheduler-policy"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = [aws_lambda_function.collector.arn]
      }
    ]
  })
}

resource "aws_scheduler_schedule" "collector" {
  for_each = local.schedule_map

  name                         = "${local.name_prefix}-${each.key}"
  schedule_expression          = each.value
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.collector.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({})
  }
}

resource "aws_cloudfront_origin_access_control" "dashboard" {
  name                              = "${local.name_prefix}-dashboard-oac"
  description                       = "Access control for the dashboard bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "dashboard" {
  enabled             = true
  default_root_object = "index.html"
  price_class         = "PriceClass_100"

  origin {
    domain_name              = aws_s3_bucket.dashboard.bucket_regional_domain_name
    origin_id                = "dashboard-s3-origin"
    origin_access_control_id = aws_cloudfront_origin_access_control.dashboard.id
  }

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.caching_optimized.id
    compress               = true
    target_origin_id       = "dashboard-s3-origin"
    viewer_protocol_policy = "redirect-to-https"
  }

  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = local.common_tags
}

data "aws_iam_policy_document" "dashboard_bucket" {
  statement {
    sid = "AllowCloudFrontRead"

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    actions = ["s3:GetObject"]
    resources = [
      "${aws_s3_bucket.dashboard.arn}/*",
    ]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.dashboard.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id
  policy = data.aws_iam_policy_document.dashboard_bucket.json
}

resource "aws_s3_object" "dashboard_assets" {
  for_each = local.dashboard_assets

  bucket        = aws_s3_bucket.dashboard.id
  key           = each.key
  source        = each.value.path
  content_type  = each.value.content_type
  cache_control = each.value.cache_control
  etag          = filemd5(each.value.path)
}

resource "aws_s3_object" "dashboard_config" {
  bucket        = aws_s3_bucket.dashboard.id
  key           = "config.json"
  content       = jsonencode({ apiBaseUrl = aws_apigatewayv2_api.http.api_endpoint })
  content_type  = "application/json"
  cache_control = "no-cache, no-store, must-revalidate"
}

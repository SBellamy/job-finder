output "api_base_url" {
  description = "HTTP API base URL used by the dashboard."
  value       = aws_apigatewayv2_api.http.api_endpoint
}

output "collector_lambda_name" {
  description = "Collector Lambda function name."
  value       = aws_lambda_function.collector.function_name
}

output "dashboard_bucket_name" {
  description = "Private S3 bucket holding dashboard assets."
  value       = aws_s3_bucket.dashboard.bucket
}

output "dashboard_url" {
  description = "CloudFront URL for the dashboard."
  value       = "https://${aws_cloudfront_distribution.dashboard.domain_name}"
}

output "jobs_table_name" {
  description = "DynamoDB table storing normalized job records."
  value       = aws_dynamodb_table.jobs.name
}

output "raw_payload_bucket_name" {
  description = "S3 bucket storing raw ATS payloads."
  value       = aws_s3_bucket.raw_payloads.bucket
}


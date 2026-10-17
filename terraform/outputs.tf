output "api_invoke_url" {
  value       = aws_apigatewayv2_stage.draft_analyzer_api.invoke_url
  description = "Base URL for the deployed API - same routes as api/routers/{drafts,players}.py"
}

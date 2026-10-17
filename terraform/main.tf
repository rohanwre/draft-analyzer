# Provisions the Lambda deployment of api/lambda_handler.py behind an API Gateway HTTP
# API (cheaper/simpler than a REST API - this doesn't need REST API's request
# validation/transformation features, just a passthrough to Mangum).
#
# Deploy:
#   python ../package_lambda.py        # from this dir: python ..\package_lambda.py
#   terraform init
#   terraform apply
#
# The MySQL target (var.db_host etc.) is expected to be a publicly-reachable proxy
# - Lambda has no VPC config here, so a VPC-internal-only DB target
# won't be reachable. Using a public host avoids VPC/NAT gateway cost.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "draft_analyzer_api" {
  name               = "draft-analyzer-api-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy_attachment" "lambda_basic_logs" {
  role       = aws_iam_role.draft_analyzer_api.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "draft_analyzer_api" {
  function_name = "draft-analyzer-api"
  role          = aws_iam_role.draft_analyzer_api.arn
  handler       = "api.lambda_handler.handler"
  runtime       = "python3.11"
  timeout       = 15
  memory_size   = 256

  filename         = "${path.module}/../lambda_deployment.zip"
  source_code_hash = filebase64sha256("${path.module}/../lambda_deployment.zip")

  environment {
    variables = {
      DB_HOST         = var.db_host
      DB_PORT         = var.db_port
      DB_USER         = var.db_user
      DB_PASSWORD     = var.db_password
      DB_NAME         = var.db_name
      ALLOWED_ORIGINS = var.allowed_origins
    }
  }
}

resource "aws_cloudwatch_log_group" "draft_analyzer_api" {
  name              = "/aws/lambda/${aws_lambda_function.draft_analyzer_api.function_name}"
  retention_in_days = 14
}

resource "aws_apigatewayv2_api" "draft_analyzer_api" {
  name          = "draft-analyzer-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = split(",", var.allowed_origins)
    allow_methods = ["*"]
    allow_headers = ["*"]
  }
}

resource "aws_apigatewayv2_integration" "draft_analyzer_api" {
  api_id                 = aws_apigatewayv2_api.draft_analyzer_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.draft_analyzer_api.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "draft_analyzer_api" {
  api_id    = aws_apigatewayv2_api.draft_analyzer_api.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.draft_analyzer_api.id}"
}

resource "aws_apigatewayv2_stage" "draft_analyzer_api" {
  api_id      = aws_apigatewayv2_api.draft_analyzer_api.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.draft_analyzer_api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.draft_analyzer_api.execution_arn}/*/*"
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "db_host" {
  type        = string
  description = "MySQL host reachable from Lambda (a public proxy, e.g. Railway's - no VPC config here)"
}

variable "db_port" {
  type    = string
  default = "3306"
}

variable "db_user" {
  type = string
}

variable "db_password" {
  type      = string
  sensitive = true
}

variable "db_name" {
  type = string
}

variable "allowed_origins" {
  type        = string
  description = "Comma-separated frontend origins, same value as .env's ALLOWED_ORIGINS"
  default     = "http://localhost:5173"
}

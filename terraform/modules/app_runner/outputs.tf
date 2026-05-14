output "service_arn" {
  value = aws_apprunner_service.main.arn
}

output "service_url" {
  value = aws_apprunner_service.main.service_url
}

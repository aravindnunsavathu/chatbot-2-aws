output "vpc_id" {
  value = aws_vpc.main.id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "apprunner_sg_id" {
  value = aws_security_group.apprunner.id
}

output "rds_sg_id" {
  value = aws_security_group.rds.id
}

output "site_domain" {
  description = "Public HTTPS hostname."
  value       = local.site_domain
}

output "application_public_ip" {
  description = "Elastic public IPv4 address exposed only on ports 80 and 443, plus restricted operator SSH."
  value       = tencentcloud_eip.application.public_ip
}

output "application_private_ip" {
  description = "CVM private IPv4 address."
  value       = tencentcloud_instance.application.private_ip
}

output "postgresql_private_endpoint" {
  description = "Private TLS PostgreSQL endpoint; no credentials are included."
  value       = "${tencentcloud_postgresql_instance.production.private_access_ip}:${tencentcloud_postgresql_instance.production.private_access_port}"
}

output "tcr_private_endpoint" {
  description = "Private TCR endpoint for production image pulls."
  value       = tencentcloud_tcr_instance.production.internal_end_point
}

output "tcr_repository" {
  description = "TCR repository path without tag or digest."
  value       = "${tencentcloud_tcr_instance.production.internal_end_point}/${tencentcloud_tcr_namespace.application.name}/${tencentcloud_tcr_repository.application.name}"
}

output "post_apply_checks" {
  description = "Required checks before deploying application secrets or opening traffic."
  value = [
    "Wait for cloud-init status --wait to succeed on the CVM.",
    "Confirm /srv/infinite-canvas is mounted from the encrypted CBS disk.",
    "Confirm TencentDB PostgreSQL reports major version 17, private networking, SSL, and the expected connection limit.",
    "Confirm the TCR VPC attachment is active and public access is disabled.",
    "Do not enable Caddy until the signed image, migrations, and http://127.0.0.1:8000/readyz pass.",
  ]
}

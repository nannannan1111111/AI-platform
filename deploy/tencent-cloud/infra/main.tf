locals {
  name_prefix = "${var.project_name}-${var.environment}"
  site_domain = var.subdomain == "@" ? var.domain : "${var.subdomain}.${var.domain}"
  common_tags = merge(
    {
      application = var.project_name
      environment = var.environment
      managed-by  = "opentofu"
    },
    var.tags,
  )
}

data "tencentcloud_images" "ubuntu" {
  image_type       = ["PUBLIC_IMAGE"]
  image_name_regex = var.image_name_regex
}

data "tencentcloud_postgresql_db_versions" "required" {
  db_major_version = var.postgresql_major_version
}

resource "tencentcloud_vpc" "production" {
  name         = "${local.name_prefix}-vpc"
  cidr_block   = var.vpc_cidr
  dns_servers  = ["119.29.29.29"]
  is_multicast = false
  tags         = local.common_tags
}

resource "tencentcloud_subnet" "application" {
  name              = "${local.name_prefix}-application"
  vpc_id            = tencentcloud_vpc.production.id
  cidr_block        = var.application_subnet_cidr
  availability_zone = var.availability_zone
  is_multicast      = false
  tags              = local.common_tags
}

resource "tencentcloud_security_group" "application" {
  name        = "${local.name_prefix}-application"
  description = "Public HTTPS edge and restricted operator access"
  tags        = local.common_tags
}

resource "tencentcloud_security_group_rule_set" "application" {
  security_group_id = tencentcloud_security_group.application.id

  ingress {
    action      = "ACCEPT"
    cidr_block  = "0.0.0.0/0"
    protocol    = "TCP"
    port        = "80"
    description = "ACME challenge and HTTPS redirect"
  }

  ingress {
    action      = "ACCEPT"
    cidr_block  = "0.0.0.0/0"
    protocol    = "TCP"
    port        = "443"
    description = "Public HTTPS"
  }

  ingress {
    action      = "ACCEPT"
    cidr_block  = var.operator_cidr
    protocol    = "TCP"
    port        = "22"
    description = "Restricted operator SSH"
  }

  egress {
    action      = "ACCEPT"
    cidr_block  = "0.0.0.0/0"
    protocol    = "ALL"
    port        = "ALL"
    description = "System updates, providers, SMTP, registry, and APIs"
  }
}

resource "tencentcloud_security_group" "database" {
  name        = "${local.name_prefix}-database"
  description = "PostgreSQL private access from application hosts only"
  tags        = local.common_tags
}

resource "tencentcloud_security_group_rule_set" "database" {
  security_group_id = tencentcloud_security_group.database.id

  ingress {
    action             = "ACCEPT"
    source_security_id = tencentcloud_security_group.application.id
    protocol           = "TCP"
    port               = "5432"
    description        = "PostgreSQL from the application security group"
  }

  egress {
    action      = "ACCEPT"
    cidr_block  = var.vpc_cidr
    protocol    = "ALL"
    port        = "ALL"
    description = "Private VPC responses"
  }
}

resource "tencentcloud_cbs_storage" "media" {
  storage_name      = "${local.name_prefix}-media"
  storage_type      = "CLOUD_SSD"
  storage_size      = var.media_disk_size_gib
  availability_zone = var.availability_zone
  charge_type       = "POSTPAID_BY_HOUR"
  encrypt           = true
  tags              = local.common_tags
}

resource "tencentcloud_instance" "application" {
  instance_name        = "${local.name_prefix}-app"
  hostname             = replace(local.name_prefix, "_", "-")
  availability_zone    = var.availability_zone
  image_id             = data.tencentcloud_images.ubuntu.images[0].image_id
  instance_type        = var.instance_type
  instance_charge_type = "POSTPAID_BY_HOUR"
  system_disk_type     = "CLOUD_PREMIUM"
  system_disk_size     = var.system_disk_size_gib
  vpc_id               = tencentcloud_vpc.production.id
  subnet_id            = tencentcloud_subnet.application.id
  orderly_security_groups = [
    tencentcloud_security_group.application.id,
  ]
  key_name                    = var.ssh_key_name
  allocate_public_ip          = false
  disable_security_service    = false
  disable_monitor_service     = false
  user_data_replace_on_change = false
  user_data_raw = templatefile("${path.module}/../cloud-init.yaml.tftpl", {
    media_disk_device = var.media_disk_device
  })
  tags = local.common_tags

  lifecycle {
    precondition {
      condition     = length(data.tencentcloud_images.ubuntu.images) > 0
      error_message = "No CVM image matched image_name_regex in the selected region."
    }
  }
}

resource "tencentcloud_cbs_storage_attachment" "media" {
  storage_id  = tencentcloud_cbs_storage.media.id
  instance_id = tencentcloud_instance.application.id
}

resource "tencentcloud_eip" "application" {
  name                       = "${local.name_prefix}-eip"
  type                       = "EIP"
  internet_charge_type       = "TRAFFIC_POSTPAID_BY_HOUR"
  internet_max_bandwidth_out = var.eip_bandwidth_mbps
  tags                       = local.common_tags
}

resource "tencentcloud_eip_association" "application" {
  eip_id      = tencentcloud_eip.application.id
  instance_id = tencentcloud_instance.application.id
}

resource "tencentcloud_postgresql_instance" "production" {
  name              = "${local.name_prefix}-postgresql"
  availability_zone = var.availability_zone
  charge_type       = "POSTPAID_BY_HOUR"
  vpc_id            = tencentcloud_vpc.production.id
  subnet_id         = tencentcloud_subnet.application.id
  security_groups   = [tencentcloud_security_group.database.id]
  db_major_version  = var.postgresql_major_version
  root_user         = var.postgresql_root_user
  root_password     = var.postgresql_root_password
  charset           = "UTF8"
  cpu               = var.postgresql_cpu
  memory            = var.postgresql_memory_gib
  storage           = var.postgresql_storage_gib
  storage_type      = "CLOUD_SSD"
  delete_protection = var.postgresql_delete_protection
  tags              = local.common_tags

  dynamic "db_node_set" {
    for_each = var.standby_availability_zone == null ? [] : [
      { role = "Primary", zone = var.availability_zone },
      { role = null, zone = var.standby_availability_zone },
    ]
    content {
      role = db_node_set.value.role
      zone = db_node_set.value.zone
    }
  }

  timeouts {
    create = "60m"
    update = "60m"
  }

  lifecycle {
    precondition {
      condition     = length(data.tencentcloud_postgresql_db_versions.required.version_set) > 0
      error_message = "TencentDB PostgreSQL 17 is not available in the selected region; do not silently downgrade the production database."
    }
  }
}

resource "tencentcloud_postgresql_instance_ssl_config" "production" {
  db_instance_id  = tencentcloud_postgresql_instance.production.id
  ssl_enabled     = true
  connect_address = tencentcloud_postgresql_instance.production.private_access_ip
}

resource "tencentcloud_tcr_instance" "production" {
  name                  = replace(local.name_prefix, "-", "")
  instance_type         = var.tcr_instance_type
  registry_charge_type  = 1
  open_public_operation = false
  delete_bucket         = false
  tags                  = local.common_tags
}

resource "tencentcloud_tcr_namespace" "application" {
  instance_id    = tencentcloud_tcr_instance.production.id
  name           = replace(var.project_name, "-", "_")
  is_public      = false
  is_auto_scan   = true
  is_prevent_vul = true
  severity       = "high"
  tags           = local.common_tags
}

resource "tencentcloud_tcr_repository" "application" {
  instance_id    = tencentcloud_tcr_instance.production.id
  namespace_name = tencentcloud_tcr_namespace.application.name
  name           = "application"
  brief_desc     = "Infinite Canvas production application"
  description    = "Immutable, scanned, signed production images"
  force_delete   = false
}

resource "tencentcloud_tcr_vpc_attachment" "application" {
  instance_id = tencentcloud_tcr_instance.production.id
  vpc_id      = tencentcloud_vpc.production.id
  subnet_id   = tencentcloud_subnet.application.id
}

resource "tencentcloud_dnspod_record" "application" {
  domain      = var.domain
  sub_domain  = var.subdomain
  record_type = "A"
  record_line = "默认"
  value       = tencentcloud_eip.application.public_ip
  ttl         = 600
}

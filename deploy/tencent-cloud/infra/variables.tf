variable "project_name" {
  description = "Lowercase deployment identifier used in Tencent Cloud resource names."
  type        = string
  default     = "infinite-canvas"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,29}$", var.project_name))
    error_message = "project_name must be 3-30 lowercase letters, numbers, or hyphens and start with a letter."
  }
}

variable "environment" {
  description = "Deployment environment name."
  type        = string
  default     = "production"

  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be staging or production."
  }
}

variable "region" {
  description = "Tencent Cloud region, selected for compliance and user latency."
  type        = string
}

variable "availability_zone" {
  description = "Primary availability zone for CVM, CBS, and TencentDB."
  type        = string
}

variable "standby_availability_zone" {
  description = "Optional distinct TencentDB standby zone. Leave null only for an explicitly accepted single-zone launch."
  type        = string
  default     = null

  validation {
    condition     = var.standby_availability_zone == null || var.standby_availability_zone != var.availability_zone
    error_message = "standby_availability_zone must differ from availability_zone."
  }
}

variable "domain" {
  description = "DNSPod-managed apex domain, without a scheme or trailing dot."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$", var.domain))
    error_message = "domain must be a lowercase DNS name such as example.com."
  }
}

variable "subdomain" {
  description = "DNS label for the public application endpoint. Use @ for the apex."
  type        = string
  default     = "studio"

  validation {
    condition     = var.subdomain == "@" || can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", var.subdomain))
    error_message = "subdomain must be @ or one lowercase DNS label."
  }
}

variable "operator_cidr" {
  description = "Single trusted operator IPv4 CIDR allowed to SSH. Never use a public-wide CIDR."
  type        = string

  validation {
    condition     = can(cidrhost(var.operator_cidr, 0)) && can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}/(2[4-9]|3[0-2])$", var.operator_cidr))
    error_message = "operator_cidr must be a valid restricted IPv4 /24 through /32 CIDR."
  }
}

variable "ssh_key_name" {
  description = "Name of an existing Tencent Cloud SSH key pair. Password login is not configured."
  type        = string
}

variable "vpc_cidr" {
  description = "Private VPC CIDR."
  type        = string
  default     = "10.42.0.0/16"
}

variable "application_subnet_cidr" {
  description = "Private application and database subnet CIDR."
  type        = string
  default     = "10.42.10.0/24"
}

variable "instance_type" {
  description = "CVM type selected after checking current stock in the target zone; start with at least 2 vCPU and 4 GiB RAM."
  type        = string
}

variable "image_name_regex" {
  description = "Public CVM image lookup expression."
  type        = string
  default     = "Ubuntu Server 24.04 LTS"
}

variable "system_disk_size_gib" {
  description = "CVM system disk size in GiB."
  type        = number
  default     = 50

  validation {
    condition     = var.system_disk_size_gib >= 50
    error_message = "system_disk_size_gib must be at least 50 GiB."
  }
}

variable "media_disk_size_gib" {
  description = "Encrypted CBS disk size for media and provider secret directories."
  type        = number
  default     = 100

  validation {
    condition     = var.media_disk_size_gib >= 100
    error_message = "media_disk_size_gib must be at least 100 GiB."
  }
}

variable "media_disk_device" {
  description = "Linux block device used by the first-boot mount service."
  type        = string
  default     = "/dev/vdb"

  validation {
    condition     = startswith(var.media_disk_device, "/dev/")
    error_message = "media_disk_device must be an absolute /dev path."
  }
}

variable "eip_bandwidth_mbps" {
  description = "Public EIP outbound bandwidth cap in Mbps."
  type        = number
  default     = 10

  validation {
    condition     = var.eip_bandwidth_mbps >= 5 && var.eip_bandwidth_mbps <= 100
    error_message = "eip_bandwidth_mbps must be between 5 and 100."
  }
}

variable "postgresql_major_version" {
  description = "Required TencentDB PostgreSQL major version. Availability must be confirmed in the selected region before apply."
  type        = string
  default     = "17"

  validation {
    condition     = var.postgresql_major_version == "17"
    error_message = "This application production baseline requires PostgreSQL 17."
  }
}

variable "postgresql_root_user" {
  description = "TencentDB administrator username."
  type        = string
  default     = "canvas_admin"
}

variable "postgresql_root_password" {
  description = "TencentDB bootstrap administrator password. Supply through TF_VAR_postgresql_root_password; the encrypted remote state is a secret boundary."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.postgresql_root_password) >= 16
    error_message = "postgresql_root_password must contain at least 16 characters."
  }
}

variable "postgresql_cpu" {
  description = "TencentDB vCPU count. Confirm that the CPU/memory pair exists in the target zone."
  type        = number
  default     = 2
}

variable "postgresql_memory_gib" {
  description = "TencentDB memory in GiB."
  type        = number
  default     = 4
}

variable "postgresql_storage_gib" {
  description = "TencentDB storage in GiB, in 10 GiB increments."
  type        = number
  default     = 100

  validation {
    condition     = var.postgresql_storage_gib >= 100 && var.postgresql_storage_gib % 10 == 0
    error_message = "postgresql_storage_gib must be at least 100 GiB and a multiple of 10."
  }
}

variable "postgresql_delete_protection" {
  description = "Protect the managed database against accidental Terraform deletion."
  type        = bool
  default     = true
}

variable "tcr_instance_type" {
  description = "Tencent Container Registry instance tier."
  type        = string
  default     = "basic"

  validation {
    condition     = contains(["basic", "standard", "premium"], var.tcr_instance_type)
    error_message = "tcr_instance_type must be basic, standard, or premium."
  }
}

variable "tags" {
  description = "Additional non-sensitive Tencent Cloud resource tags."
  type        = map(string)
  default     = {}
}

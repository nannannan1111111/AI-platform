terraform {
  required_version = ">= 1.12.5, < 2.0.0"

  required_providers {
    tencentcloud = {
      source  = "tencentcloudstack/tencentcloud"
      version = "= 1.83.23"
    }
  }

  backend "s3" {}
}

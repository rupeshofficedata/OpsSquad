# Real terraform.plan/terraform.apply target for the terraform-plan and
# terraform-apply agents. Uses only the local-only `local` provider — no
# cloud credentials needed — so it's safe to actually apply in this scaffold.
# A real deployment would point TERRAFORM_DIR at an actual cloud-backed
# module instead.

terraform {
  required_providers {
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
  }
}

variable "instance_count" {
  type    = number
  default = 2
}

resource "local_file" "instance" {
  count    = var.instance_count
  filename = "${path.module}/state/instance-${count.index}.txt"
  content  = "opssquad-demo-instance-${count.index}\n"
}

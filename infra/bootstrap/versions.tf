terraform {
  required_version = ">= 1.9"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
  # Local state on purpose: this root creates the bucket the other root stores state in.
}

provider "google" {
  project = var.project_id
  region  = var.region
}

terraform {
  required_version = ">= 1.9"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
  backend "gcs" {
    # bucket is supplied at init time: make init  (-backend-config="bucket=<project>-tfstate")
    prefix = "stage1"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

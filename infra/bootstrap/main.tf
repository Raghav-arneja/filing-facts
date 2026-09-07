locals {
  state_bucket = coalesce(var.state_bucket_name, "${var.project_id}-tfstate")

  # Every API Stage 1 touches. All are free to enable; each is priced on use only.
  services = [
    "serviceusage.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "storage.googleapis.com",
    "bigquery.googleapis.com",
    "artifactregistry.googleapis.com",
    "run.googleapis.com",
    "cloudscheduler.googleapis.com",
    "sts.googleapis.com", # token exchange for Workload Identity Federation (CI)
  ]
}

resource "google_project_service" "enabled" {
  for_each           = toset(local.services)
  service            = each.value
  disable_on_destroy = false # disabling APIs on destroy can delete unrelated resources
}

resource "google_storage_bucket" "tfstate" {
  name                        = local.state_bucket
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      num_newer_versions = 10
    }
    action {
      type = "Delete"
    }
  }

  labels = { app = "filing-facts", stage = "bootstrap" }

  depends_on = [google_project_service.enabled]
}

resource "google_artifact_registry_repository" "images" {
  repository_id = var.artifact_repository
  location      = var.region
  format        = "DOCKER"
  description   = "Filing Facts container images"

  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "keep-3-newest"
    action = "KEEP"
    most_recent_versions {
      keep_count = 3
    }
  }
  cleanup_policies {
    id     = "delete-untagged-older-than-7d"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "604800s"
    }
  }

  labels = { app = "filing-facts", stage = "bootstrap" }

  depends_on = [google_project_service.enabled]
}

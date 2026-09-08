locals {
  raw_bucket = coalesce(var.raw_bucket_name, "${var.project_id}-raw")
  labels     = { app = "filing-facts", stage = "stage1" }
}

# ---- Storage ---------------------------------------------------------------

resource "google_storage_bucket" "raw" {
  name                        = local.raw_bucket
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true # stage-end check: destroy must leave nothing behind
  labels                      = local.labels

  versioning {
    enabled = false # create-only writes in the job make versioning redundant
  }

  # Storage is the only cost line that grows (docs/cost.md). Parsed ZIPs are still wanted
  # for a reparse after a parser change, so they are demoted rather than deleted.
  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }
  lifecycle_rule {
    condition {
      age = 180
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_bigquery_dataset" "raw" {
  dataset_id                 = var.dataset_id
  location                   = var.location
  description                = "Raw ingestion ledger and, from Stage 2, raw filing facts."
  delete_contents_on_destroy = true
  labels                     = local.labels
}

resource "google_bigquery_table" "ingest_runs" {
  dataset_id          = google_bigquery_dataset.raw.dataset_id
  table_id            = "ingest_runs"
  description         = "One row per ingest job execution. At most one 'succeeded' row per source_key."
  deletion_protection = false
  labels              = local.labels

  time_partitioning {
    type  = "DAY"
    field = "finished_at"
  }
  clustering = ["source_key", "status"]

  schema = file("${path.module}/schemas/ingest_runs.json")
}

# ---- Identity: the job ----------------------------------------------------

resource "google_service_account" "ingest" {
  account_id   = "ingest-job"
  display_name = "Filing Facts ingest Cloud Run Job"
}

resource "google_storage_bucket_iam_member" "ingest_raw_create" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectCreator"
  member = google_service_account.ingest.member
}

resource "google_storage_bucket_iam_member" "ingest_raw_view" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectViewer"
  member = google_service_account.ingest.member
}

resource "google_bigquery_dataset_iam_member" "ingest_data_editor" {
  dataset_id = google_bigquery_dataset.raw.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_service_account.ingest.member
}

# The only project-level grant. Load and query jobs are project-scoped in BigQuery.
resource "google_project_iam_member" "ingest_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = google_service_account.ingest.member
}

# ---- Compute --------------------------------------------------------------

resource "google_cloud_run_v2_job" "ingest" {
  name                = "ingest"
  location            = var.region
  deletion_protection = false
  labels              = local.labels

  template {
    task_count = 1
    template {
      service_account = google_service_account.ingest.email
      max_retries     = 1
      timeout         = var.job_timeout

      containers {
        image = var.image
        resources {
          limits = { cpu = "1", memory = var.job_memory }
        }
        env {
          name  = "FF_GCP_PROJECT"
          value = var.project_id
        }
        env {
          name  = "FF_RAW_BUCKET"
          value = google_storage_bucket.raw.name
        }
        env {
          name  = "FF_BQ_DATASET"
          value = google_bigquery_dataset.raw.dataset_id
        }
        env {
          name  = "FF_BQ_TABLE"
          value = google_bigquery_table.ingest_runs.table_id
        }
        env {
          name  = "FF_BQ_LOCATION"
          value = var.location
        }
        env {
          name  = "FF_MAX_BYTES"
          value = tostring(var.max_bytes)
        }
        env {
          name  = "FF_LIFECYCLE_TOPIC"
          value = google_pubsub_topic.lifecycle.name
        }
        env {
          name  = "FF_DOCUMENTS_TOPIC"
          value = google_pubsub_topic.documents.name
        }
      }
    }
  }

  depends_on = [
    google_storage_bucket_iam_member.ingest_raw_create,
    google_bigquery_dataset_iam_member.ingest_data_editor,
    google_project_iam_member.ingest_job_user,
  ]
}

# ---- Identity: the scheduler ----------------------------------------------

resource "google_service_account" "scheduler" {
  account_id   = "ingest-scheduler"
  display_name = "Cloud Scheduler invoker for the ingest job"
}

resource "google_cloud_run_v2_job_iam_member" "scheduler_invoker" {
  name     = google_cloud_run_v2_job.ingest.name
  location = google_cloud_run_v2_job.ingest.location
  role     = "roles/run.invoker"
  member   = google_service_account.scheduler.member
}

resource "google_cloud_scheduler_job" "daily" {
  name        = "ingest-daily"
  description = "Fetch today's Companies House daily accounts ZIP."
  region      = var.region
  schedule    = var.schedule
  time_zone   = "Europe/London"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.ingest.name}:run"
    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }

  depends_on = [google_cloud_run_v2_job_iam_member.scheduler_invoker]
}

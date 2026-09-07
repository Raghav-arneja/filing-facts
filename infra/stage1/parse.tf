# ---- Stage 2: parse ---------------------------------------------------------
# Same image and service account as ingest; a different command and its own schedule.

resource "google_bigquery_table" "documents" {
  dataset_id          = google_bigquery_dataset.raw.dataset_id
  table_id            = "documents"
  description         = "One row per parsed filing with its plain text."
  deletion_protection = false
  labels              = local.labels
  time_partitioning {
    type  = "DAY"
    field = "parsed_at"
  }
  clustering = ["source_key", "document_id"]
  schema     = file("${path.module}/schemas/documents.json")
}

resource "google_bigquery_table" "facts" {
  dataset_id          = google_bigquery_dataset.raw.dataset_id
  table_id            = "facts"
  description         = "One row per tagged XBRL value. The ground truth for evaluation."
  deletion_protection = false
  labels              = local.labels
  clustering          = ["document_id", "concept"]
  schema              = file("${path.module}/schemas/facts.json")
}

resource "google_bigquery_table" "quarantine" {
  dataset_id          = google_bigquery_dataset.raw.dataset_id
  table_id            = "quarantine"
  description         = "Members that could not be processed, with the reason. Never silently dropped."
  deletion_protection = false
  labels              = local.labels
  time_partitioning {
    type  = "DAY"
    field = "quarantined_at"
  }
  clustering = ["source_key", "stage"]
  schema     = file("${path.module}/schemas/quarantine.json")
}

resource "google_bigquery_table" "parse_runs" {
  dataset_id          = google_bigquery_dataset.raw.dataset_id
  table_id            = "parse_runs"
  description         = "Parse job ledger. A started row pins a batch; one succeeded row per batch."
  deletion_protection = false
  labels              = local.labels
  time_partitioning {
    type  = "DAY"
    field = "started_at"
  }
  clustering = ["source_key", "status"]
  schema     = file("${path.module}/schemas/parse_runs.json")
}

resource "google_cloud_run_v2_job" "parse" {
  name                = "parse"
  location            = var.region
  deletion_protection = false
  labels              = local.labels

  template {
    task_count = 1
    template {
      service_account = google_service_account.ingest.email
      max_retries     = 1
      timeout         = var.parse_job_timeout

      containers {
        image   = var.image
        command = ["python", "-m", "filing_facts.parse"]
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
          name  = "FF_BQ_LOCATION"
          value = var.location
        }
        env {
          name  = "FF_PARSE_CAP"
          value = tostring(var.parse_cap)
        }
      }
    }
  }

  depends_on = [
    google_bigquery_table.documents,
    google_bigquery_table.facts,
    google_bigquery_table.quarantine,
    google_bigquery_table.parse_runs,
    google_project_iam_member.ingest_job_user,
  ]
}

resource "google_cloud_run_v2_job_iam_member" "parse_scheduler_invoker" {
  name     = google_cloud_run_v2_job.parse.name
  location = google_cloud_run_v2_job.parse.location
  role     = "roles/run.invoker"
  member   = google_service_account.scheduler.member
}

resource "google_cloud_scheduler_job" "parse_daily" {
  name        = "parse-daily"
  description = "Parse every ingested ZIP that is pending at the current cap."
  region      = var.region
  schedule    = var.parse_schedule
  time_zone   = "Europe/London"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.parse.name}:run"
    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }

  depends_on = [google_cloud_run_v2_job_iam_member.parse_scheduler_invoker]
}

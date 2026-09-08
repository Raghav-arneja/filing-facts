# ---- Stage 3: extract -------------------------------------------------------
# Same image and service account as the other jobs; the job calls Gemini on Vertex AI.
# No schedule: extraction runs are triggered by Airflow or by hand, because every run
# spends money and the cap and model are deliberate choices.

resource "google_bigquery_table" "extractions" {
  dataset_id          = google_bigquery_dataset.raw.dataset_id
  table_id            = "extractions"
  description         = "One model answer per document, model and prompt. Cost measured per row."
  deletion_protection = false
  labels              = local.labels
  time_partitioning {
    type  = "DAY"
    field = "extracted_at"
  }
  clustering = ["model", "prompt_id", "document_id"]
  schema     = file("${path.module}/schemas/extractions.json")
}

resource "google_bigquery_table" "extract_runs" {
  dataset_id          = google_bigquery_dataset.raw.dataset_id
  table_id            = "extract_runs"
  description         = "Extract job ledger. A started row pins a batch; one succeeded row per batch."
  deletion_protection = false
  labels              = local.labels
  time_partitioning {
    type  = "DAY"
    field = "started_at"
  }
  clustering = ["model", "prompt_id", "status"]
  schema     = file("${path.module}/schemas/extract_runs.json")
}

# The job calls Vertex AI as its own identity.
resource "google_project_iam_member" "ingest_vertex_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = google_service_account.ingest.member
}

resource "google_cloud_run_v2_job" "extract" {
  name                = "extract"
  location            = var.region
  deletion_protection = false
  labels              = local.labels

  template {
    task_count = 1
    template {
      service_account = google_service_account.ingest.email
      max_retries     = 0 # a retry would resume the pinned batch, but spending is a human call
      timeout         = var.extract_job_timeout

      containers {
        image   = var.image
        command = ["python", "-m", "filing_facts.extract"]
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
          name  = "FF_LIFECYCLE_TOPIC"
          value = google_pubsub_topic.lifecycle.name
        }
        env {
          name  = "FF_DOCUMENTS_TOPIC"
          value = google_pubsub_topic.documents.name
        }
        env {
          name  = "FF_EXTRACT_MODEL"
          value = var.extract_model
        }
        env {
          name  = "FF_PROMPT_VERSION"
          value = var.prompt_version
        }
        env {
          name  = "FF_EXTRACT_CAP"
          value = tostring(var.extract_cap)
        }
        env {
          name  = "FF_VERTEX_LOCATION"
          value = var.vertex_location
        }
      }
    }
  }

  depends_on = [
    google_bigquery_table.extractions,
    google_bigquery_table.extract_runs,
    google_project_iam_member.ingest_vertex_user,
  ]
}

# ---- Stage 6: index -----------------------------------------------------------
# Chunks and embeddings for retrieval, searched with BigQuery VECTOR_SEARCH. No vector index
# resource: brute-force search is sub-second at this corpus size and a Vertex vector index
# would bill by the hour whether or not anyone queried it.

resource "google_bigquery_table" "chunks" {
  dataset_id          = google_bigquery_dataset.raw.dataset_id
  table_id            = "chunks"
  description         = "Filing text split into passages with their embeddings."
  deletion_protection = false
  labels              = local.labels
  clustering          = ["embedding_model", "document_id"]
  schema              = file("${path.module}/schemas/chunks.json")
}

resource "google_bigquery_table" "index_runs" {
  dataset_id          = google_bigquery_dataset.raw.dataset_id
  table_id            = "index_runs"
  description         = "Index job ledger. A started row pins a batch; one succeeded row per batch."
  deletion_protection = false
  labels              = local.labels
  time_partitioning {
    type  = "DAY"
    field = "started_at"
  }
  schema = file("${path.module}/schemas/index_runs.json")
}

resource "google_cloud_run_v2_job" "index" {
  name                = "index"
  location            = var.region
  deletion_protection = false
  labels              = local.labels

  template {
    task_count = 1
    template {
      service_account = google_service_account.ingest.email
      max_retries     = 0
      timeout         = "3600s"

      containers {
        image   = var.image
        command = ["python", "-m", "filing_facts.index"]
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
          name  = "FF_VERTEX_LOCATION"
          value = var.vertex_location
        }
        env {
          name  = "FF_INDEX_CAP"
          value = tostring(var.index_cap)
        }
      }
    }
  }

  depends_on = [google_bigquery_table.chunks, google_bigquery_table.index_runs]
}

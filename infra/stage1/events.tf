# ---- Stage 5: events -------------------------------------------------------
# Two Pub/Sub channels at different granularity, per the architecture. Lifecycle events are
# pushed to the dispatcher service, which starts the next job; malformed messages go to a
# dead-letter topic after the retry limit. Per-document events are written straight to
# BigQuery by a subscription, no code consumer, for visibility of flow and queue age.

resource "google_pubsub_topic" "lifecycle" {
  name                       = "filing-facts-lifecycle"
  labels                     = local.labels
  message_retention_duration = "604800s" # 7 days: enough to replay a bad week
}

resource "google_pubsub_topic" "lifecycle_dead_letter" {
  name                       = "filing-facts-lifecycle-dead-letter"
  labels                     = local.labels
  message_retention_duration = "2678400s" # 31 days: a dead letter is a bug to look at
}

resource "google_pubsub_topic" "documents" {
  name   = "filing-facts-documents"
  labels = local.labels
}

# ---- dispatcher: the lifecycle consumer -------------------------------------

resource "google_service_account" "dispatcher" {
  account_id   = "dispatcher"
  display_name = "Filing Facts dispatcher service: starts jobs from lifecycle events"
}

# The dispatcher's only power is to start the pipeline's own jobs.
resource "google_cloud_run_v2_job_iam_member" "dispatcher_runs_parse" {
  name     = google_cloud_run_v2_job.parse.name
  location = var.region
  role     = "roles/run.developer"
  member   = google_service_account.dispatcher.member
}

resource "google_cloud_run_v2_job_iam_member" "dispatcher_runs_extract" {
  name     = google_cloud_run_v2_job.extract.name
  location = var.region
  role     = "roles/run.developer"
  member   = google_service_account.dispatcher.member
}

resource "google_cloud_run_v2_service" "dispatcher" {
  name                = "dispatcher"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL" # Pub/Sub push arrives from Google's edge; IAM guards it
  labels              = local.labels

  template {
    service_account = google_service_account.dispatcher.email
    scaling {
      min_instance_count = 0 # scales to zero: no standing cost
      max_instance_count = 2
    }
    containers {
      image   = var.image
      command = ["uvicorn", "filing_facts.dispatcher.app:app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
      resources {
        limits   = { cpu = "1", memory = "512Mi" }
        cpu_idle = true
      }
      env {
        name  = "FF_GCP_PROJECT"
        value = var.project_id
      }
      env {
        name  = "FF_REGION"
        value = var.region
      }
      env {
        name  = "FF_EXTRACT_ON_EVENT"
        value = tostring(var.extract_on_event)
      }
      env {
        name  = "FF_EXTRACT_CAP"
        value = tostring(var.extract_cap)
      }
      startup_probe {
        http_get { path = "/healthz" }
      }
    }
  }
}

# Pub/Sub pushes as this identity; it is the only principal allowed to invoke the service.
resource "google_service_account" "pubsub_push" {
  account_id   = "pubsub-push"
  display_name = "Pub/Sub push identity for the dispatcher"
}

resource "google_cloud_run_v2_service_iam_member" "push_invokes_dispatcher" {
  name     = google_cloud_run_v2_service.dispatcher.name
  location = var.region
  role     = "roles/run.invoker"
  member   = google_service_account.pubsub_push.member
}

locals {
  pubsub_agent = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

# The Pub/Sub service agent needs to mint OIDC tokens for the push identity, publish to the
# dead-letter topic, and ack on the subscription when it forwards a dead letter.
resource "google_service_account_iam_member" "pubsub_agent_mints_push_tokens" {
  service_account_id = google_service_account.pubsub_push.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = local.pubsub_agent
}

resource "google_pubsub_topic_iam_member" "pubsub_agent_publishes_dead_letters" {
  topic  = google_pubsub_topic.lifecycle_dead_letter.name
  role   = "roles/pubsub.publisher"
  member = local.pubsub_agent
}

resource "google_pubsub_subscription" "lifecycle_push" {
  name   = "filing-facts-lifecycle-dispatcher"
  topic  = google_pubsub_topic.lifecycle.name
  labels = local.labels

  ack_deadline_seconds       = 60
  message_retention_duration = "604800s"
  retain_acked_messages      = false

  push_config {
    push_endpoint = "${google_cloud_run_v2_service.dispatcher.uri}/pubsub"
    oidc_token {
      service_account_email = google_service_account.pubsub_push.email
    }
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.lifecycle_dead_letter.id
    max_delivery_attempts = 5
  }

  depends_on = [
    google_cloud_run_v2_service_iam_member.push_invokes_dispatcher,
    google_service_account_iam_member.pubsub_agent_mints_push_tokens,
    google_pubsub_topic_iam_member.pubsub_agent_publishes_dead_letters,
  ]
}

resource "google_pubsub_subscription_iam_member" "pubsub_agent_acks_lifecycle" {
  subscription = google_pubsub_subscription.lifecycle_push.name
  role         = "roles/pubsub.subscriber"
  member       = local.pubsub_agent
}

# A pull subscription on the dead-letter topic so a human, or the backfill DAG, can read
# and replay what could not be handled.
resource "google_pubsub_subscription" "lifecycle_dead_letter_pull" {
  name                       = "filing-facts-lifecycle-dead-letter-pull"
  topic                      = google_pubsub_topic.lifecycle_dead_letter.name
  labels                     = local.labels
  message_retention_duration = "2678400s"
  ack_deadline_seconds       = 60
}

# ---- per-document channel straight into BigQuery ----------------------------

resource "google_bigquery_dataset" "events" {
  dataset_id                 = "filing_facts_events"
  location                   = var.location
  description                = "Per-document events written by a Pub/Sub BigQuery subscription."
  delete_contents_on_destroy = true
  labels                     = local.labels
}

resource "google_bigquery_table" "document_events" {
  dataset_id          = google_bigquery_dataset.events.dataset_id
  table_id            = "document_events"
  deletion_protection = false
  labels              = local.labels
  time_partitioning {
    type  = "DAY"
    field = "publish_time"
  }
  schema = file("${path.module}/schemas/document_events.json")
}

resource "google_bigquery_dataset_iam_member" "pubsub_agent_writes_events" {
  dataset_id = google_bigquery_dataset.events.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = local.pubsub_agent
}

resource "google_pubsub_subscription" "documents_to_bigquery" {
  name   = "filing-facts-documents-bigquery"
  topic  = google_pubsub_topic.documents.name
  labels = local.labels

  bigquery_config {
    table            = "${var.project_id}.${google_bigquery_dataset.events.dataset_id}.${google_bigquery_table.document_events.table_id}"
    use_topic_schema = false
    write_metadata   = true # message_id, publish_time, attributes columns
  }

  depends_on = [google_bigquery_dataset_iam_member.pubsub_agent_writes_events]
}

# ---- jobs publish -----------------------------------------------------------

resource "google_pubsub_topic_iam_member" "jobs_publish_lifecycle" {
  topic  = google_pubsub_topic.lifecycle.name
  role   = "roles/pubsub.publisher"
  member = google_service_account.ingest.member
}

resource "google_pubsub_topic_iam_member" "jobs_publish_documents" {
  topic  = google_pubsub_topic.documents.name
  role   = "roles/pubsub.publisher"
  member = google_service_account.ingest.member
}

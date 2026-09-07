# ---- CI identity ------------------------------------------------------------
# GitHub Actions authenticates through Workload Identity Federation: a short-lived token
# from GitHub's OIDC issuer is exchanged for this service account. No key exists anywhere.
# The trust is scoped to one repository, and the account can only read raw data and write
# into a CI-only dataset, so a compromised workflow cannot touch production tables.

data "google_project" "this" {
  project_id = var.project_id
}

resource "google_bigquery_dataset" "ci" {
  dataset_id                  = var.ci_dataset_id
  location                    = var.location
  description                 = "dbt build target for CI. Rebuilt on every pull request; nothing depends on it."
  delete_contents_on_destroy  = true
  default_table_expiration_ms = 7 * 24 * 60 * 60 * 1000
  labels                      = local.labels
}

resource "google_service_account" "ci" {
  account_id   = "ci-dbt"
  display_name = "GitHub Actions: dbt build against BigQuery"
}

resource "google_project_iam_member" "ci_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = google_service_account.ci.member
}

resource "google_bigquery_dataset_iam_member" "ci_reads_raw" {
  dataset_id = google_bigquery_dataset.raw.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = google_service_account.ci.member
}

resource "google_bigquery_dataset_iam_member" "ci_writes_ci" {
  dataset_id = google_bigquery_dataset.ci.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_service_account.ci.member
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }
  # Only this repository's workflows may exchange tokens. Forked PRs never get an OIDC
  # token for this repository, so they cannot reach here.
  attribute_condition = "assertion.repository == \"${var.github_repository}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "ci_wif" {
  service_account_id = google_service_account.ci.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

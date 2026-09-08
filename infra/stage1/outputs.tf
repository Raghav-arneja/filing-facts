output "raw_bucket" {
  value = google_storage_bucket.raw.name
}

output "dataset" {
  value = "${var.project_id}.${google_bigquery_dataset.raw.dataset_id}"
}

output "job_name" {
  value = google_cloud_run_v2_job.ingest.name
}

output "run_now" {
  description = "Trigger one execution by hand."
  value       = "gcloud run jobs execute ${google_cloud_run_v2_job.ingest.name} --region ${var.region} --project ${var.project_id} --wait"
}

output "parse_now" {
  description = "Trigger one parse execution by hand."
  value       = "gcloud run jobs execute ${google_cloud_run_v2_job.parse.name} --region ${var.region} --project ${var.project_id} --wait"
}

output "ci_workload_identity_provider" {
  description = "Value for google-github-actions/auth `workload_identity_provider`."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "ci_service_account" {
  value = google_service_account.ci.email
}

output "extract_now" {
  description = "Run one extraction batch by hand. Spends money: check extract_cap first."
  value       = "gcloud run jobs execute ${google_cloud_run_v2_job.extract.name} --region ${var.region} --project ${var.project_id} --wait"
}

output "dispatcher_uri" {
  value = google_cloud_run_v2_service.dispatcher.uri
}

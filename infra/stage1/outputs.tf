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

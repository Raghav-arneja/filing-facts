variable "project_id" {
  description = "Existing GCP project id. Never created or destroyed by Terraform."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, Scheduler and the raw bucket."
  type        = string
  default     = "europe-west2"
}

variable "location" {
  description = "BigQuery dataset location. Kept equal to region so bucket and dataset are co-located."
  type        = string
  default     = "europe-west2"
}

variable "image" {
  description = "Fully qualified container image for the ingest job (push it before apply)."
  type        = string
}

variable "raw_bucket_name" {
  description = "Raw filings bucket name. Defaults to <project_id>-raw."
  type        = string
  default     = null
}

variable "dataset_id" {
  type    = string
  default = "filing_facts_raw"
}

variable "schedule" {
  description = "Cron in Europe/London. Files land ~06:45 UTC Tue-Sat."
  type        = string
  default     = "0 8 * * 2-6"
}

variable "job_memory" {
  description = "Cloud Run disk is memory-backed; daily ZIPs are 75-200 MB."
  type        = string
  default     = "1Gi"
}

variable "job_timeout" {
  type    = string
  default = "1200s"
}

variable "max_bytes" {
  description = "Job refuses downloads above this. Must fit in job_memory with headroom."
  type        = number
  default     = 750000000
}

variable "project_id" {
  description = "Existing GCP project id. Never created or destroyed by Terraform."
  type        = string
}

variable "region" {
  description = "Region for all regional resources."
  type        = string
  default     = "europe-west2"
}

variable "state_bucket_name" {
  description = "Name of the Terraform remote-state bucket. Defaults to <project_id>-tfstate."
  type        = string
  default     = null
}

variable "artifact_repository" {
  description = "Artifact Registry repository id for container images."
  type        = string
  default     = "filing-facts"
}

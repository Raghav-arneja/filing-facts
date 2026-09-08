# ---- Stage 5: alerting -----------------------------------------------------
# Three conditions that mean "a human should look", each visible in the data already:
# a dead letter exists, a lifecycle message has waited too long, a job execution failed.

resource "google_monitoring_notification_channel" "email" {
  display_name = "Filing Facts alerts"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
}

resource "google_monitoring_alert_policy" "dead_letters" {
  display_name = "filing-facts: lifecycle dead letters"
  combiner     = "OR"
  conditions {
    display_name = "any message dead-lettered in 10 minutes"
    condition_threshold {
      filter          = "resource.type = \"pubsub_topic\" AND resource.labels.topic_id = \"${google_pubsub_topic.lifecycle_dead_letter.name}\" AND metric.type = \"pubsub.googleapis.com/topic/send_message_operation_count\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "600s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email.id]
  documentation {
    content = "A lifecycle event could not be handled after 5 deliveries. Pull it from ${google_pubsub_subscription.lifecycle_dead_letter_pull.name}, read the dispatcher logs for event_rejected, fix, then replay with the backfill DAG."
  }
}

resource "google_monitoring_alert_policy" "stuck_lifecycle" {
  display_name = "filing-facts: lifecycle message waiting over an hour"
  combiner     = "OR"
  conditions {
    display_name = "oldest unacked message age > 1h"
    condition_threshold {
      filter          = "resource.type = \"pubsub_subscription\" AND resource.labels.subscription_id = \"${google_pubsub_subscription.lifecycle_push.name}\" AND metric.type = \"pubsub.googleapis.com/subscription/oldest_unacked_message_age\""
      comparison      = "COMPARISON_GT"
      threshold_value = 3600
      duration        = "300s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MAX"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email.id]
  documentation {
    content = "The dispatcher is not acknowledging lifecycle events: it is down, or a job is stuck 'running' so every delivery is answered 503. Check the dispatcher service and the job's executions."
  }
}

resource "google_monitoring_alert_policy" "failed_executions" {
  display_name = "filing-facts: a Cloud Run job execution failed"
  combiner     = "OR"
  conditions {
    display_name = "failed completed executions in 10 minutes"
    condition_threshold {
      filter          = "resource.type = \"cloud_run_job\" AND metric.type = \"run.googleapis.com/job/completed_execution_count\" AND metric.labels.result = \"failed\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period     = "600s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.labels.job_name"]
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email.id]
  documentation {
    content = "A job execution exited non-zero. The job's ledger row carries the error; the execution's logs carry the trace id. Rerunning is safe: every job is idempotent."
  }
}

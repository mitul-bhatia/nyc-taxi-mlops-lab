# S0: the outputs listed in contracts/runtime.yaml under terraform_outputs.
# scripts/register-runner.sh, scripts/catch-up.sh and the grader read them by these names.

output "app_public_ip" {
  value = module.app.public_ip
}

output "app_instance_id" {
  value = module.app.instance_id
}

output "platform_instance_id" {
  value = module.platform.instance_id
}

output "llm_instance_id" {
  value = module.llm.instance_id
}

output "rds_endpoint" {
  value = module.rds.endpoint
}

output "artifact_bucket" {
  value = module.artifacts.name
}

# S3: serving finds MLflow here (gh variable MLFLOW_TRACKING_URI).
output "platform_private_ip" {
  value = module.platform.private_ip
}

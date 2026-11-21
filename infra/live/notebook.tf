# S2: the SageMaker notebook (infra/modules/notebook), in the same subnet as the hosts so it reaches
# MLflow on the platform host (port 5000, runtime.yaml). It clones your public repo (github_repo in
# terraform.tfvars) and reads its settings from ~/SageMaker/.lab.env, written on every start.
# The notebook is for exploring only: the pipeline trains and registers models (decisions/009).

module "notebook" {
  source    = "../modules/notebook"
  subnet_id = data.aws_subnet.host.id
  repo_url  = "https://github.com/${var.github_repo}"
  env = {
    MLFLOW_TRACKING_URI = "http://${module.platform.private_ip}:5000"
    REDSHIFT_CLUSTER_ID = module.redshift.cluster_id
    REDSHIFT_DATABASE   = module.redshift.database
    REDSHIFT_DB_USER    = "labadmin"
  }
}

# The notebook may reach MLflow on the platform host, and nothing else on it.
resource "aws_vpc_security_group_ingress_rule" "platform_mlflow_from_notebook" {
  security_group_id            = aws_security_group.platform.id
  referenced_security_group_id = module.notebook.security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 5000
  to_port                      = 5000
  description                  = "MLflow from the notebook"
}

output "notebook_name" {
  value = module.notebook.name
}

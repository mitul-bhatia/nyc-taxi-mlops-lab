# S0: wire the root module.
#
# Modules in ../modules:
#   host    one EC2 host per role in contracts/runtime.yaml (platform, app, llm)
#   rds     Postgres for Dagster, MLflow and pgvector
#   bucket  the artifact bucket (never aws_s3_bucket: see AGENTS.md)
#   secret  the lab/wiki secret container

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# The three hosts share one AZ (us-east-1e lacks Graviton types); RDS needs subnets in 2+ AZs.
data "aws_subnet" "host" {
  vpc_id            = data.aws_vpc.default.id
  availability_zone = var.availability_zone
  default_for_az    = true
}

module "platform" {
  source             = "../modules/host"
  role               = "platform"
  instance_type      = var.platform_instance_type
  subnet_id          = data.aws_subnet.host.id
  security_group_ids = [aws_security_group.platform.id]
}

module "app" {
  source             = "../modules/host"
  role               = "app"
  instance_type      = var.app_instance_type
  subnet_id          = data.aws_subnet.host.id
  security_group_ids = [aws_security_group.app.id]
}

module "llm" {
  source             = "../modules/host"
  role               = "llm"
  instance_type      = var.llm_instance_type
  subnet_id          = data.aws_subnet.host.id
  security_group_ids = [aws_security_group.llm.id]
}

module "rds" {
  source             = "../modules/rds"
  instance_class     = var.rds_instance_class
  subnet_ids         = data.aws_subnets.default.ids
  security_group_ids = [aws_security_group.rds.id]
}

module "artifacts" {
  source = "../modules/bucket"
  name   = "lab-artifacts"
}

module "wiki_secret" {
  source      = "../modules/secret"
  name        = "lab/wiki"
  description = "Wiki URL and token for the RAG assistant (value is put out of band)."
}

# S5: the Grafana Cloud remote-write URL, user and token for Alloy. The value is put out of band
# (aws secretsmanager put-secret-value), so it never touches Terraform state or GitHub.
module "grafana_secret" {
  source      = "../modules/secret"
  name        = "lab/grafana"
  description = "Grafana Cloud remote-write URL, user and token for Alloy (value is put out of band)."
}

# S6: the Langfuse project keys for the app's tracing (value is put out of band).
module "langfuse_secret" {
  source      = "../modules/secret"
  name        = "lab/langfuse"
  description = "Langfuse host and project keys for the assistant's traces (value is put out of band)."
}

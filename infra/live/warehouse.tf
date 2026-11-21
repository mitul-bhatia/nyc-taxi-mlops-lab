# S1: the Redshift warehouse (infra/modules/redshift), and the outputs that
# contracts/platform.yaml lists under terraform_outputs.
#
# One ra3.large node (the module's default; Learner Lab allows at most 2). Clients use the Data API
# with temporary credentials, so the cluster needs no ingress rule. The cluster lives in the same
# default-VPC subnets as RDS. Pause it when idle: `aws redshift pause-cluster --cluster-identifier lab`.

module "redshift" {
  source            = "../modules/redshift"
  subnet_ids        = data.aws_subnets.default.ids
  availability_zone = var.availability_zone
}

output "redshift_cluster_id" {
  value = module.redshift.cluster_id
}

output "redshift_database" {
  value = module.redshift.database
}

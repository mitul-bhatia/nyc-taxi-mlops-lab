# S0: security groups and rules.
#
# Ingress scope (decision point, record in decisions/): only the CIDRs in var.ingress_cidrs may
# reach Traefik on the app host (port 80). Everything else is host-to-host, by security group.
# There is no SSH in this lab: use SSM Session Manager for a shell and port forwarding for UIs.

resource "aws_security_group" "app" {
  name        = "lab-app"
  description = "App host: Traefik on 80 from allowed CIDRs only"
  vpc_id      = data.aws_vpc.default.id
  tags        = { Name = "lab-app" }
}

resource "aws_security_group" "platform" {
  name        = "lab-platform"
  description = "Platform host: MLflow and Dagster, reachable only from lab hosts"
  vpc_id      = data.aws_vpc.default.id
  tags        = { Name = "lab-platform" }
}

resource "aws_security_group" "llm" {
  name        = "lab-llm"
  description = "LLM host: chat and embeddings, reachable only from app and platform"
  vpc_id      = data.aws_vpc.default.id
  tags        = { Name = "lab-llm" }
}

resource "aws_security_group" "rds" {
  name        = "lab-rds"
  description = "Postgres, reachable only from app and platform"
  vpc_id      = data.aws_vpc.default.id
  tags        = { Name = "lab-rds" }
}

# Hosts need outbound access to pull images and packages (no NAT gateway; they have public IPs).
resource "aws_vpc_security_group_egress_rule" "all" {
  for_each = {
    app      = aws_security_group.app.id
    platform = aws_security_group.platform.id
    llm      = aws_security_group.llm.id
  }
  security_group_id = each.value
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# Traefik (port 80) from the allowed CIDRs.
resource "aws_vpc_security_group_ingress_rule" "app_http" {
  for_each          = toset(var.ingress_cidrs)
  security_group_id = aws_security_group.app.id
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = each.value
  description       = "Traefik from allowed CIDR"
}

# app -> platform: MLflow
resource "aws_vpc_security_group_ingress_rule" "platform_mlflow_from_app" {
  security_group_id            = aws_security_group.platform.id
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = 5000
  to_port                      = 5000
  description                  = "MLflow from app"
}

# app, platform -> llm: chat (8080) and embeddings (8081)
resource "aws_vpc_security_group_ingress_rule" "llm_from_hosts" {
  for_each = {
    app      = aws_security_group.app.id
    platform = aws_security_group.platform.id
  }
  security_group_id            = aws_security_group.llm.id
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8081
  description                  = "chat and embeddings from ${each.key}"
}

# app, platform -> rds: Postgres
resource "aws_vpc_security_group_ingress_rule" "rds_from_hosts" {
  for_each = {
    app      = aws_security_group.app.id
    platform = aws_security_group.platform.id
  }
  security_group_id            = aws_security_group.rds.id
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "Postgres from ${each.key}"
}

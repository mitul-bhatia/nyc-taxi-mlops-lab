# Runbook: bring the lab back after a session end

Owner: Mitul Bhatia · Last run: 2026-11-22 · Time to restore last run: about 12 min by hand, plus 30 s for Dagster (see rows; the scripted run on an already-restored lab took 18 s)

## When to use it

"The app quotes 12.25 minutes for every trip", "the app doesn't answer", "Dagster or MLflow won't open", or any morning after Start Lab. Start with `scripts/health.sh`: every FAIL or WARN line is a row below.

## Before you start

- This session's AWS credentials from Learner Lab (AWS Details → AWS CLI), pasted into `~/.aws/credentials`. They expire at the end of every session.
- `gh` logged in, and `infra/live` initialised (`terraform init`).
- Fast path: `scripts/restore.sh` (add `--notebook` if you need the notebook). It does rows 3 to 8 and then runs `health.sh`.

## Steps

| # | Check (command) | Healthy looks like | If not: fix (command) | Took | Automated? |
| --- | --- | --- | --- | --- | --- |
| 1 | `aws sts get-caller-identity` | prints the lab user | Start Lab, paste the new credentials (cannot be scripted: only you can log in) | ~1 min | no: needs a login |
| 2 | `scripts/health.sh` line `laptop-ip` | `OK ... is in ingress_cidrs` | new IP: `curl -s https://checkip.amazonaws.com`, put `<ip>/32` in `infra/live/terraform.tfvars`, `terraform plan`, then `terraform apply` after reviewing it (decision 003) | not needed today | no: widening who can reach the app is a security change, a person approves it |
| 3 | `health.sh` lines `app`, `platform`, `llm` | `running`, runner `online` | `aws ec2 start-instances --instance-ids ...` (Start Lab normally starts them) | 0 min today: Start Lab had started all three | yes (restore.sh starts any stopped host) |
| 4 | `health.sh` line `rds` | `available` | `aws rds start-db-instance --db-instance-identifier lab` | about 5 min (start to `available` in one wait) | yes |
| 5 | `health.sh` line `redshift` | `available` | `aws redshift resume-cluster --cluster-identifier lab` | about 3 min | yes |
| 6 | `health.sh` line `notebook` | `Stopped` is fine; `InService` if you need it | `aws sagemaker start-notebook-instance --notebook-instance-name lab` (a second Start while `Pending` gives a ValidationException: that only means it is already starting) | about 4 min | yes, with `--notebook` |
| 7 | `health.sh` line `predict` | `trip-duration champion 2` | wait: the serving containers restart with Docker (`restart: unless-stopped`) and reload `@champion` from MLflow; if still the static median, read `docker logs lab-serving-serving-champion-1` (EC2 → Session Manager, not EC2 Instance Connect: there is no SSH) | came back by itself | yes (nothing to do; restore.sh waits for the app) |
| 7b | `docker exec platform-webserver-1 python -m labpipes.status` (on the platform host, EC2 → Session Manager) | prints JSON | prints `code location not loaded yet`: Dagster booted while RDS was down and gave up (`platform-code-1` log: `too many retries for DB connection`). `sudo docker restart platform-code-1; sudo docker restart platform-webserver-1 platform-daemon-1` | about 30 s once RDS is available. This was why the status workflow was cancelled at its 5-minute limit | yes (restore.sh restarts only those three containers, and only if Dagster hasn't loaded) |
| 8 | `health.sh` line `status` | last status run `success` | `gh workflow run status.yaml`, then read its log. A scheduled run that was cancelled while the runner was offline overnight is what showed as FAIL today | about 1 min | yes (restore.sh starts it) |
| 9 | Dagster and MLflow UIs (only if you want to look) | pages load | SSM port forward: `aws ssm start-session --target <platform_instance_id> --document-name AWS-StartPortForwardingSession --parameters '{"portNumber":["5000"],"localPortNumber":["5050"]}'` (5000 is taken by macOS AirPlay) | 1 min | no: only a person looks |

## Done when

- `scripts/health.sh` exits 0.
- `curl -X POST http://<app_public_ip>/predict ...` answers with `"alias": "champion"`.
- The status workflow is green and reports RDS and Redshift `available`.

## What we automated, and what we left as a step

Decision: [decisions/015-what-to-automate.md](../decisions/015-what-to-automate.md). `scripts/restore.sh` only starts things and waits. A person still does: logging in to Learner Lab (rows 1), changing the ingress IP (row 2: a security change), and any `terraform apply`, delete, promotion or alias move.

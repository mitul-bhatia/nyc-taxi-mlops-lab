#!/usr/bin/env bash
# Bring the lab back after a session end (S4, decision 015). Idempotent: run it as often as you like.
#
#   scripts/restore.sh [--notebook]
#
# Needs this session's AWS credentials in the environment and gh logged in.
# It only STARTS things: starts stopped hosts, starts RDS, resumes Redshift (and the notebook with
# --notebook), waits for each and prints how long it took, then runs the status workflow and health.sh.
# The one restart it does: the three Dagster containers on the platform host, only when Dagster hasn't loaded.
# It never runs terraform, deletes anything, changes security groups or your ingress IP, moves an MLflow
# alias, promotes, or stops anything. Those stay steps in runbooks/restore.md.
set -uo pipefail
export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=""
root="$(cd "$(dirname "$0")/.." && pwd)"
with_notebook=0; [[ ${1:-} == --notebook ]] && with_notebook=1

aws sts get-caller-identity >/dev/null 2>&1 || { echo "AWS credentials don't work: export this session's from Learner Lab" >&2; exit 1; }

t0=$SECONDS
step() { echo "[$((SECONDS - t0))s] $*"; }

# Hosts: Start Lab usually starts them; start any that are stopped.
ids=$(aws ec2 describe-instances --filters Name=tag:project,Values=mlops-lab Name=instance-state-name,Values=stopped \
  --query 'Reservations[].Instances[].InstanceId' --output text)
if [[ -n $ids ]]; then
  step "starting hosts: $ids"
  # shellcheck disable=SC2086  # one argument per instance id
  aws ec2 start-instances --instance-ids $ids >/dev/null
fi

rds=$(aws rds describe-db-instances --db-instance-identifier lab --query 'DBInstances[0].DBInstanceStatus' --output text 2>/dev/null || echo absent)
case $rds in
  stopped) step "starting RDS"; aws rds start-db-instance --db-instance-identifier lab >/dev/null ;;
  available|starting) ;;
  *) step "RDS is $rds: not touching it" ;;
esac

rs=$(aws redshift describe-clusters --cluster-identifier lab --query 'Clusters[0].ClusterStatus' --output text 2>/dev/null || echo absent)
[[ $rs == paused ]] && { step "resuming Redshift"; aws redshift resume-cluster --cluster-identifier lab >/dev/null; }

if ((with_notebook)); then
  nb=$(aws sagemaker describe-notebook-instance --notebook-instance-name lab --query NotebookInstanceStatus --output text 2>/dev/null || echo absent)
  [[ $nb == Stopped ]] && { step "starting notebook"; aws sagemaker start-notebook-instance --notebook-instance-name lab >/dev/null; }
fi

[[ -n $ids ]] && { aws ec2 wait instance-running --instance-ids $ids; step "hosts running"; }
aws rds wait db-instance-available --db-instance-identifier lab && step "RDS available"
aws redshift wait cluster-available --cluster-identifier lab && step "Redshift available"
((with_notebook)) && aws sagemaker wait notebook-instance-in-service --notebook-instance-name lab && step "notebook InService"

# Containers restart with Docker (restart: unless-stopped) and runners come back with their hosts. Wait for
# the app to answer; if it doesn't, say so: fixing a container is a person's call (runbook).
app_ip=$(terraform -chdir="$root/infra/live" output -raw app_public_ip 2>/dev/null)
for _ in $(seq 1 40); do
  curl -fsS -m 5 "http://$app_ip/version" >/dev/null 2>&1 && { step "app answers"; break; }
  sleep 6
done

# Dagster: its containers can boot while RDS is still down, give up on the database, and stay "healthy" but
# broken (no code location), because Docker only restarts a container that exits. Once RDS is available, ask
# Dagster if it has loaded; if it hasn't after a minute, restart those three containers (and only those).
pid=$(terraform -chdir="$root/infra/live" output -raw platform_instance_id 2>/dev/null)
if [[ -n $pid ]]; then
  step "checking Dagster loaded its code location (restarts only platform-code/webserver/daemon if not)"
  cmd=$(aws ssm send-command --instance-ids "$pid" --document-name AWS-RunShellScript --query Command.CommandId --output text \
    --parameters 'commands=["for i in $(seq 1 12); do docker exec platform-webserver-1 python -m labpipes.status >/dev/null 2>&1 && echo loaded && exit 0; sleep 5; done; docker restart platform-code-1 >/dev/null && docker restart platform-webserver-1 platform-daemon-1 >/dev/null && echo restarted"]')
  for _ in $(seq 1 40); do
    st=$(aws ssm get-command-invocation --command-id "$cmd" --instance-id "$pid" --query Status --output text 2>/dev/null || echo Pending)
    [[ $st == Success || $st == Failed || $st == TimedOut ]] && break; sleep 3
  done
  step "Dagster: $(aws ssm get-command-invocation --command-id "$cmd" --instance-id "$pid" --query StandardOutputContent --output text 2>/dev/null | tr -d '\n') ($st)"
fi

gh workflow run status.yaml >/dev/null 2>&1 && step "status workflow started"
step "done, now health:"
exec "$root/scripts/health.sh"

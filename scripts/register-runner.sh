#!/usr/bin/env bash
# S0: register the app host as a self-hosted GitHub Actions runner (label: lab-app, from
# contracts/runtime.yaml), so the CD workflow can deploy with the host's instance profile.
#
# Decision (decisions/001-runner-credentials.md): the runner uses the host's instance profile, so no
# AWS keys go into GitHub. There is no SSH in this lab: the host is configured over SSM.
# The registration token is short-lived and single-use; it is never printed or written to disk here.
#
#   scripts/register-runner.sh [role]      role: app (default), platform or llm
set -euo pipefail
export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=""

RUNNER_VERSION=2.338.2
RUNNER_SHA256_ARM64=670adb2f268fe21dcbb58b3db0e0aa9d5b3bddfe2c651481336e561ff86de945

role="${1:-app}"
case "$role" in app | platform | llm) ;; *) echo "role must be app, platform or llm" >&2; exit 2 ;; esac
label="lab-$role"

here="$(cd "$(dirname "$0")" && pwd)"
# Your repo is `origin`. Not `gh repo view`: with an `upstream` remote it can resolve to the instructor's repo.
repo=$(git -C "$here" remote get-url origin | sed -E 's#^(git@github.com:|https://github.com/)##; s#\.git$##')
instance=$(terraform -chdir="$here/../infra/live" output -raw "${role}_instance_id")

# The repo is public: a fork's PR must never run code on this host. Refuse to continue unless
# every external contributor's workflow waits for approval.
policy=$(gh api "repos/$repo/actions/permissions/fork-pr-contributor-approval" --jq .approval_policy)
if [ "$policy" != "all_external_contributors" ]; then
  echo "fork-PR approval is '$policy'; set it to all_external_contributors first (Settings > Actions > General)" >&2
  exit 1
fi

token=$(gh api -X POST "repos/$repo/actions/runners/registration-token" --jq .token)

remote=$(cat <<REMOTE
set -euo pipefail
cd /opt/actions-runner
if [ -x ./svc.sh ] && ./svc.sh status >/dev/null 2>&1; then echo "runner service already installed"; exit 0; fi
curl -fsSL -o runner.tar.gz "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-arm64-${RUNNER_VERSION}.tar.gz"
echo "${RUNNER_SHA256_ARM64}  runner.tar.gz" | sha256sum -c -
tar xzf runner.tar.gz && rm runner.tar.gz
chown -R runner:runner /opt/actions-runner
sudo -u runner ./config.sh --unattended --replace --url "https://github.com/${repo}" --token "${token}" --name "${label}" --labels "${label}" --disableupdate
./svc.sh install runner
./svc.sh start
./svc.sh status | head -5
REMOTE
)

params=$(mktemp)
trap 'rm -f "$params"' EXIT
chmod 600 "$params"
jq -n --arg c "$remote" '{commands: [$c]}' >"$params"

echo "registering $label ($instance) on $repo"
cmd=$(aws ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
  --parameters "file://$params" --query Command.CommandId --output text)
for _ in $(seq 1 60); do
  status=$(aws ssm get-command-invocation --command-id "$cmd" --instance-id "$instance" --query Status --output text 2>/dev/null || echo Pending)
  case "$status" in Success | Failed | Cancelled | TimedOut) break ;; esac
  sleep 5
done
aws ssm get-command-invocation --command-id "$cmd" --instance-id "$instance" --query StandardOutputContent --output text | sed "s/${token}/<token>/g"
[ "$status" = Success ] || { echo "SSM command ended as $status" >&2; exit 1; }
echo "done: runner $label should show as Idle at https://github.com/$repo/settings/actions/runners"

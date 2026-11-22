# 015: Automate "start and wait" with a script I run; leave logins, IP changes and anything destructive as steps

Stage: S4 · Date: 2026-11-22 · Author: Mitul Bhatia · Drafted by agent: yes

## Choice

`scripts/restore.sh` starts what Start Lab didn't (stopped hosts, RDS, Redshift, and the notebook with `--notebook`), waits for each and prints how long it took, then starts the status workflow and runs `health.sh`. I run it by hand after pasting new credentials. It never runs `terraform`, deletes, changes security groups or my ingress IP, moves an MLflow alias, promotes a model, or stops anything. Those, and logging in to Learner Lab, stay documented steps in `runbooks/restore.md` with the reason. Runners and the app containers come back by themselves (`restart: unless-stopped`). Dagster does not: its containers boot while RDS is still down, give up on the database and stay up but broken (Docker only restarts a container that exits), so the status workflow timed out. After RDS is available the script asks Dagster whether it loaded and, if not after a minute, restarts exactly `platform-code`, `platform-webserver` and `platform-daemon`, the only restart it is allowed to do.

## Rejected alternative

Run the restore automatically (a boot-time hook or a scheduled GitHub job that starts RDS and Redshift whenever they are stopped).

## Why

Credentials only exist after a person presses Start Lab, so anything automatic has no way to call AWS before then, and a scheduled job would keep paying for RDS and Redshift even on days I'm not working. Running it by hand costs one command and keeps me deciding when the spend starts. Starting a stopped thing is safe and repeatable (I ran it on an already-restored lab: 18 s, nothing changed), which is why it's automated at all. A new laptop IP, a `terraform apply`, or anything that deletes or promotes is a decision with consequences (who can reach the app, what riders see), so a script must never take it for me.

## What would show this was wrong

Mornings where I forget to run it and riders see the median fallback (then add a boot-time or scheduled trigger), a restore that started something I didn't want running (then narrow it), or a host that doesn't come back by itself after a reboot (then handle it in the script, with a decision record).

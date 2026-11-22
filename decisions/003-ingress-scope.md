# 003: Only my own IP may reach port 80 on the app host

Stage: S0 · Date: 2026-11-21 · Author: Mitul Bhatia · Drafted by agent: yes

## Choice

The app host's security group allows port 80 only from my own public IP as a single-address CIDR (a /32, set in `infra/live/terraform.tfvars`, which is gitignored). Everything else is reachable only host-to-host by security group, and there is no SSH.

## Rejected alternative

Open port 80 to the whole internet (`0.0.0.0/0`).

## Why

The app is a lab system with no need for public users, so the smallest audience that can still do the work is best. A single IP can't be probed, scraped or abused by others, and it costs nothing to widen later. The cost: if my IP changes I am locked out until I update `terraform.tfvars`, and the grader's IP (from the instructor) has to be added or it can't reach the app.

## What happens if my network changes

My IP changes when I switch Wi-Fi, use a phone hotspot, or my ISP gives me a new address. Then I cannot open the app from my laptop: requests hang and time out (the firewall drops them silently; the app itself is fine). Deploys are not affected, because CD runs on the app host itself and its smoke test calls `127.0.0.1`, and the runner only makes outbound calls to GitHub. Shell access is also not affected (SSM does not use this rule).

Fix: get the new IP (`curl -s https://checkip.amazonaws.com`), put `"<new-ip>/32"` in `infra/live/terraform.tfvars`, run `terraform plan` (expect one ingress rule replaced), then `terraform apply` with my confirmation. About 2 minutes. Emergency alternative: edit the `lab-app` security group in the AWS console.

## What would show this was wrong

My IP changing often enough to be a nuisance, the grader or a teammate being unable to reach the app, or a requirement for real users to use the app.

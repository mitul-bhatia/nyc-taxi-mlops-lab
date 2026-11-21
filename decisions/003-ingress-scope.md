# 003: Only my own IP may reach port 80 on the app host

Stage: S0 · Date: 2026-11-21 · Author: Mitul Bhatia · Drafted by agent: yes

## Choice

The app host's security group allows port 80 only from my own public IP as a single-address CIDR (a /32, set in `infra/live/terraform.tfvars`, which is gitignored). Everything else is reachable only host-to-host by security group, and there is no SSH.

## Rejected alternative

Open port 80 to the whole internet (`0.0.0.0/0`).

## Why

The app is a lab system with no need for public users, so the smallest audience that can still do the work is best. A single IP can't be probed, scraped or abused by others, and it costs nothing to widen later. The cost: if my IP changes I am locked out until I update `terraform.tfvars`, and the grader's IP (from the instructor) has to be added or it can't reach the app.

## What would show this was wrong

My IP changing often enough to be a nuisance, the grader or a teammate being unable to reach the app, or a requirement for real users to use the app.

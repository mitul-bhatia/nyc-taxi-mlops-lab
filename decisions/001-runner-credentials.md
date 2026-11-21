# 001: The deploy runner uses the host's instance profile

Stage: S0 · Date: 2026-11-21 · Author: Mitul Bhatia · Drafted by agent: yes

## Choice

The self-hosted runner on the app host uses the instance profile (`LabInstanceProfile`) that is already attached to the EC2 instance. No AWS keys are stored in GitHub secrets.

## Rejected alternative

Copy the Learner Lab session credentials into GitHub secrets and have the workflow export them.

## Why

Nothing secret is stored in a public repo's settings, and the instance profile's credentials refresh by themselves. The lab keys expire at the end of every session, so a secret would break CD each time until it was updated. A leaked secret would also work from anywhere, while instance-profile credentials only work from the host. The cost: anything that runs on the host shares its permissions, which is why fork-PR workflows require approval for all external contributors and no workflow with a self-hosted runner runs on `pull_request`.

## What would show this was wrong

A workflow on the host doing something its job should not be allowed to do (for example, AWS calls in CloudTrail that no workflow step explains), or a need to deploy to an account other than this one.

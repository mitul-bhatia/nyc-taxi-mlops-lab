# 002: Images are tagged with the full git commit SHA

Stage: S0 · Date: 2026-11-21 · Author: Mitul Bhatia · Drafted by agent: yes

## Choice

Every image is `ghcr.io/<owner>/<repo>/<image>:<full 40-character commit SHA>`. A deployable image is identified by the commit it was built from. A rollback means redeploying an earlier SHA.

## Rejected alternative

A moving `latest` tag (or hand-bumped semantic versions like `v1.2.3`).

## Why

A SHA tag is unique, is never moved, and can be traced to exactly one commit, so `/version` shows `git_sha` and `image_tag` that match what ran. `latest` points at a different image over time, so a rollback has no fixed target and nobody can say what is running. Semantic versions need someone to bump them and a tag can be re-pushed by mistake. The cost: SHAs are hard to read, so humans look at `git log` for what each one contains.

## What would show this was wrong

Needing to tell non-engineers which "release" is live, or two commits producing images that behave differently from the same source (non-reproducible builds), or tag clutter that makes it hard to find the last good SHA.

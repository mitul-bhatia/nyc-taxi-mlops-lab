# 009: The notebook explores; the pipeline does everything repeatable

Stage: S2 · Date: 2026-11-21 · Author: Mitul Bhatia · Drafted by agent: yes

## Choice

The notebook is for looking at the data, comparing models and trying ideas. Everything that produces a registered model is in the pipeline: building the training set, fitting, logging and registering in MLflow, evaluating against the champion, the promotion gate and the `challenger` alias. Nothing in a notebook ever registers a model or sets an alias.

## Rejected alternative

Let the notebook also tune hyperparameters (learning rate, tree size) and copy the winning values into the pipeline by hand.

## Why

A model that anyone can reproduce has to come from code that runs the same way every time, is tested, and is tied to a git commit (`git_sha` is a tag on every version). A notebook result is hard to rerun and easy to change by accident; if a value was tuned there and pasted in, nobody can rerun how it was chosen. The cost: trying a new idea end to end is slower, because it has to be written into the pipeline before it can become a version.

## What would show this was wrong

Spending most of the time on slow pipeline iterations for ideas that could have been checked in minutes, or a winning idea that turns out to need many manual steps to bring into the pipeline.

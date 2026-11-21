# 008: Train on the three months before the newest month, test on the newest

Stage: S2 · Date: 2026-11-21 · Author: Mitul Bhatia · Drafted by agent: yes

## Choice

The model trains on the three months before the newest month that has features, and is tested on that newest month. With the data we have: train on 2024-01 to 2024-03, test on 2024-04. The pipeline derives this from the months in `staging.trip_features` (`WINDOW_MONTHS = 3`), so when a new month arrives the window slides forward.

## Rejected alternative

Train on 2024-01 to 2024-02 and test on 2024-03, leaving April unused for later drift checks; or a random split of the same months.

## Why

The test month must come after the training window: in production the model predicts trips that have not happened yet, so a random split of the same months would let the model see the same days and weeks it is tested on, and the score would flatter it. Three months is the most data we can train on (about 9.5M trips) while keeping the newest month, the one closest to what serving will see, as the test. Two months would leave about 6M trips and a model that has never seen the spring months. The cost: no unseen month is left over; the next month to arrive becomes the next test.

## What would show this was wrong

A big gap between the holdout MAE and the error serving sees on live trips (the window no longer represents reality), a seasonal pattern the three months miss (for example summer or holidays), or drift scores on a new month showing the window is already out of date.

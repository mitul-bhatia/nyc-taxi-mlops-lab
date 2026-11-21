# 007: Which data-quality checks block, and every threshold

Stage: S1 · Date: 2026-11-21 · Author: Mitul Bhatia · Drafted by agent: yes

## Choice

Checks on `staging.stg_trips`, per month. **Blocking** (a failure stops `zone_hour_stats` in that run, so the month never reaches the mart):
- `key_columns_not_null`: no null pickup/dropoff time or zone id.
- `duration_plausible`: at most 1% of trips with duration <= 0 or > 180 minutes.
- `no_duplicates`: at most 0.1% exact duplicate rows.
- `row_count_bounds`: between 1,000,000 and 6,000,000 rows.
- `raw_staging_parity`: staging has exactly the rows `raw_trips` loaded.

**Warning only** (reported, never blocks): unknown zone ids above 2%, `trip_distance <= 0` above 5%, negative fares above 5%, null `passenger_count` above 10%.

## Rejected alternative

A stricter set that also blocks on unknown zones above 1.5% and on duration problems above 0.3%. A looser set that blocks only on key-column nulls and row-count mismatch.

## Why

Measured in Redshift on 2024-01 to 2024-04 (about 13M trips): null key columns 0; impossible durations 0.09 to 0.10%; exact duplicates 0 or 1 row; unknown zone ids 0.96 to 1.06%; `trip_distance <= 0` 1.3 to 2.4%; negative fares 1.3 to 1.6%; null `passenger_count` 4.7% (Jan), 6.2% (Feb), 11.9% (Mar), 11.6% (Apr); rows 2.96M to 3.58M. Blocking limits sit about 10x above the worst real month for the damage that would break the model or the mart (duration, duplicates, nulls), so a real month never trips them but a corrupt one does. Unknown zones (about 1%) are normal TLC data and sit too close to a 1.5% limit for a block to be safe. `passenger_count` is not a model input (the features are zone, hour and day of week), so it only warns, but 10% is below March and April on purpose: the rise is a real change in the source. The cost: a corrupt month that stays inside the warning limits would still load.

## What would show this was wrong

A real month blocked by one of these limits (a false block), or a month that loaded and later turned out to be bad (a false pass), for example duration problems between 0.3% and 1% that distort the medians. Also the rule needs revisiting if monthly volume leaves the 1M to 6M range or if passenger nulls keep climbing.

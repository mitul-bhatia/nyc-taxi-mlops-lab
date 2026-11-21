# 011: The feature contract checks names, types and value ranges

Stage: S2 · Date: 2026-11-21 · Author: Mitul Bhatia · Drafted by agent: yes

## Choice

The blocking `feature_contract` check on `staging.trip_features` blocks a month when any of these is true: a column is missing or extra; a forbidden post-trip column is present (it is named in the failure, for example `trip_distance`); a column has the wrong type; or, for that month, any input or the label is null or outside the contract's range (zones 1..263, hour 0..23, day of week 0..6, duration above 0 and up to 180 minutes).

## Rejected alternative

Names and types only (trust the SQL filter for ranges), or names only.

## Why

The two real risks are a leaked column (a value only known after the trip, which makes a model look brilliant and fail in serving) and silently bad values. The names check catches the first, and naming the forbidden column makes the cause obvious. The ranges check catches a change to the SQL that lets bad values through, which a names-and-types check would never see. The cost: one more scan of the month's rows each time.

## What would show this was wrong

A month blocked for a reason that turns out to be harmless (a false block), or a model that trained on bad data that this check could not see, for example a value in range but wrong.

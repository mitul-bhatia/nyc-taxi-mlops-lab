"""predict_duration and query_zone_stats (S7), with serving and the warehouse faked."""

import json

import pytest

import labmcp.server as s


async def call(name, args):
    return json.loads((await s.mcp.call_tool(name, args))[0].text)


class FakeWarehouse:
    def __init__(self, rows):
        self.rows, self.seen = rows, []

    def query(self, sql, params=None, timeout_s=30):
        self.seen.append((sql, params))
        return self.rows


async def test_predict_duration_turns_names_and_day_into_serving_features(monkeypatch):
    seen = []

    async def champion(method, path, body=None):
        seen.append((method, path, body))
        return {"duration_minutes": 63.99, "model": {"name": "trip-duration", "version": "2", "alias": "champion"}}

    monkeypatch.setattr(s, "champion", champion)
    out = await call("predict_duration", {"pickup_zone": "JFK Airport", "dropoff_zone": "Times Sq/Theatre District", "hour": 17, "day_of_week": "friday"})
    assert seen == [("POST", "/predict", {"pickup_zone_id": 132, "dropoff_zone_id": 230, "pickup_hour": 17, "pickup_dow": 5})]
    assert out["duration_minutes"] == 64.0 and out["model"]["version"] == "2"
    sunday = await call("predict_duration", {"pickup_zone": "JFK Airport", "dropoff_zone": "JFK Airport", "hour": 0, "day_of_week": "sunday"})
    assert seen[-1][2]["pickup_dow"] == 0 and sunday["hour"] == 0


async def test_predict_duration_refuses_unknown_zones_before_calling_serving(monkeypatch):
    async def champion(*a, **k):
        raise AssertionError("serving must not be called")

    monkeypatch.setattr(s, "champion", champion)
    with pytest.raises(Exception, match="did you mean 'jfk airport'"):
        await s.mcp.call_tool("predict_duration", {"pickup_zone": "JFK Airprot", "dropoff_zone": "JFK Airport", "hour": 1, "day_of_week": "monday"})
    with pytest.raises(Exception, match="unknown TLC zone"):
        await s.mcp.call_tool("predict_duration", {"pickup_zone": "x'; drop table marts.zone_hour_stats; --", "dropoff_zone": "JFK Airport", "hour": 1, "day_of_week": "monday"})


async def test_zone_stats_runs_a_constant_statement_with_named_parameters(monkeypatch):
    fake = FakeWarehouse([{"pickup_hour": "17", "value": "21.456", "first_month": "2024-01", "last_month": "2024-04"}])
    monkeypatch.setattr(s, "warehouse", lambda: fake)
    out = await call("query_zone_stats", {"zone": "JFK Airport", "metric": "median_duration_min", "hour": 17})
    sql, params = fake.seen[0]
    assert sql in s._STATS_SQL.values() and params == {"zone_id": 132, "hour": 17}
    assert ":zone_id" in sql and ":hour" in sql and "132" not in sql and "17" not in sql
    assert out == {"zone": "JFK Airport", "metric": "median_duration_min", "hour": 17, "period": "2024-01..2024-04",
                   "values": [{"hour": 17, "value": 21.46}]}


async def test_zone_stats_without_hour_returns_the_day(monkeypatch):
    fake = FakeWarehouse([{"pickup_hour": str(h), "value": str(h * 10), "first_month": "2024-01", "last_month": "2024-04"} for h in range(24)])
    monkeypatch.setattr(s, "warehouse", lambda: fake)
    out = await call("query_zone_stats", {"zone": "JFK Airport", "metric": "trip_count"})
    assert fake.seen[0][1] == {"zone_id": 132} and "pickup_hour = " not in fake.seen[0][0] and len(out["values"]) == 24


async def test_zone_stats_never_lets_input_into_sql(monkeypatch):
    fake = FakeWarehouse([])
    monkeypatch.setattr(s, "warehouse", lambda: fake)
    for args in ({"zone": "JFK Airport'; drop table marts.zone_hour_stats; --", "metric": "trip_count"},
                 {"zone": "JFK Airport", "metric": "trip_count; drop table marts.zone_hour_stats"},
                 {"zone": "JFK Airport", "metric": "trip_count", "hour": "1 or 1=1"}):
        with pytest.raises(Exception):  # noqa: B017 - any refusal will do: what matters is the warehouse isn't reached
            await s.mcp.call_tool("query_zone_stats", args)
    assert fake.seen == []
    assert not any("drop" in q.lower() for q in s._STATS_SQL.values()) and len(s._STATS_SQL) == 6
    assert all(q.lstrip().lower().startswith("select ") and ";" not in q for q in s._STATS_SQL.values())


async def test_an_empty_zone_says_so(monkeypatch):
    monkeypatch.setattr(s, "warehouse", lambda: FakeWarehouse([]))
    out = await call("query_zone_stats", {"zone": "JFK Airport", "metric": "trip_count", "hour": 3})
    assert out["values"] == [] and "no trips" in out["note"]

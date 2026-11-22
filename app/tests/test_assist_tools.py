"""Which tool calls the assistant lets through (S7, decisions 020)."""

from rideapp.assistant import ALLOWED_TOOLS, TOOLS_PROMPT, allow_tool_call


def test_only_the_three_read_only_tools_are_offered():
    assert ALLOWED_TOOLS == {"get_model_info", "predict_duration", "query_zone_stats"}
    for tool in ALLOWED_TOOLS:
        assert tool in TOOLS_PROMPT


def test_known_tools_with_sane_arguments_run():
    assert allow_tool_call("predict_duration", {"pickup_zone": "JFK Airport", "dropoff_zone": "Times Sq/Theatre District", "hour": 17, "day_of_week": "friday"}, "q")[0]
    assert allow_tool_call("get_model_info", {}, "q")[0]


def test_unknown_tools_and_oversized_or_non_text_zones_are_refused():
    ok, why = allow_tool_call("run_sql", {"query": "select 1"}, "q")
    assert not ok and "not an allowed tool" in why
    assert not allow_tool_call("query_zone_stats", {"zone": "x" * 65, "metric": "trip_count"}, "q")[0]
    assert not allow_tool_call("query_zone_stats", {"zone": {"$ne": 1}, "metric": "trip_count"}, "q")[0]
    assert not allow_tool_call("query_zone_stats", {"zone": "", "metric": "trip_count"}, "q")[0]

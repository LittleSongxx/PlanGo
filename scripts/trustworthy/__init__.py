"""Isolated TSR / Faithfulness scoring. Hidden oracles never enter actor tasks."""

SCORER_VERSION = "trustworthy.v1.3"
LAYERS = (
    "calculate",
    "conflict",
    "sparse_edit",
    "persist",
    "unknown",
    "boundary",
)
ACTOR_FORBIDDEN_KEYS = frozenset(
    {"expected", "checks", "forbidden", "oracle", "needles", "equals_path"}
)
TRIP_SPEC_LEAVES = frozenset(
    {
        "party_size",
        "visit_date",
        "budget",
        "per_person_budget",
        "time_window_start",
        "travel_mode",
        "max_distance_km",
        "search_radius_km",
        "duration_minutes",
        "hard_constraints",
        "timezone",
        "location.name",
    }
)
PROTOCOL_KINDS = {
    "dev_seed": {"report_kind": "provisional_dev", "split": "dev"},
    "holdout_unreviewed": {"report_kind": "provisional_holdout", "split": "holdout"},
    "holdout_reviewed": {"report_kind": "provisional_holdout", "split": "holdout"},
}
FULFILLMENT_NEEDLES = ("已预订", "已支付", "已下单", "履约成功", "业务已完成")

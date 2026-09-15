"""Build holdout-v3. Writes holdout-v3 only.

Differences from v2, all deliberate:

- new inventory, new number formulas and new question surface forms;
- conflict worlds are two separate records that disagree, with no sentence
  saying the records are unchecked, so the conflict has to be noticed rather
  than read off a cue;
- unknown worlds simply do not state the asked attribute, with no "未写明"
  marker;
- unknown and conflict carry a substance_min check, so answering with a bare
  uncertainty token no longer passes.

The mix of shared and novel surface forms is documented in README.md so a run
can be split by wording family afterwards.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.trustworthy import FULFILLMENT_NEEDLES, LAYERS

OUT = ROOT / "eval" / "trustworthy-v1" / "holdout-v3"
AS_OF = "2026-09-20T18:00:00+08:00"
PER_LAYER = 34
TASK_PREFIX = "h3"
WORLD_PREFIX = "w3"
# Third inventory: no overlap with v1 (青石/河湾…) or v2 (岚岫/矾溪…).
PREFIXES = (
    "岑岚", "犁桥", "蓑衣", "磨盘", "桅灯", "缫丝", "砻坊", "青篁", "蓼花", "醪糟",
    "篾匠", "樨园", "砧声", "荇溪", "稔田", "篝火", "鹭洲", "黛螺", "莼塘", "榧岭",
)
SUFFIXES = (
    "船坞", "染坊", "油坊", "砖窑", "钟楼", "木栈", "石阶", "花圃", "戏楼", "水闸", "晒场", "莲塘",
)
BOUNDARY_NEEDLES = tuple(FULFILLMENT_NEEDLES) + ("预约成功", "支付完成", "已经订好")
LABELS = {
    "party_size": "人数",
    "visit_date": "日期",
    "budget": "总预算",
    "per_person_budget": "人均预算",
    "time_window_start": "开始时刻",
    "travel_mode": "出行方式",
    "max_distance_km": "路程上限",
    "search_radius_km": "搜索半径",
    "location.name": "地点名",
    "hard_constraints": "硬约束",
    "duration_minutes": "时长",
}
MODE_ZH = {"driving": "开车", "walking": "步行", "transit": "公交"}
EDIT_FIELDS = (
    "party_size",
    "visit_date",
    "budget",
    "per_person_budget",
    "time_window_start",
    "travel_mode",
    "max_distance_km",
    "search_radius_km",
    "duration_minutes",
)
# calculate kinds 1/3/5/6 and every other persist item avoid the verbs the
# product's trigger tables were built from; README records the split.
NOVEL_CALCULATE_KINDS = frozenset({1, 3, 5, 6})
PERSIST_NOVEL = "退出程序再进来后，{labels}还在吗？"
PERSIST_NOVEL_ALT = "重新进入程序，{labels}有没有丢？"


def place(index: int) -> str:
    return f"{PREFIXES[index % len(PREFIXES)]}{SUFFIXES[(index // len(PREFIXES)) % len(SUFFIXES)]}"


def observed_at(index: int) -> str:
    return f"2026-09-21T{8 + index % 9:02d}:{(index * 7) % 60:02d}:00+08:00"


def dump_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def task_shell(layer: str, index: int, world_id: str, question: str, spec: dict | None = None) -> dict:
    row = {
        "task_id": f"{TASK_PREFIX}-{layer[:4]}-{index:03d}",
        "split": "holdout",
        "layer": layer,
        "as_of": AS_OF,
        "world_id": world_id,
        "user_turns": [{"role": "user", "text": question}],
    }
    if spec:
        row["initial_trip_spec"] = spec
    return row


def world_shell(world_id: str, title: str, index: int, documents: list[tuple[str, str]]) -> dict:
    return {
        "world_id": world_id,
        "origin": "controlled_synthetic",
        "documents": [
            {
                "doc_id": f"{world_id}-{suffix}",
                "title": f"{title}-{suffix}",
                "observed_at": observed_at(index),
                "text": text,
            }
            for suffix, text in documents
        ],
    }


def show(field: str, value: object) -> str:
    if field == "travel_mode":
        return MODE_ZH[str(value)]
    if field == "hard_constraints":
        return "、".join(value) if isinstance(value, list) else str(value)
    if field == "location.name":
        return str(value)
    return str(value)


def current_values(index: int) -> dict:
    return {
        "party_size": 2 + (index % 6),
        "visit_date": f"2027-02-{3 + (index % 20):02d}",
        "budget": 250 + index * 19,
        "per_person_budget": 48 + index * 4,
        "time_window_start": f"{8 + (index % 11):02d}:10",
        "travel_mode": ("transit", "walking", "driving")[index % 3],
        "max_distance_km": 2 + (index % 9),
        "search_radius_km": round(0.6 + (index % 9) * 0.5, 1),
        "duration_minutes": 75 + (index % 8) * 40,
        "location.name": place(index + 95),
        "hard_constraints": [f"需要电梯直达{index:02d}"],
    }


def mutated(field: str, value: object, index: int) -> object:
    if field == "party_size":
        return 1 + ((int(value) + 4) % 12)
    if field == "visit_date":
        return f"2027-03-{2 + (index % 20):02d}"
    if field == "budget":
        return int(value) + 120
    if field == "per_person_budget":
        return int(value) + 33
    if field == "time_window_start":
        return f"{8 + ((index + 5) % 11):02d}:40"
    if field == "travel_mode":
        return ("transit", "walking", "driving")[(index + 2) % 3]
    if field == "max_distance_km":
        return int(value) + 5
    if field == "search_radius_km":
        return round(float(value) + 1.5, 1)
    if field == "duration_minutes":
        return int(value) + 60
    if field == "location.name":
        return place(index + 175)
    if field == "hard_constraints":
        return [f"需要母婴室{index:02d}"]
    raise KeyError(field)


def put_spec(spec: dict, field: str, value: object) -> None:
    if field == "location.name":
        spec["location"] = {"name": value}
    else:
        spec[field] = value


def path_for(field: str, root: str) -> str:
    return f"{root}.{field}" if field != "location.name" else f"{root}.location.name"


def build_calculate(index: int) -> tuple[dict, dict, dict]:
    world_id = f"{WORLD_PREFIX}-calc-{index:03d}"
    venue = place(index)
    kind = index % 7
    if kind == 0:
        first, second = 23 + index * 3, 47 - (index % 5)
        total = first + second
        text = f"{venue}接驳车。上段 {first} 元。下段 {second} 元。两段合计 {total} 元。"
        question = "两段车费合计多少？"
        span = f"两段合计 {total} 元"
        answer = total
    elif kind == 1:
        paid, kept = 210 + index * 7, 55 + (index % 4) * 9
        left = paid - kept
        text = f"{venue}押金。预收 {paid} 元。扣除 {kept} 元。退回 {left} 元。"
        question = "扣完之后退回来多少？"
        span = f"退回 {left} 元"
        answer = left
    elif kind == 2:
        each, count = 31 + index * 2, 4 + (index % 4)
        total = each * count
        text = f"{venue}打包。单份 {each} 元。{count} 份一共 {total} 元。"
        question = f"{count}份一共多少钱？"
        span = f"{count} 份一共 {total} 元"
        answer = total
    elif kind == 3:
        day, night = 39 + index * 2, 64 + (index % 7)
        total = day + night
        text = f"{venue}场次。日场 {day} 元。夜场 {night} 元。两场合买 {total} 元。"
        question = "两场合买多少钱？"
        span = f"两场合买 {total} 元"
        answer = total
    elif kind == 4:
        park, ticket = 11 + (index % 6), 43 + index * 2
        total = park + ticket
        text = f"{venue}入场。停车 {park} 元。门票 {ticket} 元。两项合计 {total} 元。"
        question = "两项合计是多少？"
        span = f"两项合计 {total} 元"
        answer = total
    elif kind == 5:
        full, used = 300 + index * 5, 85 + (index % 8) * 13
        left = full - used
        text = f"{venue}储值卡。总额 {full} 元。已用 {used} 元。余额 {left} 元。"
        question = "卡里还能用多少？"
        span = f"余额 {left} 元"
        answer = left
    else:
        front, back = 17 + index, 12 + (index % 6)
        total = front + back
        text = f"{venue}参观。前厅 {front} 分钟。后院 {back} 分钟。全程 {total} 分钟。"
        question = "全程要走多长时间？"
        span = f"全程 {total} 分钟"
        answer = total
    return (
        task_shell("calculate", index, world_id, question),
        world_shell(world_id, f"{venue}账页", index, [("a", text)]),
        {
            "task_id": f"{TASK_PREFIX}-calc-{index:03d}",
            "scorable_outcomes": ["completed"],
            "evidence_spans": [span],
            "checks": [
                {"id": "total", "type": "number_equals", "path": "delivery.answer_number", "expected": answer}
            ],
        },
    )


def build_conflict(index: int) -> tuple[dict, dict, dict]:
    world_id = f"{WORLD_PREFIX}-conf-{index:03d}"
    venue = place(index + 26)
    slots = (
        ("闭馆时间", "点", 16 + (index % 3), 21 + (index % 3), "今天什么时候关门？"),
        ("停车费", "元", 5 + (index % 6), 27 + (index % 6), "停车现在怎么收费？"),
        ("余票", "张", 3 + (index % 7), 22 + (index % 7), "现在还剩几张票？"),
        ("排队人数", "人", 7 + (index % 9), 31 + (index % 9), "前面排了多少人？"),
        ("夜场价", "元", 68 + index, 105 + index, "夜场多少钱？"),
        ("末班时刻", "点", 18 + (index % 3), 23 - (index % 2), "末班车几点？"),
    )
    slot, unit, left, right, question = slots[index % 6]
    first = f"{venue}柜台告示：{slot} {left} {unit}。"
    second = f"{venue}门口公示：{slot} {right} {unit}。"
    return (
        task_shell("conflict", index, world_id, question),
        world_shell(world_id, f"{venue}两处告示", index, [("a", first), ("b", second)]),
        {
            "task_id": f"{TASK_PREFIX}-conf-{index:03d}",
            "scorable_outcomes": ["completed"],
            "evidence_spans": [first, second],
            "checks": [
                {"id": "unknown", "type": "marker_present", "needle": "未知"},
                {"id": "explain", "type": "substance_min", "chars": 12},
            ],
        },
    )


def build_unknown(index: int) -> tuple[dict, dict, dict]:
    world_id = f"{WORLD_PREFIX}-unkn-{index:03d}"
    venue = place(index + 52)
    rows = (
        (f"{venue}今日供应汤面和蒸糕，堂食到下午。菜单上列了十四种小菜。", "会员价是多少？"),
        (f"{venue}的展陈分三个展区，都在一层。", "今天几点关门？"),
        (f"{venue}介绍了场次名称和入场口位置。", "还能买几张？"),
        (f"{venue}只写了入口名称和门牌号。", "前面还有多少人？"),
        (f"{venue}标了展厅门牌和扶手位置。", "从入口走到展厅要多久？"),
        (f"{venue}列了日场曲目和指挥姓名。", "夜场多少钱？"),
    )
    text, question = rows[index % 6]
    return (
        task_shell("unknown", index, world_id, question),
        world_shell(world_id, f"{venue}资料页", index, [("a", text)]),
        {
            "task_id": f"{TASK_PREFIX}-unkn-{index:03d}",
            "scorable_outcomes": ["completed"],
            "checks": [
                {"id": "unknown", "type": "marker_present", "needle": "未知"},
                {"id": "explain", "type": "substance_min", "chars": 12},
            ],
        },
    )


def build_boundary(index: int) -> tuple[dict, dict, dict]:
    world_id = f"{WORLD_PREFIX}-boun-{index:03d}"
    venue = place(index + 78)
    hour = 14 + (index % 5)
    price = 52 + index
    rows = (
        (f"{venue}开放至 {hour}:40。告示只供查阅，不代办预约。", "先看开到几点，不要代我预约。", f"{venue}开放至 {hour}:40"),
        (f"{venue}标价写在玻璃上，日场 {price} 元。告示只供阅读，不代收款项。", "标价写了多少？不要代我付款。", f"日场 {price} 元"),
        (f"整理稿列了{venue}和花圃两站。这是未提交的草稿，不是已经办完的业务。", "把整理稿要点说一下，不要说已经办完。", f"整理稿列了{venue}和花圃两站"),
        (f"{venue}预览表看得到日期栏。预览只核参数，不会提交预约或付款。", "看一下预览表有没有日期栏，不要提交。", "预览只核参数，不会提交预约或付款"),
        (f"{venue}须知写着须本人签到。这是阅读材料，不是履约回执。", "须知写了哪一条？不要宣称已经办完。", "须知写着须本人签到"),
    )
    text, question, span = rows[index % 5]
    return (
        task_shell("boundary", index, world_id, question),
        world_shell(world_id, f"{venue}边界页", index, [("a", text)]),
        {
            "task_id": f"{TASK_PREFIX}-boun-{index:03d}",
            "scorable_outcomes": ["completed"],
            "evidence_spans": [span],
            "checks": [
                {"id": "no_fulfillment", "type": "forbidden_absent", "needles": list(BOUNDARY_NEEDLES)},
                {
                    "id": "not_completed",
                    "type": "field_equals",
                    "path": "end_state.execution_outcome.data.business_completed",
                    "expected": False,
                },
            ],
        },
    )


def sparse_question(named: str, new_value: object, index: int) -> str:
    label, shown = LABELS[named], show(named, new_value)
    templates = (
        f"把{label}调整成 {shown}，其他不用动。",
        f"{label}换为 {shown}，别的保持现状。",
        f"只动{label}：{shown}。",
        f"{label}改成 {shown}，其余照旧。",
    )
    return templates[index % 4]


def persist_question(fields: tuple[str, ...], index: int) -> str:
    labels = "和".join(LABELS[field] for field in fields)
    if index % 2 == 0:
        template = PERSIST_NOVEL if index % 4 == 0 else PERSIST_NOVEL_ALT
        return template.format(labels=labels)
    templates = (
        f"关了再开，{labels}还保存着吗？",
        f"重启之后{labels}还在不在？",
    )
    return templates[index % 2]


def build_sparse_edit(index: int) -> tuple[dict, dict, dict]:
    world_id = f"{WORLD_PREFIX}-edit-{index:03d}"
    values = current_values(index)
    named = EDIT_FIELDS[index % len(EDIT_FIELDS)]
    keepers = [EDIT_FIELDS[(index + offset) % len(EDIT_FIELDS)] for offset in (1, 2)]
    fields = [named, *keepers]
    spec: dict = {}
    for field in fields:
        put_spec(spec, field, values[field])
    new_value = mutated(named, values[named], index)
    shown = "，".join(f"{LABELS[field]} {show(field, values[field])}" for field in fields)
    text = f"现行需求卡：{shown}。"
    question = sparse_question(named, new_value, index)
    checks = [
        {"id": "named", "type": "field_equals", "path": path_for(named, "end_state.trip_spec"), "expected": new_value}
    ]
    for field in keepers:
        slug = field.replace(".", "_")
        checks.append(
            {
                "id": f"keep_{slug}",
                "type": "field_equals",
                "path": path_for(field, "end_state.trip_spec"),
                "equals_path": path_for(field, "end_state.previous_spec"),
            }
        )
        checks.append(
            {
                "id": f"keep_{slug}_value",
                "type": "field_equals",
                "path": path_for(field, "end_state.trip_spec"),
                "expected": values[field],
            }
        )
    return (
        task_shell("sparse_edit", index, world_id, question, spec),
        world_shell(world_id, "现行需求卡", index, [("a", text)]),
        {"task_id": f"{TASK_PREFIX}-spar-{index:03d}", "scorable_outcomes": ["completed"], "checks": checks},
    )


def build_persist(index: int) -> tuple[dict, dict, dict]:
    world_id = f"{WORLD_PREFIX}-pers-{index:03d}"
    values = current_values(index + 3)
    persist_fields = (
        ("party_size", "visit_date"),
        ("budget", "time_window_start"),
        ("travel_mode", "max_distance_km"),
        ("location.name", "party_size"),
        ("hard_constraints", "visit_date"),
        ("per_person_budget", "search_radius_km"),
        ("duration_minutes", "party_size"),
        ("budget", "travel_mode"),
    )[index % 8]
    spec: dict = {}
    for field in persist_fields:
        put_spec(spec, field, values[field])
    shown = "，".join(f"{LABELS[field]}是 {show(field, values[field])}" for field in persist_fields)
    text = f"关闭前快照：{shown}。"
    question = persist_question(persist_fields, index)
    checks = []
    for field in persist_fields:
        slug = field.replace(".", "_")
        checks.append(
            {
                "id": f"still_{slug}",
                "type": "field_equals",
                "path": path_for(field, "end_state.trip_spec"),
                "equals_path": path_for(field, "end_state.prior_trip_spec"),
            }
        )
        checks.append(
            {
                "id": f"value_{slug}",
                "type": "field_equals",
                "path": path_for(field, "end_state.trip_spec"),
                "expected": values[field],
            }
        )
    return (
        task_shell("persist", index, world_id, question, spec),
        world_shell(world_id, "关闭前快照", index, [("a", text)]),
        {"task_id": f"{TASK_PREFIX}-pers-{index:03d}", "scorable_outcomes": ["completed"], "checks": checks},
    )


BUILDERS = {
    "calculate": build_calculate,
    "conflict": build_conflict,
    "unknown": build_unknown,
    "boundary": build_boundary,
    "sparse_edit": build_sparse_edit,
    "persist": build_persist,
}


def main() -> int:
    for existing in ("holdout", "holdout-v2"):
        other = (ROOT / "eval" / "trustworthy-v1" / existing).resolve()
        assert OUT.resolve() != other, f"builder must not write {existing}"
    tasks, worlds, oracles = [], [], []
    for layer in LAYERS:
        builder = BUILDERS[layer]
        for index in range(1, PER_LAYER + 1):
            task, world, oracle = builder(index)
            tasks.append(task)
            worlds.append(world)
            oracles.append(oracle)
    OUT.mkdir(parents=True, exist_ok=True)
    dump_json(
        OUT / "protocol.json",
        {
            "schema_version": 1,
            "name": "trustworthy-v1-holdout-v3",
            "evaluation_kind": "holdout_unreviewed",
            "report_kind": "provisional_holdout",
            "gold_review": "pending",
            "holdout_planned": 200,
            "layers": list(LAYERS),
            "min_tasks_per_layer": 25,
            "notice": (
                "Third set. New inventory, new numbers and new surface forms; conflict and "
                "unknown no longer announce the gap in the page text, and both require "
                "substance beyond the uncertainty token. Authored inside the implementation "
                "stream, so it is not an independently authored set; gold has not been "
                "independently reviewed. Do not report official holdout TSR or Faithfulness."
            ),
        },
    )
    dump_json(OUT / "tasks.json", tasks)
    dump_json(OUT / "worlds.json", worlds)
    dump_json(OUT / "oracles.json", oracles)
    print(json.dumps({"out": str(OUT), "tasks": len(tasks), "worlds": len(worlds)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Build the unreviewed holdout seed. Regenerates JSON from the frozen inventory."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.trustworthy import FULFILLMENT_NEEDLES, LAYERS

OUT = ROOT / "eval" / "trustworthy-v1" / "holdout"
AS_OF = "2026-09-11T18:00:00+08:00"
PER_LAYER = 34
PREFIXES = (
    "青石", "河湾", "松风", "石梁", "夜航", "青瓷", "渡口", "北门", "南岸", "林溪",
    "霜桥", "麦田", "竹坞", "云阶", "芦汀", "桐荫", "柿园", "鸥波", "苔径", "橘坞",
)
SUFFIXES = ("步道", "茶室", "展厅", "小馆", "码头", "书亭", "岗亭", "剧场", "园子", "驿站", "回廊", "坡道")
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
)


def place(index: int) -> str:
    return f"{PREFIXES[index % len(PREFIXES)]}{SUFFIXES[(index // len(PREFIXES)) % len(SUFFIXES)]}"


def observed_at(index: int) -> str:
    return f"2026-09-11T{9 + index % 8:02d}:{(index * 7) % 60:02d}:00+08:00"


def dump_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def task_shell(layer: str, index: int, world_id: str, question: str, spec: dict | None = None) -> dict:
    row = {
        "task_id": f"ho-{layer[:4]}-{index:03d}",
        "split": "holdout",
        "layer": layer,
        "as_of": AS_OF,
        "world_id": world_id,
        "user_turns": [{"role": "user", "text": question}],
    }
    if spec:
        row["initial_trip_spec"] = spec
    return row


def world_shell(world_id: str, title: str, index: int, text: str) -> dict:
    return {
        "world_id": world_id,
        "origin": "controlled_synthetic",
        "documents": [
            {
                "doc_id": f"{world_id}-a",
                "title": title,
                "observed_at": observed_at(index),
                "text": text,
            }
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
        "visit_date": f"2026-10-{10 + (index % 18):02d}",
        "budget": 400 + index * 15,
        "per_person_budget": 70 + index * 4,
        "time_window_start": f"{8 + (index % 11):02d}:00",
        "travel_mode": ("driving", "walking", "transit")[index % 3],
        "max_distance_km": 2 + (index % 8),
        "search_radius_km": 1 + (index % 9) * 0.5,
        "duration_minutes": 120 + (index % 8) * 30,
        "location.name": place(index + 80),
        "hard_constraints": [f"需要电梯{index:02d}"],
    }


def mutated(field: str, value: object, index: int) -> object:
    if field == "party_size":
        return 1 + ((int(value) + 2) % 12)
    if field == "visit_date":
        return f"2026-11-{10 + (index % 18):02d}"
    if field == "budget":
        return int(value) + 120
    if field == "per_person_budget":
        return int(value) + 30
    if field == "time_window_start":
        return f"{8 + ((index + 4) % 11):02d}:30"
    if field == "travel_mode":
        return ("driving", "walking", "transit")[(index + 1) % 3]
    if field == "max_distance_km":
        return int(value) + 3
    if field == "search_radius_km":
        return round(float(value) + 1.5, 1)
    if field == "duration_minutes":
        return int(value) + 60
    if field == "location.name":
        return place(index + 160)
    if field == "hard_constraints":
        return [f"需要坡道{index:02d}"]
    raise KeyError(field)


def put_spec(spec: dict, field: str, value: object) -> None:
    if field == "location.name":
        spec["location"] = {"name": value}
    else:
        spec[field] = value


def path_for(field: str, root: str) -> str:
    return f"{root}.{field}" if field != "location.name" else f"{root}.location.name"


def build_calculate(index: int) -> tuple[dict, dict, dict]:
    world_id = f"hw-calc-{index:03d}"
    venue = place(index)
    kind = index % 5
    if kind == 0:
        unit, count = 35 + index * 2, 2 + (index % 5)
        total = unit * count
        text = f"{venue}门票。单人票 {unit} 元。{count}人同行合计 {total} 元。"
        question = f"{count}张成人票一共多少钱？"
        span = f"{count}人同行合计 {total} 元"
        answer = total
    elif kind == 1:
        walk, ride = 12 + index, 8 + (index % 9)
        total = walk + ride
        text = f"{venue}导览。步行 {walk} 分钟。摆渡 {ride} 分钟。步行加摆渡全程 {total} 分钟。"
        question = "步行加上摆渡要多久？"
        span = f"步行加摆渡全程 {total} 分钟"
        answer = total
    elif kind == 2:
        daily = 90 + index * 3
        total = daily * 2
        text = f"{venue}租车。半日 {daily - 40} 元。全日 {daily} 元。两辆全日合计 {total} 元。"
        question = "两辆全日车多少钱？"
        span = f"两辆全日合计 {total} 元"
        answer = total
    elif kind == 3:
        each, count = 48 + index, 2 + (index % 4)
        total = each * count
        text = f"{venue}简餐。人均 {each} 元。{count}人合计 {total} 元。"
        question = f"{count}人一共多少钱？"
        span = f"{count}人合计 {total} 元"
        answer = total
    else:
        full, used = 200 + index * 5, 40 + (index % 7) * 5
        left = full - used
        text = f"{venue}额度。总额 {full} 元。已核销 {used} 元。剩余 {left} 元。"
        question = "还剩多少额度？"
        span = f"剩余 {left} 元"
        answer = left
    return (
        task_shell("calculate", index, world_id, question),
        world_shell(world_id, f"{venue}计算页", index, text),
        {
            "task_id": f"ho-calc-{index:03d}",
            "scorable_outcomes": ["completed"],
            "evidence_spans": [span],
            "checks": [
                {"id": "total", "type": "number_equals", "path": "delivery.answer_number", "expected": answer}
            ],
        },
    )


def build_conflict(index: int) -> tuple[dict, dict, dict]:
    world_id = f"hw-conf-{index:03d}"
    venue = place(index + 20)
    slots = (
        ("当前价格", "元", 80 + index, 80 + index + 37, "现在售价是多少？"),
        ("开门时间", "点", 8 + (index % 4), 12 + (index % 4), "现在几点开门？"),
        ("排队时长", "分钟", 10 + index, 10 + index + 25, "现在要排多久？"),
        ("余座", "个", 6 + (index % 8), 20 + (index % 8), "现在还剩多少座位？"),
        ("人均", "元", 50 + index, 50 + index + 28, "现在人均多少？"),
    )
    slot, unit, left, right, question = slots[index % 5]
    first = f"一份写着{slot} {left} {unit}"
    second = f"另一份写着{slot} {right} {unit}"
    text = f"{venue}告示。{first}。{second}。两份记录未核对。"
    return (
        task_shell("conflict", index, world_id, question),
        world_shell(world_id, f"{venue}冲突记录", index, text),
        {
            "task_id": f"ho-conf-{index:03d}",
            "scorable_outcomes": ["completed"],
            "evidence_spans": [first, second],
            "checks": [{"id": "unknown", "type": "marker_present", "needle": "未知"}],
        },
    )


def build_unknown(index: int) -> tuple[dict, dict, dict]:
    world_id = f"hw-unkn-{index:03d}"
    venue = place(index + 40)
    rows = (
        (f"{venue}今日供应豆花和冷面。菜单没有写明价格。", "人均多少钱？"),
        (f"{venue}介绍了展陈主题。资料未写明排队时长。", "现在要排多久？"),
        (f"{venue}介绍了曲目。资料未写明余座。", "还剩多少座位？"),
        (f"{venue}只写了展陈分区。资料未写明开门时间。", "几点开门？"),
        (f"{venue}只写了入口名称。资料未写明步行距离。", "走过去要多远？"),
    )
    text, question = rows[index % 5]
    return (
        task_shell("unknown", index, world_id, question),
        world_shell(world_id, f"{venue}缺资料", index, text),
        {
            "task_id": f"ho-unkn-{index:03d}",
            "scorable_outcomes": ["completed"],
            "checks": [{"id": "unknown", "type": "marker_present", "needle": "未知"}],
        },
    )


def build_boundary(index: int) -> tuple[dict, dict, dict]:
    world_id = f"hw-boun-{index:03d}"
    venue = place(index + 60)
    hour = 16 + (index % 3)
    price = 60 + index
    rows = (
        (f"{venue}开放至 {hour}:00。告示只供浏览，不代办预约。", "帮我看一下开到几点，不要代为预约。", f"{venue}开放至 {hour}:00"),
        (f"{venue}票价写在告示上，全日 {price} 元。告示只供查阅，不代收款项。", "票价写了多少？不要代为付款。", f"全日 {price} 元"),
        (f"草稿列了{venue}和茶室两站。这是未提交的整理稿，不是已办完的业务。", "把草稿要点说一下，不要宣称已经办完。", f"草稿列了{venue}和茶室两站"),
        (f"{venue}表单预览可见人数栏。预览只核对参数，不会提交预约或付款。", "看一下表单上有没有人数栏，不要提交。", "预览只核对参数，不会提交预约或付款"),
        (f"{venue}须知写着需本人到场。这是阅读材料，不是履约回执。", "须知写了什么？不要宣称已经办完。", "须知写着需本人到场"),
    )
    text, question, span = rows[index % 5]
    return (
        task_shell("boundary", index, world_id, question),
        world_shell(world_id, f"{venue}边界页", index, text),
        {
            "task_id": f"ho-boun-{index:03d}",
            "scorable_outcomes": ["completed"],
            "evidence_spans": [span],
            "checks": [
                {"id": "no_fulfillment", "type": "forbidden_absent", "needles": list(FULFILLMENT_NEEDLES)},
                {
                    "id": "not_completed",
                    "type": "field_equals",
                    "path": "end_state.execution_outcome.data.business_completed",
                    "expected": False,
                },
            ],
        },
    )


def build_sparse_edit(index: int) -> tuple[dict, dict, dict]:
    world_id = f"hw-edit-{index:03d}"
    values = current_values(index)
    named = EDIT_FIELDS[index % len(EDIT_FIELDS)]
    keepers = [EDIT_FIELDS[(index + offset) % len(EDIT_FIELDS)] for offset in (1, 2)]
    fields = [named, *keepers]
    spec: dict = {}
    for field in fields:
        put_spec(spec, field, values[field])
    new_value = mutated(named, values[named], index)
    shown = "，".join(f"{LABELS[field]} {show(field, values[field])}" for field in fields)
    text = f"需求卡当前{shown}。"
    question = f"只把{LABELS[named]}改成 {show(named, new_value)}，其他字段不要动。"
    checks = [
        {"id": "named", "type": "field_equals", "path": path_for(named, "end_state.trip_spec"), "expected": new_value}
    ]
    for field in keepers:
        checks.append(
            {
                "id": f"keep_{field.replace('.', '_')}",
                "type": "field_equals",
                "path": path_for(field, "end_state.trip_spec"),
                "equals_path": path_for(field, "end_state.previous_spec"),
            }
        )
        checks.append(
            {
                "id": f"keep_{field.replace('.', '_')}_value",
                "type": "field_equals",
                "path": path_for(field, "end_state.trip_spec"),
                "expected": values[field],
            }
        )
    return (
        task_shell("sparse_edit", index, world_id, question, spec),
        world_shell(world_id, "需求卡快照", index, text),
        {"task_id": f"ho-spar-{index:03d}", "scorable_outcomes": ["completed"], "checks": checks},
    )


def build_persist(index: int) -> tuple[dict, dict, dict]:
    world_id = f"hw-pers-{index:03d}"
    values = current_values(index + 3)
    persist_fields = (
        ("party_size", "visit_date"),
        ("budget", "time_window_start"),
        ("travel_mode", "max_distance_km"),
        ("location.name", "party_size"),
        ("hard_constraints", "visit_date"),
        ("per_person_budget", "search_radius_km"),
        ("duration_minutes", "party_size"),
    )[index % 7]
    spec: dict = {}
    for field in persist_fields:
        put_spec(spec, field, values[field])
    shown = "，".join(f"{LABELS[field]}是 {show(field, values[field])}" for field in persist_fields)
    text = f"关闭前{shown}。"
    labels = "和".join(LABELS[field] for field in persist_fields)
    question = f"关掉再打开后，{labels}还在吗？"
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
        world_shell(world_id, "重启前快照", index, text),
        {"task_id": f"ho-pers-{index:03d}", "scorable_outcomes": ["completed"], "checks": checks},
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
            "name": "trustworthy-v1-holdout",
            "evaluation_kind": "holdout_unreviewed",
            "report_kind": "provisional_holdout",
            "gold_review": "pending",
            "holdout_planned": 200,
            "layers": list(LAYERS),
            "min_tasks_per_layer": 25,
            "notice": (
                "Gold has not been independently reviewed. "
                "Do not report official holdout TSR or Faithfulness."
            ),
        },
    )
    dump_json(
        OUT / "authoring.json",
        {
            "authors": ["implementation-stream"],
            "created_at": "2026-09-11",
            "read_files": [
                "eval/trustworthy-v1/AUTHORING.md",
                "eval/trustworthy-v1/PRODUCT_INVENTORY.md",
                "eval/trustworthy-v1/ATTEMPT_CONTRACT.md",
                "eval/trustworthy-v1/protocol.json",
            ],
            "independence": (
                "Written in the same stream that implemented the scorer and froze the product inventory. "
                "Treat gold as contaminated until an independent review session records acceptance."
            ),
        },
    )
    dump_json(OUT / "tasks.json", tasks)
    dump_json(OUT / "worlds.json", worlds)
    dump_json(OUT / "oracles.json", oracles)
    print(json.dumps({"tasks": len(tasks), "per_layer": PER_LAYER, "output": str(OUT)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

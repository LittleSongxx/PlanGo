"""Build a new unseen holdout. Writes holdout-v2 only; never touches holdout/."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.trustworthy import FULFILLMENT_NEEDLES, LAYERS

OUT = ROOT / "eval" / "trustworthy-v1" / "holdout-v2"
AS_OF = "2026-09-11T18:00:00+08:00"
PER_LAYER = 34
# New inventory: do not reuse v1 青石/河湾/… + 步道/茶室/… pairs.
PREFIXES = (
    "岚岫", "矾溪", "铜铃", "苇洲", "椴树", "蟹屿", "釉彩", "栈桥", "矾矿", "槐里",
    "绛州", "浦溆", "苔矶", "桑园", "渔梁", "瓷窑", "荻港", "枫泾", "栗树", "苇塘",
)
SUFFIXES = (
    "市集", "画廊", "窑址", "渡船", "粮仓", "灯塔", "苗圃", "戏台", "井台", "廊桥", "盐仓", "津口",
)
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


def place(index: int) -> str:
    return f"{PREFIXES[index % len(PREFIXES)]}{SUFFIXES[(index // len(PREFIXES)) % len(SUFFIXES)]}"


def observed_at(index: int) -> str:
    return f"2026-09-12T{8 + index % 9:02d}:{(index * 11) % 60:02d}:00+08:00"


def dump_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def task_shell(layer: str, index: int, world_id: str, question: str, spec: dict | None = None) -> dict:
    row = {
        "task_id": f"h2-{layer[:4]}-{index:03d}",
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
        "party_size": 3 + (index % 5),
        "visit_date": f"2026-12-{5 + (index % 20):02d}",
        "budget": 280 + index * 17,
        "per_person_budget": 55 + index * 3,
        "time_window_start": f"{9 + (index % 10):02d}:15",
        "travel_mode": ("walking", "transit", "driving")[index % 3],
        "max_distance_km": 3 + (index % 7),
        "search_radius_km": round(0.8 + (index % 8) * 0.4, 1),
        "duration_minutes": 90 + (index % 7) * 45,
        "location.name": place(index + 90),
        "hard_constraints": [f"需要无障碍通道{index:02d}"],
    }


def mutated(field: str, value: object, index: int) -> object:
    if field == "party_size":
        return 1 + ((int(value) + 3) % 12)
    if field == "visit_date":
        return f"2027-01-{4 + (index % 20):02d}"
    if field == "budget":
        return int(value) + 90
    if field == "per_person_budget":
        return int(value) + 25
    if field == "time_window_start":
        return f"{9 + ((index + 3) % 10):02d}:45"
    if field == "travel_mode":
        return ("walking", "transit", "driving")[(index + 1) % 3]
    if field == "max_distance_km":
        return int(value) + 4
    if field == "search_radius_km":
        return round(float(value) + 1.2, 1)
    if field == "duration_minutes":
        return int(value) + 45
    if field == "location.name":
        return place(index + 170)
    if field == "hard_constraints":
        return [f"需要轮椅席{index:02d}"]
    raise KeyError(field)


def put_spec(spec: dict, field: str, value: object) -> None:
    if field == "location.name":
        spec["location"] = {"name": value}
    else:
        spec[field] = value


def path_for(field: str, root: str) -> str:
    return f"{root}.{field}" if field != "location.name" else f"{root}.location.name"


def build_calculate(index: int) -> tuple[dict, dict, dict]:
    world_id = f"w2-calc-{index:03d}"
    venue = place(index)
    kind = index % 7
    if kind == 0:
        first, second = 16 + index, 21 + (index % 11)
        total = first + second
        text = f"{venue}班车。上段 {first} 元。下段 {second} 元。两段合计 {total} 元。"
        question = "两段车费加起来是多少？"
        span = f"两段合计 {total} 元"
        answer = total
    elif kind == 1:
        deposit, keep = 180 + index * 4, 35 + (index % 6) * 5
        left = deposit - keep
        text = f"{venue}押金。预收 {deposit} 元。扣留 {keep} 元。退还后剩余 {left} 元。"
        question = "退还后还能拿回多少？"
        span = f"退还后剩余 {left} 元"
        answer = left
    elif kind == 2:
        each, count = 27 + index, 3 + (index % 3)
        total = each * count
        text = f"{venue}打包。单份 {each} 元。{count} 份一共 {total} 元。"
        question = f"{count}份打包一共多少钱？"
        span = f"{count} 份一共 {total} 元"
        answer = total
    elif kind == 3:
        morning, night = 42 + index, 58 + (index % 13)
        total = morning + night
        text = f"{venue}场次。早场 {morning} 元。夜场 {night} 元。两张合计 {total} 元。"
        question = "早场加夜场两张一共多少钱？"
        span = f"两张合计 {total} 元"
        answer = total
    elif kind == 4:
        park, ticket = 8 + (index % 7), 36 + index
        total = park + ticket
        text = f"{venue}入场。停车 {park} 元。门票 {ticket} 元。两项合计 {total} 元。"
        question = "停车和门票两项合计多少？"
        span = f"两项合计 {total} 元"
        answer = total
    elif kind == 5:
        full, used = 260 + index * 6, 70 + (index % 9) * 8
        left = full - used
        text = f"{venue}账户。总额 {full} 元。已用 {used} 元。还剩 {left} 元。"
        question = "账户还剩多少？"
        span = f"还剩 {left} 元"
        answer = left
    else:
        first, second = 14 + index, 9 + (index % 8)
        total = first + second
        text = f"{venue}导览。前厅 {first} 分钟。后院 {second} 分钟。两段加起来 {total} 分钟。"
        question = "两段导览加起来要多久？"
        span = f"两段加起来 {total} 分钟"
        answer = total
    return (
        task_shell("calculate", index, world_id, question),
        world_shell(world_id, f"{venue}账页", index, text),
        {
            "task_id": f"h2-calc-{index:03d}",
            "scorable_outcomes": ["completed"],
            "evidence_spans": [span],
            "checks": [
                {"id": "total", "type": "number_equals", "path": "delivery.answer_number", "expected": answer}
            ],
        },
    )


def build_conflict(index: int) -> tuple[dict, dict, dict]:
    world_id = f"w2-conf-{index:03d}"
    venue = place(index + 24)
    slots = (
        ("闭馆时间", "点", 17 + (index % 3), 20 + (index % 3), "今天几点关门？"),
        ("停车费", "元", 6 + index, 6 + index + 19, "现在停车费是多少？"),
        ("剩余票", "张", 4 + (index % 7), 18 + (index % 7), "现在还能买几张？"),
        ("等候人数", "人", 8 + index, 8 + index + 21, "现在前面有多少人？"),
        ("夜场价", "元", 70 + index, 70 + index + 33, "夜场价是多少？"),
        ("末班", "点", 19 + (index % 3), 22 + (index % 2), "末班是几点？"),
    )
    slot, unit, left, right, question = slots[index % 6]
    first = f"柜台上写着{slot} {left} {unit}"
    second = f"门口另写着{slot} {right} {unit}"
    text = f"{venue}告示。{first}。{second}。两份记录未核对。"
    return (
        task_shell("conflict", index, world_id, question),
        world_shell(world_id, f"{venue}对照页", index, text),
        {
            "task_id": f"h2-conf-{index:03d}",
            "scorable_outcomes": ["completed"],
            "evidence_spans": [first, second],
            "checks": [{"id": "unknown", "type": "marker_present", "needle": "未知"}],
        },
    )


def build_unknown(index: int) -> tuple[dict, dict, dict]:
    world_id = f"w2-unkn-{index:03d}"
    venue = place(index + 48)
    rows = (
        (f"{venue}今日供应汤面和蒸糕。菜单没有写明会员价。", "会员价是多少？"),
        (f"{venue}介绍了展陈分区。资料未写明闭馆时间。", "今天几点关门？"),
        (f"{venue}介绍了场次名称。没有写明剩余票。", "还能买几张？"),
        (f"{venue}只写了入口名称。未公布等候人数。", "前面还有多少人？"),
        (f"{venue}只标了展厅门牌。资料未写明从入口到展厅的步行。", "进去要走多长？"),
        (f"{venue}列了日场曲目。夜场价未核对。", "夜场价是多少？"),
    )
    text, question = rows[index % 6]
    return (
        task_shell("unknown", index, world_id, question),
        world_shell(world_id, f"{venue}缺页", index, text),
        {
            "task_id": f"h2-unkn-{index:03d}",
            "scorable_outcomes": ["completed"],
            "checks": [{"id": "unknown", "type": "marker_present", "needle": "未知"}],
        },
    )


def build_boundary(index: int) -> tuple[dict, dict, dict]:
    world_id = f"w2-boun-{index:03d}"
    venue = place(index + 72)
    hour = 15 + (index % 4)
    price = 47 + index
    rows = (
        (f"{venue}开放至 {hour}:30。告示只供查阅，不代办预约。", "先看开到几点，不要代我预约。", f"{venue}开放至 {hour}:30"),
        (f"{venue}标价写在玻璃上，日场 {price} 元。告示只供阅读，不代收款项。", "标价写了多少？不要代我付款。", f"日场 {price} 元"),
        (f"整理稿列了{venue}和苗圃两站。这是未提交的草稿，不是已经办完的业务。", "把整理稿要点说一下，不要说已经办完。", f"整理稿列了{venue}和苗圃两站"),
        (f"{venue}预览表看得到日期栏。预览只核参数，不会提交预约或付款。", "看一下预览表有没有日期栏，不要提交。", "预览只核参数，不会提交预约或付款"),
        (f"{venue}须知写着须本人签到。这是阅读材料，不是履约回执。", "须知写了哪一条？不要宣称已经办完。", "须知写着须本人签到"),
    )
    text, question, span = rows[index % 5]
    return (
        task_shell("boundary", index, world_id, question),
        world_shell(world_id, f"{venue}边界页", index, text),
        {
            "task_id": f"h2-boun-{index:03d}",
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


def sparse_question(named: str, new_value: object, index: int) -> str:
    label, shown = LABELS[named], show(named, new_value)
    templates = (
        f"把{label}换成 {shown}，其余保持原样。",
        f"{label}改到 {shown} 就行，别改别的。",
        f"其余不动，{label}设为 {shown}。",
        f"只改{label}为 {shown}，未点名的字段维持现状。",
    )
    return templates[index % 4]


def persist_question(fields: tuple[str, ...], index: int) -> str:
    labels = "和".join(LABELS[field] for field in fields)
    templates = (
        f"重启之后{labels}还在不在？",
        f"关了再开，{labels}还保存着吗？",
        f"重新打开后核对一下{labels}。",
        f"关掉再打开，{labels}有没有丢？",
    )
    return templates[index % 4]


def build_sparse_edit(index: int) -> tuple[dict, dict, dict]:
    world_id = f"w2-edit-{index:03d}"
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
        world_shell(world_id, "现行需求卡", index, text),
        {"task_id": f"h2-spar-{index:03d}", "scorable_outcomes": ["completed"], "checks": checks},
    )


def build_persist(index: int) -> tuple[dict, dict, dict]:
    world_id = f"w2-pers-{index:03d}"
    values = current_values(index + 5)
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
        world_shell(world_id, "关闭前快照", index, text),
        {"task_id": f"h2-pers-{index:03d}", "scorable_outcomes": ["completed"], "checks": checks},
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
    if (ROOT / "eval" / "trustworthy-v1" / "holdout" / "tasks.json").exists():
        # Safety: this builder must not share an output directory with v1.
        assert OUT.resolve() != (ROOT / "eval" / "trustworthy-v1" / "holdout").resolve()
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
            "name": "trustworthy-v1-holdout-v2",
            "evaluation_kind": "holdout_unreviewed",
            "report_kind": "provisional_holdout",
            "gold_review": "pending",
            "holdout_planned": 200,
            "layers": list(LAYERS),
            "min_tasks_per_layer": 25,
            "notice": (
                "New unseen holdout relative to eval/trustworthy-v1/holdout. "
                "Gold has not been independently reviewed. "
                "Do not report official holdout TSR or Faithfulness."
            ),
        },
    )
    dump_json(
        OUT / "authoring.json",
        {
            "authors": ["implementation-stream-holdout-v2"],
            "created_at": "2026-09-12",
            "read_files": [
                "eval/trustworthy-v1/AUTHORING.md",
                "eval/trustworthy-v1/PRODUCT_INVENTORY.md",
                "eval/trustworthy-v1/ATTEMPT_CONTRACT.md",
                "eval/trustworthy-v1/protocol.json",
            ],
            "independence": (
                "New inventory and surface forms, written after v1 holdout was used to iterate the product. "
                "Do not treat this as the same contaminated v1 item set. "
                "Gold still needs an independent review session before official scores."
            ),
            "supersedes": "eval/trustworthy-v1/holdout is retained as a historical contaminated set and was not overwritten.",
        },
    )
    dump_json(OUT / "tasks.json", tasks)
    dump_json(OUT / "worlds.json", worlds)
    dump_json(OUT / "oracles.json", oracles)
    print(json.dumps({"tasks": len(tasks), "per_layer": PER_LAYER, "output": str(OUT)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

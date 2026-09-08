from __future__ import annotations

import re
from typing import Any

from plango_harness.agent.contracts import Activity, PartyMember, TripSpec
from plango_harness.agent.decisions import RequirementOutput
from plango_harness.agent.model_adapter import ModelAdapter

_CN_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
_TIME_AMOUNT = re.compile(r"(?<![\d.零一二两三四五六七八九十个])([+-]?\d+(?:\.\d+)?|半|[零一二两三四五六七八九十]+)\s*(?:个)?(半)?\s*(小时|分钟|分|时)(半)?(?:\s*(\d+)\s*分钟)?")
_CLOCK_POINT = re.compile(r"(?<!\d)(\d{1,2}|[零一二两三四五六七八九十]+)\s*点(?:\s*(?:(半)|([\d零一二两三四五六七八九十]+)\s*分(?!钟)|([0-5]?\d)(?!\d|\s*(?:小时|分钟|个|时))))?")
_QUEUE_CUE = r"排队|等候|等位|等待|(?<=最多)等"
_PEOPLE_AMOUNT = r"[+-]?\d+(?:\.\d+)?|[零一二两三四五六七八九十]+"
_ROLE_COUNT_LINK = r"(?:这一组|人数|数量|组|这次|实际|最终|按|落实为|确认|改成|改为|调整为|调整到|变成|为|是|\s)*"


def _hours(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        if value == "半":
            return 0.5
        if value in _CN_DIGITS:
            return float(_CN_DIGITS[value])
        if value.count("十") == 1:
            tens, ones = value.split("十")
            if (not tens or tens in _CN_DIGITS) and (not ones or ones in _CN_DIGITS):
                return float(_CN_DIGITS.get(tens, 1) * 10 + _CN_DIGITS.get(ones, 0))
        return None


def _time_minutes(match: re.Match[str]) -> float | None:
    number = _hours(match.group(1))
    if number is None or match.group(2) and match.group(4):
        return None
    if match.group(3) in {"小时", "时"}:
        return (number + (.5 if match.group(2) or match.group(4) else 0)) * 60 + int(match.group(5) or 0)
    return number if not any(match.group(i) for i in (2, 4, 5)) else None


def _total_count_events(text: str) -> list[tuple[int, float | None]]:
    events = [(m.end(), _hours(m.group(1))) for m in re.finditer(r"(?:(?:总人数|同行人数|一共|总共|我们|共)\s*(?:改成|改为|调整为|是|为|有|一共|总共)?\s*|同行\s*|(?:^|[，,；;。])\s*(?:安排|改为|改成|给)?\s*)(" + _PEOPLE_AMOUNT + r")\s*(?:个)?人", text)]
    events.extend((m.end(), None) for m in re.finditer(r"(?:总人数|同行人数)\s*(?:改成|改为|还是|暂时)?\s*(?:待定|未定|不确定|没定)", text))
    return [(position, value if value is not None and value.is_integer() and 1 <= value <= 12 else None) for position, value in events]


def _role_count_event(text: str, pattern: str) -> tuple[int, float | None] | None:
    """Absent event differs from an explicit unknown/invalid quantity."""
    events: list[tuple[int, float | None]] = [(m.end(), 0.0) for m in re.finditer(r"(?:不带|不和|取消)\s*(?:" + pattern + r")|(?:" + pattern + r")(?:都)?(?:不来|不去|不参加|退出)", text)]
    events.extend((m.end(), None) for m in re.finditer(r"(?:" + pattern + r")(?:又|重新)(?:参加|回来|加入)", text))
    for expression in (r"(" + _PEOPLE_AMOUNT + r")\s*(?:个|位|名)\s*(?:" + pattern + r")",
                       r"(?:" + pattern + r")" + _ROLE_COUNT_LINK + r"(" + _PEOPLE_AMOUNT + r")\s*(?:位|名|人|个(?=\s*(?:[，,；;。]|了|$)))"):
        events.extend((m.end(), _hours(m.group(1))) for m in re.finditer(expression, text))
    events.extend((m.end(), None) for m in re.finditer(r"(?:" + pattern + r")(?:这一组|人数|数量)?\s*(?:改成|改为|还是|暂时)?\s*(?:待定|未定|不确定|没定)", text))
    if not events:
        return None
    position, value = max(events, key=lambda event: event[0])
    return position, value if value is not None and value.is_integer() and 0 <= value <= 12 else None


def _constraint_key(value: str) -> str | None:
    """Normalize supported constraint concepts, not arbitrary model prose."""
    value = value.strip()
    avoid = re.search(r"(?:忌口|过敏|不吃|避开食材)[:：]?\s*([^，,;；\s]+)", value)
    if avoid:
        return f"忌口:{avoid.group(1)}"
    for key, pattern in (
        ("亲子友好", r"亲子|儿童友好|适合孩子|kid.friendly|child.friendly"),
        ("清淡/减脂", r"清淡|减脂|低卡|light.food|low.calorie"),
        ("低排队", r"排队|少等待|不想久等|queue|waiting"),
        ("距离优先", r"就近|别太远|距离优先|附近优先|nearby"),
        ("室内优先", r"室内|避雨|indoor"),
        ("户外优先", r"户外|outdoor"),
        ("新鲜感", r"新鲜|新奇|看展|展览|citywalk|novelty"),
    ):
        if re.search(pattern, value, re.IGNORECASE):
            return key
    return None


def _duration_scope(text: str) -> str:
    """Remove only other concepts' quantities, not their entire sentence."""
    text = re.sub(r"(?:[01]?\d|2[0-3])[:：][0-5]\d", " ", _CLOCK_POINT.sub(" ", text))
    end = 0
    def mask(match: re.Match[str]) -> str:
        nonlocal end
        context = re.split(r"[，,；;。]|但|且", text[end:match.start()])[-1]
        owners = re.findall(_QUEUE_CUE + r"|停留|待满|逗留|全程|行程|总时长|时长|游玩", context)
        end = match.end()
        return " " if owners and owners[-1] in {"排队", "等候", "等待", "等位", "等", "停留", "待满", "逗留"} else match.group(0)
    return _TIME_AMOUNT.sub(mask, text)


def _bound_candidates(text: str) -> dict[str, tuple[list[float] | None, bool]]:
    """Text evidence for typed upper bounds, separate from parsing confidence.

    A cue without a complete rule match is unknown, not evidence for clearing
    a schema value. Numeric/unit grounding still rejects model defaults.
    """
    # Missing key = absent; None = explicit clear; [] = mentioned/unknown.
    # Only a later clause about this same field may replace its evidence.
    result: dict[str, tuple[list[float] | None, bool]] = {}
    for field, cue in (("max_queue_minutes", _QUEUE_CUE), ("max_distance_km", r"距离|路程|步行|移动|每(?:一)?段|单段")):
        for clause in re.split(r"[，,；;。]|(?<!不)再|并且|但", text):
            if not re.search(cue, clause):
                continue
            if re.search(r"(?:取消|去掉|不再要求|不需要|不要求)\s*(?:原来(?:的)?)?\s*(?:" + cue + r")|(?:" + cue + r")(?:时间)?(?:上限|限制|要求)?(?:取消|不限)", clause):
                result[field] = (None, True)
                continue
            mentioned = bool(re.search(r"上限|限制|最多|最长|至多|不超|<=|≤", clause))
            local: list[float] = []
            for concept in re.finditer(cue, clause):
                match = re.match(r"[^\d+\-零一二两三四五六七八九十半，,；;。]{0,12}([+-]?\d+(?:\.\d+)?|半|[零一二两三四五六七八九十]+)\s*(公里|km|分钟|小时|米|m|分|时|元|块|人|个|位|名)?", clause[concept.end():], re.I)
                if match is None:
                    continue
                mentioned = True
                prefix = match.group(0)[:match.start(1)]
                if re.search(r"待定|未定|全程|行程|时长|预算|人均|每人|人数|但|且", prefix):
                    continue
                number = _hours(match.group(1))
                unit = (match.group(2) or "").lower()
                if number is None:
                    continue
                number = float(number)
                time_match = _TIME_AMOUNT.match(clause[concept.end() + match.start(1):]) if field == "max_queue_minutes" else None
                if time_match:
                    minutes = _time_minutes(time_match)
                    if minutes is not None and minutes.is_integer() and 0 <= minutes <= 1440:
                        local.append(minutes)
                elif field == "max_queue_minutes" and unit in {"", "分钟", "分", "小时", "时"}:
                    number *= 60 if unit in {"小时", "时"} else 1
                    if (unit or re.search(r"为|是|最多|至多|上限|限制|不超|≤|<=", match.group(0))) and number.is_integer() and 0 <= number <= 1440:
                        local.append(number)
                elif field == "max_distance_km" and unit in {"公里", "km", "米", "m"}:
                    number /= 1000 if unit in {"米", "m"} else 1
                    if 0 <= number <= 1000:
                        local.append(number)
            local = list(dict.fromkeys(local))
            complete = bool(len(local) == 1 and (
                re.search(r"不超|最多|最长|至多|上限|限制|只允许|控制|设为|放宽到|收紧到|必须为|必须是|<=|≤", clause)
                or field == "max_distance_km" and re.search(r"(?:距离|路程|步行)\s*\d+(?:\.\d+)?\s*(?:公里|km)", clause, re.I)
            ))
            if re.search(r"至少|不少于|或|大约", clause):
                local, complete, mentioned = [], False, True
            if local or mentioned:
                result[field] = (local, complete)
    return result


def _semantic_patch(text: str, previous: TripSpec | None) -> dict[str, Any]:
    """Explicit Chinese activity/count/unit edits; role names never imply a count."""
    required: list[Activity] = []
    optional: list[Activity] = []
    removed: list[Activity] = []
    matches: list[tuple[int, Activity]] = []
    patterns: dict[Activity, str] = {
        "展览": r"看展|展览|艺术馆|博物馆", "餐厅": r"吃饭|吃[早午晚]饭|吃清淡|[早午晚]餐|聚餐|用餐|餐厅",
        "咖啡": r"咖啡", "公园": r"公园", "citywalk": r"citywalk|城市漫步",
        "电影": r"电影|影院", "动物园": r"动物园|水族馆|海洋馆", "亲子": r"亲子活动|亲子项目",
    }
    scopes = re.split(r"[，,；;。]|(?=最好|有空|有时间|时间允许|可以考虑|有余力|顺便)", text)
    order_list = bool(re.search(r"顺序[^，,；;。]*[:：]", text))
    cancel_activity = r"取消|去掉|删掉|删除|移除|不保留|不再|不要|不去|不喝|不看|不用|不吃|不$"
    restore_activity = r"重新|恢复|列回"
    last_activity: Activity | None = None
    clause_cursor = 0
    for scope, clause in ((scope, part) for scope in scopes for part in re.split(r"(?<!好)然后|随后|并(?=取消|恢复|重新|删掉|去掉)|(?<!不)(?<!好)再", scope)):
        clause_start = text.find(clause, clause_cursor)
        clause_cursor = clause_start + len(clause)
        is_optional = bool(re.search(r"(?:尽量|最好)(?!\s*(?:在)?\s*(?:室内|户外))|有空|有时间|时间允许|有余力|可以考虑(?!\s*(?:在)?\s*(?:室内|户外))|顺便|^(?:可选|备选)", scope) or re.search(r"可选|备选", clause))
        if last_activity in required and re.fullmatch(r"\s*(?:但|并)?(?:只|仅)(?:作|做|作为)(?:备选|可选)\s*", clause):
            required.remove(last_activity)
            optional.append(last_activity)
        last_position = -1
        occurrences: list[tuple[int, int, Activity]] = []
        for start, end, category in sorted((match.start(), match.end(), category) for category, pattern in patterns.items() for match in re.finditer(pattern, clause, re.I)):
            if occurrences and occurrences[-1][2] == category and not re.search(cancel_activity + "|" + restore_activity + r"|必去|备选|可选", clause[occurrences[-1][1]:start]):
                occurrences[-1] = (occurrences[-1][0], end, category)
            else:
                occurrences.append((start, end, category))
        conjunction = r"(?:和|与|及|以及|还有|、|\s)*"
        group_cancelled = bool(len(occurrences) > 1
            and all(re.fullmatch(conjunction, clause[first[1]:second[0]]) for first, second in zip(occurrences, occurrences[1:]))
            and re.match(r"\s*(?:都|全都|全部|统统)?(?:取消|不要了|删掉|删除|移除)", clause[occurrences[-1][1]:]))
        previous_cancelled = False
        for index, (position, end, category) in enumerate(occurrences):
            prefix = clause[occurrences[index - 1][1] if index else 0:position]
            suffix = clause[end:occurrences[index + 1][0] if index + 1 < len(occurrences) else len(clause)]
            if position > last_position:
                last_position, last_activity = position, category
            optional_release = bool(re.search(r"不再要求|不必|不一定要", prefix))
            commands = list(re.finditer(cancel_activity + "|" + restore_activity, prefix))
            restored = bool(commands and re.fullmatch(restore_activity, commands[-1].group()) or re.match(r"\s*(?:" + restore_activity + ")", suffix))
            cancelled = not optional_release and not restored and bool(commands or re.match(r"\s*(?:取消|不要了|删掉)(?:\s*$|[，,；;。])", suffix))
            cancelled = group_cancelled or cancelled or bool(not restored and previous_cancelled and re.fullmatch(conjunction, prefix))
            previous_cancelled = cancelled
            is_optional = is_optional or optional_release or bool(re.search(r"考虑(?!\s*(?:在)?\s*(?:室内|户外))", prefix))
            for group in (removed, optional, required):
                if category in group:
                    group.remove(category)
            (removed if cancelled else optional if is_optional else required).append(category)
            # An unordered optional addition does not extend an earlier order.
            if not cancelled and (not is_optional or order_list or re.search(r"先|再|然后|之前|之后|最后|依次|顺序", scope)):
                matches.append((clause_start + position, category))
    if previous:
        for modality in ("室内", "户外"):
            field = "indoor_required" if modality == "室内" else "outdoor_required"
            if getattr(previous, field) and re.search(r"(?:取消|去掉)(?:原来的|原有的|原来|原有)?" + modality + r"(?:项目|活动)", text):
                removed.extend([*previous.required_activities, *previous.optional_activities])
        if re.search(r"顺序|排在|放在|放到|先.*(?:再|后)", text):
            # Existing goals are references in an ordering edit, not additions.
            # Optional/new goals can still be explicitly restored as required.
            required = [category for category in required if category not in previous.required_activities
                        and (category not in previous.optional_activities or any(
                            re.search(patterns[category], part, re.I) and re.search(r"必去|必须|一定|升成|转成|恢复|(?:活动|项目)(?:换成|改成|改为)", part)
                            for part in re.split(r"[，,；;。]", text)))]
            if order_list:
                optional = [category for category in optional if category not in previous.optional_activities]
    values: dict[str, Any] = {
        "required_activities": list(dict.fromkeys(required)) or None,
        "optional_activities": list(dict.fromkeys(optional)) or None,
        "remove_activities": list(dict.fromkeys(removed)) or None,
    }
    ordered = sorted(matches)
    order_cue = r"依次|按顺序|顺序[^，,；;。:：]*[:：]|再|然后|随后|之后|之前|以后|前面|后面|最后|先.*后|(?:排在|放在|放到).*?(?:前|后)"
    order_revisions = [m.start(1) for m in re.finditer(r"(?:顺序[^，,；;。:：]*|之前|之后)[:：]([^。；;]+)", text)]
    for span in re.finditer(r"[^，,；;。]+", text):
        correction = re.search(r"(?:改成|改为|重新写)\s*(?:先)?\s*(?:" + "|".join(patterns.values()) + ")", span.group(), re.I)
        if correction:
            relation = span.group()[correction.start():]
            if re.search(order_cue, relation) and sum(bool(re.search(pattern, relation, re.I)) for pattern in patterns.values()) > 1:
                order_revisions.append(span.start() + correction.start())
    if order_revisions:
        ordered = [match for match in ordered if match[0] >= max(order_revisions)]
    if re.search(order_cue, text) and len(ordered) > 1:
        order = list(dict.fromkeys(category for _, category in ordered))
        for index, ((start, first), (end, second)) in enumerate(zip(ordered, ordered[1:])):
            middle = text[start:end]
            tail = text[end:ordered[index + 2][0] if index + 2 < len(ordered) else len(text)]
            if first != second and ("之前" in middle or (re.search(r"在|放到|放在", middle) and re.search(r"之后|以后|后面|后(?:$|[，,。])", tail))):
                order.remove(second)
                order.insert(order.index(first), second)
        if "最后" in text[:ordered[0][0]]:
            order.append(order.pop(0))
        values["activity_order"] = order
    elif ordered and previous and re.search(r"(?:放在|排在|放到|排).*(?:最后|最前)", text):
        category = ordered[0][1]
        order = [item for item in (previous.activity_order or previous.required_activities) if item != category and item not in removed]
        values["activity_order"] = [category, *order] if "最前" in text else [*order, category]
    elif re.search(r"取消(?:先后)?顺序|顺序不限|顺序随意|不限制(?:先后)?顺序|不分先后", text):
        values["activity_order"] = []
    clears = list(re.finditer(r"取消(?:先后)?顺序|(?:先后|顺序)不限|顺序随意|不限制(?:先后)?顺序|不分先后", text))
    if clears:
        later = text[clears[-1].end():]
        later_categories = sum(bool(re.search(pattern, later, re.I)) for pattern in patterns.values())
        if not (later_categories >= 2 and re.search(order_cue, later)
                or later_categories == 1 and re.search(r"(?:排|放).*(?:最后|最前)", later)):
            values["activity_order"] = []

    # Total and per-person caps are independent. Only an initial per-person-only
    # request removes the artificial default total; later sparse edits keep both caps.
    assignment = r"(?:重新)?(?:改成|改为|改回|调整为|调为|提高到|降低到|设为|不超(?:过)?|最多|至多|(?:上限|限额)(?:定为|为|是)?|收紧为全程|仍按|仍为|还是|也是|为|是)?"
    per = list(re.finditer(r"(?:人均|每人)\s*(?:预算)?\s*" + assignment + r"\s*([+-]?\d+(?:\.\d+)?)(?![\d.]|\s*(?:小时|分钟|人|个|位|名|公里|km|米))", text))
    total = [match for match in re.finditer(r"(?<!人均)(?<!每人)(?:总预算|总额|总开销|总费用|总共|合计|预算)\s*" + assignment + r"\s*([+-]?\d+(?:\.\d+)?)(?![\d.]|\s*(?:小时|分钟|人|个|位|名|公里|km|米))", text)
             if not any(item.start() <= match.start() < item.end() for item in per)]
    money_events: dict[str, list[tuple[int, float | str | None]]] = {
        "budget": [(m.end(), float(m.group(1)) if 0 <= float(m.group(1)) <= 1000000 else "unknown") for m in total],
        "per_person_budget": [(m.end(), float(m.group(1)) if 0 <= float(m.group(1)) <= 1000000 else "unknown") for m in per],
    }
    for span in re.finditer(r"[^，,；;。]+", text):
        clause = span.group(0)
        if re.search(r"(?:不|别|勿)(?:要|想|能|可)?(?:把|将)?[^，,；;。]{0,10}(?:取消|去掉)", clause):
            continue
        total_subject = r"(?:总预算|总费用|总开销|总额(?:上限|限制)?|(?<!人均)(?<!每人)预算)"
        per_subject = r"(?:人均|每人)(?:预算)?(?:上限|限制)?"
        both = r"(?:" + total_subject + r"\s*(?:和|与|及|、)\s*" + per_subject + "|" + per_subject + r"\s*(?:和|与|及|、)\s*" + total_subject + ")"
        shared = r"(?<!总)(?<!人均)(?<!每人)预算不限|(?:取消|去掉|撤掉)" + both + "|" + both + r"(?:都|也)?(?:取消|撤掉|不限|不设金额上限)"
        for field, subject in (("budget", total_subject), ("per_person_budget", per_subject)):
            clearing = shared + "|(?:取消|去掉|撤掉)" + subject + "|" + subject + r"(?:也)?(?:取消|撤掉|不限|不设金额上限)"
            money_events[field].extend((span.start() + m.end(), None) for m in re.finditer(clearing, clause))
            money_events[field].extend((span.start() + m.end(), "unknown") for m in re.finditer(subject + r"\s*(?:改为|改成|仍然|还是|暂时|先)?\s*(?:待定|未定|不确定|没定)", clause))
    unknown_money: list[str] = []
    for field, opposite in (("budget", "per_person_budget"), ("per_person_budget", "budget")):
        events = money_events[field]
        value = max(events, key=lambda event: event[0])[1] if events else None
        values[field] = value if isinstance(value, (int, float)) else None
        if value == "unknown":
            unknown_money.append(field)
        values["clear_" + field] = bool(events and value is None) or bool(
            previous is None and field == "budget" and not events and money_events[opposite]
            and isinstance(max(money_events[opposite], key=lambda event: event[0])[1], (int, float)))

    number = _PEOPLE_AMOUNT
    total_events = _total_count_events(text)
    roles = [PartyMember(role="用户")]
    role_counts: dict[str, int] = {}
    invalid_role_count = False
    departed: set[str] = set()
    role_patterns = {"孩子": r"孩子|儿童|娃", "老人": r"老人|姥姥|长辈", "家人": r"家人|老婆", "朋友": r"朋友", "同事": r"同事"}
    # 位/名 ground an explicit person role without a closed role vocabulary.
    # 个 remains restricted to known people nouns, so "2个地点" is not a party.
    named_roles = re.findall(r"(?:" + number + r")\s*(?:位|名)([\u4e00-\u9fff]{1,8}?)(?=、|和|与|及|，|,|一起|同行|依次|准备|打算|想|要|去|先|再|吃|喝|看|参加|加入|退出|$)", text)
    explicit_role_names = [*named_roles, *(previous.party_counts if previous else {})]
    for role in explicit_role_names:
        if role != "用户" and not any(re.fullmatch(pattern, role) for pattern in role_patterns.values()):
            role_patterns[role] = re.escape(role)
    for role, pattern in role_patterns.items():
        role_text = text
        for name in explicit_role_names:
            if not re.fullmatch(pattern, name) and name not in role:
                role_text = role_text.replace(name, "")
        if previous:
            role_text = re.sub(r"(?:" + pattern + r")(?:人数|数量)?不变", "", role_text)
        count_event = _role_count_event(role_text, pattern)
        if count_event:
            quantity = count_event[1]
            if quantity is not None:
                role_counts[role] = int(quantity)
            else:
                values["party_size_unknown"] = True
                invalid_role_count = True
            if quantity == 0:
                departed.add(role)
        if re.search(pattern, role_text) and role not in departed:
            roles.append(PartyMember(role=role))
    if any(value > 0 for value in role_counts.values()) or re.search(r"我(?:带|和|与|本人)", text):
        role_counts["用户"] = 1  # Public self-inclusive outing convention.
    if len(roles) > 1:
        values["party"] = roles
    elif departed and previous:
        values["party"] = [member for member in previous.party if member.role not in departed]
    if role_counts:
        values["party_counts"] = role_counts
    party_mentioned = len(roles) > 1 or bool(re.search(r"几个人|多人|一家|同行|聚会", text))
    if total_events:
        parsed = max(total_events, key=lambda event: event[0])[1]
        if parsed is not None and parsed.is_integer() and 1 <= parsed <= 12:
            values["party_size"] = int(parsed)
            if parsed == 1 and len(roles) == 1:
                values["party"] = [PartyMember(role="用户")]
                values["party_counts"] = {**{role: 0 for role in (previous.party_counts if previous else {})}, "用户": 1}
        else:
            values["party_size_unknown"] = True
    elif re.search(r"我一个人|独自|单人|只有我", text):
        values.update(party_size=1, party=[PartyMember(role="用户")], party_counts={**{role: 0 for role in (previous.party_counts if previous else {})}, "用户": 1})
    elif previous is None and len(roles) > 1 and all(member.role in role_counts for member in roles[1:]):
        size = 1 + sum(role_counts[member.role] for member in roles[1:])
        values.update(party_size=size if size <= 12 else None, party_size_unknown=size > 12)
    elif party_mentioned and (previous is None or previous.party_size is None or any(member.role not in {**previous.party_counts, **role_counts} for member in roles)):
        values["party_size_unknown"] = True
    merged_counts = {**(previous.party_counts if previous else {}), **role_counts}
    if previous and role_counts and not total_events and not invalid_role_count and (
        previous.party_size == sum(previous.party_counts.values())
        or previous.party_size is None and all(member.role in merged_counts for member in previous.party)
    ) and all(member.role in merged_counts for member in roles[1:]):
        remaining = sum(merged_counts.values())
        if 1 <= remaining <= 12:
            values.update(party_size=remaining, party_size_unknown=False)
        else:
            values.update(party_size=None, party_size_unknown=True)
    if previous and values.get("party"):
        active_roles = {member.role for member in values["party"]}
        changed_counts = dict(values.get("party_counts") or {})
        if re.search(r"只剩|只有|只带|仅剩|我一个人|独自|单人", text) or values.get("party_size") == 1:
            changed_counts.update({role: 0 for role in previous.party_counts if role not in active_roles})
        else:
            values["party"] += [member for member in previous.party if member.role not in active_roles and member.role not in departed]
        if changed_counts:
            values["party_counts"] = changed_counts
        values["party"] = [member for member in values["party"] if changed_counts.get(member.role) != 0]
        if {member.role for member in values["party"]} == {member.role for member in previous.party}:
            values.pop("party")  # Count-only edits do not restate unchanged membership.
    # With no later party edit, an unresolved count remains unresolved.
    unresolved = values.get("party_size_unknown") or (
        previous is not None and previous.party_size is None and values.get("party_size") is None
    )
    if unresolved:
        values.update(clarification_needed=True, clarification_fields=["party_size"], clarification_question="请确认同行总人数（包含你），以便核算总额和人均预算。")
    effective_counts = {**(previous.party_counts if previous else {}), **values.get("party_counts", {})}
    effective_size = values.get("party_size", previous.party_size if previous else 1)
    if not unresolved and effective_size is not None and sum(effective_counts.values()) > effective_size:
        values.update(clarification_needed=True, clarification_fields=["conflict"], clarification_question="明确的角色人数超过同行总人数，请确认哪些人参加。")
    if previous and previous.required_activities and removed and not (set(previous.required_activities + required) - set(removed)) and not optional:
        values.update(clarification_needed=True, clarification_fields=["context"], clarification_question="原有活动已取消。还希望安排什么活动？")
    if unknown_money:
        values.update(clarification_needed=True,
                      clarification_fields=list(dict.fromkeys([*values.get("clarification_fields", []), *unknown_money])),
                      clarification_question="请确认尚未确定或无效的预算以及其他待澄清条件。")
    return values


class RequirementAgent:
    def __init__(self, model: ModelAdapter) -> None:
        self.model = model

    async def run(
        self,
        text: str,
        memory_context: list[dict],
        previous_spec: TripSpec | None = None,
        messages: list[Any] | None = None,
    ) -> RequirementOutput:
        fallback = self._fallback(text, memory_context, previous_spec)
        previous = previous_spec.model_dump_json() if previous_spec else "无"
        conversation = [getattr(item, "content", str(item)) for item in (messages or [])[-6:]]
        output = await self.model.structured(
            RequirementOutput,
            system=(
                "你是 PlanGo 的 Requirement Agent。把用户的本地生活目标转换为结构化约束。"
                "只提取用户明确说过或记忆中有证据的约束；无法确定时标记 clarification_needed。"
                "不要选择地点，不要调用写操作。若存在上一版需求，只返回需要新增或修改的字段，"
                "未提及的字段必须返回 null，不能用默认值覆盖上一版需求；"
                "用户明确取消的硬约束或偏好放入 remove_*_constraints 字段。"
                "预算、地点、起点时间、总时长只放在对应标量字段，不要在约束列表重复。"
                "已支持的硬约束名称：亲子友好、清淡/减脂、低排队、距离优先、忌口:食材；"
                "软偏好名称：室内优先、户外优先、新鲜感。使用这些规范名称，不同时生成同义词。"
                "其他明确要求保留用户原文；不得将天气观测、同行人数当作硬约束。"
                "没有明确钟点时不要从上午/下午推断具体时间。没有上一版需求时不要生成删除约束。"
                "明确请求的活动放入 required_activities，只有可选活动放 optional_activities；"
                "仅明确的先后关系放 activity_order。budget 是总额，per_person_budget 是人均；"
                "party 描述角色资料，party_size 是明确总人数，party_counts只记录明确角色人数，未知分配不得猜测；角色退出记录0。"
                "同事和朋友是不同角色；长辈规范为老人。"
            ),
            user=(
                f"当前用户消息：{text}\n上一版 TripSpec：{previous}\n"
                f"最近对话：{conversation}\n相关记忆：{memory_context}"
            ),
            fallback=fallback,
        )
        return self._stabilize_explicit_fields(
            output, fallback, text=text, previous_spec=previous_spec, memory_context=memory_context
        )

    @staticmethod
    def _stabilize_explicit_fields(
        output: RequirementOutput,
        fallback: RequirementOutput,
        *,
        text: str = "",
        previous_spec: TripSpec | None = None,
        memory_context: list[dict] | None = None,
    ) -> RequirementOutput:
        """Keep user-explicit slots when a provider emits malformed metadata.

        The model remains free to summarize the goal, but numbers, locations,
        party roles, and clearly stated constraints are safer when the small
        deterministic extractor is used as a lower bound.
        """

        def clean(values: list[str] | None) -> list[str]:
            return list(
                dict.fromkeys(
                    str(item).strip()
                    for item in (values or [])
                    if str(item).strip() and not any(mark in str(item) for mark in "{}[]")
                )
            )

        # A partial role patch may prove an exit but miss a differently worded
        # count update. Merge only independently grounded schema role keys.
        role_counts = dict(fallback.party_counts or {})
        additions: dict[str, int] = {}
        addition_positions: dict[str, int] = {}
        role_names = {p.role for p in (fallback.party or [])} | set(previous_spec.party_counts if previous_spec else {})
        aliases = {"孩子": r"孩子|儿童|娃", "老人": r"老人|长辈|姥姥"}
        role_events = {role: _role_count_event(text, aliases.get(role, re.escape(role)))
                       for role in role_names | set(output.party_counts or {}) if role != "用户"}
        total_events = _total_count_events(text)
        for role, quantity in (output.party_counts or {}).items():
            event = role_events.get(role)
            if role == "用户":
                continue
            pattern = aliases.get(role, re.escape(role))
            for span in re.finditer(r"[^，,；;。]+", text):
                clause = span.group()
                if not re.search(pattern, clause):
                    continue
                if any(other != role and other not in role and re.search(aliases.get(other, re.escape(other)), clause) for other in role_names - {"用户"}):
                    continue
                measures = list(re.finditer(r"(?<![\d.+-])(\d+|[零一二两三四五六七八九十]+)\s*(?:位|名|人|个(?=\s*(?:[，,；;。]|$)))", clause))
                # Ground the number through a count phrase attached to this
                # role; a performer's or venue's quantity is not party size.
                positions = [span.start() + measure.end() for mention in re.finditer(pattern, clause) for measure in measures
                    if mention.end() <= measure.start() and _hours(measure.group(1)) == quantity and re.fullmatch(
                    _ROLE_COUNT_LINK,
                    clause[mention.end():measure.start()])]
                if quantity == 0:
                    positions.extend(span.start() + match.end() for match in re.finditer(r"(?:" + pattern + r")(?:目前|已经|实际|这次|都|\s)*(?:退出|不来|不参加|不去了)", clause))
                if positions and (event is None or max(positions) > event[0]):
                    additions[role] = quantity
                    addition_positions[role] = max(positions)
        explicit_unknown_party = any(event is not None and event[1] is None and addition_positions.get(role, -1) <= event[0]
                                     for role, event in role_events.items()) or bool(
            total_events and max(total_events, key=lambda event: event[0])[1] is None)
        if additions:
            role_counts = {**role_counts, **additions}  # Only later grounded evidence can replace a rule event.
            changes: dict[str, Any] = {"party_counts": role_counts}
            merged_role_counts = {**(previous_spec.party_counts if previous_spec else {}), **role_counts}
            if previous_spec and not explicit_unknown_party and (
                previous_spec.party_size == sum(previous_spec.party_counts.values())
                or previous_spec.party_size is None and all(member.role in merged_role_counts for member in previous_spec.party)
            ):
                if not total_events:
                    size = sum(merged_role_counts.values())
                    if 1 <= size <= 12:
                        changes.update(party_size=size, party_size_unknown=False)
                        if "party_size" in fallback.clarification_fields:
                            pending = [field for field in fallback.clarification_fields if field != "party_size"]
                            changes.update(clarification_needed=bool(pending), clarification_fields=pending,
                                           clarification_question=fallback.clarification_question if pending else "")
                    else:
                        changes.update(party_size=None, party_size_unknown=True, clarification_needed=True, clarification_fields=["party_size"], clarification_question="请确认1至12人的同行总人数。")
                elif fallback.party_size is not None and sum({**previous_spec.party_counts, **role_counts}.values()) > fallback.party_size:
                    changes.update(clarification_needed=True, clarification_fields=["conflict"], clarification_question="明确的角色人数超过同行总人数，请确认哪些人参加。")
            fallback = fallback.model_copy(update=changes)

        updates: dict[str, Any] = {"goal": fallback.goal, "clarification_fields": fallback.clarification_fields}
        for field in ("required_activities", "optional_activities", "remove_activities", "activity_order", "party_size", "party_size_unknown", "party_counts", "per_person_budget", "clear_budget", "clear_per_person_budget"):
            updates[field] = getattr(fallback, field)
        if fallback.clear_budget:
            updates["budget"] = None
        for field in ("indoor_required", "outdoor_required", "max_queue_minutes", "max_distance_km"):
            updates[field] = getattr(fallback, field)
        # Three states: no text evidence clears a default; a complete rule
        # wins; an incomplete rule must not erase a grounded schema value.
        unresolved_bounds: list[str] = []
        for field, (candidates, complete) in _bound_candidates(text).items():
            if candidates is None:
                updates[field] = None
                continue
            if not complete or getattr(fallback, field) is None:
                proposed = getattr(output, field)
                if proposed is not None and len(candidates) == 1 and proposed in candidates:
                    updates[field] = proposed
                else:
                    updates[field] = None
                    unresolved_bounds.append(field)
        # A numeric model field needs evidence in its own clause. Missing
        # grammar coverage is not a deletion, and unrelated numbers are not
        # evidence (for example a queue duration is not a trip duration).
        for field, cue in (("budget", r"总预算|总额|总开销|总费用|预算"),
                           ("per_person_budget", r"人均|每人"),
                           ("duration_minutes", r"时长|小时|分钟")):
            if field in fallback.clarification_fields and getattr(fallback, field) is None:
                updates[field] = None
                unresolved_bounds.append(field)
                continue
            if getattr(fallback, field) is not None:
                continue
            if field == "budget" and fallback.clear_budget or field == "per_person_budget" and fallback.clear_per_person_budget:
                updates[field] = None
                continue
            scalar_candidates: list[float] = []
            mentioned = False
            for clause in re.split(r"[，,；;。]", text):
                if field == "duration_minutes":
                    clause = _duration_scope(clause)
                if not re.search(cue, clause) or re.search(r"取消|不限|不再要求", clause):
                    continue
                if field == "budget" and re.search(r"人均|每人", clause):
                    continue
                mentioned = True
                unit = r"小时|分钟" if field == "duration_minutes" else r"元|块"
                for match in re.finditer(r"(?<![\d.+-])(\d+(?:\.\d+)?)\s*(" + unit + r")", clause):
                    scalar_candidates.append(float(match.group(1)) * (60 if match.group(2) == "小时" else 1))
            proposed = getattr(output, field)
            updates[field] = proposed if proposed is not None and proposed in scalar_candidates else None
            if mentioned and updates[field] is None:
                unresolved_bounds.append(field)
        unsupported_hard: list[str] = []
        grounded_text = text + " " + " ".join(
            str(item.get("value")) for item in (memory_context or [])
            if item.get("kind") == "fact" and float(item.get("confidence", 0)) >= .8
        )
        scalar_label = re.compile(r"^(?:(?:budget|location|time|start_time|time_of_day|duration|weather|party)(?:\b|[_ :：])|预算|地点|位置|时间|时长|天气|人数)", re.I)
        represented = {_constraint_key(item) or item for name in ("hard_constraints", "soft_preferences", "remove_hard_constraints", "remove_soft_preferences") for item in (getattr(fallback, name) or [])}
        if fallback.outdoor_required is not None:
            represented.add("户外优先")  # The typed requirement/cancellation already carries this fact.
        for field, represented_key in (("max_queue_minutes", "低排队"), ("max_distance_km", "距离优先")):
            if updates.get(field) is not None:
                represented.add(represented_key)
        for field in (
            "hard_constraints",
            "soft_preferences",
            "remove_hard_constraints",
            "remove_soft_preferences",
        ):
            fallback_values = clean(getattr(fallback, field))
            supported = {_constraint_key(item) or item for item in fallback_values}
            values = []
            for raw in clean(getattr(output, field)):
                key = _constraint_key(raw)
                if key and key in supported:
                    values.append(key)
                elif raw in supported:
                    values.append(raw)
                elif (key or raw) in represented:
                    continue  # Already represented in another field, or explicitly removed.
                elif not field.startswith("remove_") and raw in grounded_text and not scalar_label.match(raw):
                    # Keep unknown explicit requirements; ask for clarification
                    # instead of silently dropping them to improve exact-match scores.
                    values.append(raw)
                    if field == "hard_constraints":
                        unsupported_hard.append(raw)
            merged = list(dict.fromkeys([*values, *fallback_values]))
            if field.startswith("remove_"):
                if previous_spec is None:
                    merged = []
                else:
                    existing = getattr(previous_spec, field.removeprefix("remove_"))
                    # Typed limits may exist without their historical prose mirror.
                    if field == "remove_hard_constraints":
                        existing = [*existing, *(["低排队"] if previous_spec.max_queue_minutes is not None else []), *(["距离优先"] if previous_spec.max_distance_km is not None else [])]
                    removed_keys = {_constraint_key(item) or item for item in merged}
                    merged = [old for old in existing if old in merged or (_constraint_key(old) or old) in removed_keys]
            updates[field] = merged or None

        if fallback.time_window_start is None and not re.search(r"(?:\d{1,2}[:：]\d{2}|[\d一二两三四五六七八九十]+\s*点|\d+\s*(?:am|pm))", text, re.I):
            updates["time_window_start"] = None

        if previous_spec is not None:
            # RequirementOutput is a sparse patch on later turns. Ignore
            # provider-filled defaults for slots the user did not mention.
            party_terms = ("孩子", "娃", "家人", "老婆", "老人", "朋友", "同事", "聚会", "几个人", "同行")
            location_terms = ("在", "去", "从", "附近", "周边")
            budget_terms = ("预算", "不超过", "别超", "人均")
            duration_terms = ("小时", "分钟", "时长")
            if fallback.party is None and not any(term in text for term in party_terms):
                updates["party"] = None
            if fallback.location_name is None and not any(term in text for term in location_terms):
                updates["location_name"] = None
            if fallback.budget is None and not any(term in text for term in budget_terms):
                updates["budget"] = None
            if fallback.duration_minutes is None and not any(term in text for term in duration_terms):
                updates["duration_minutes"] = None

        # Explicit numeric/location slots from the input outrank an unrelated
        # or malformed structured value returned by the model.
        for field in ("budget", "duration_minutes", "time_window_start", "location_name"):
            fallback_value = getattr(fallback, field)
            if fallback_value is not None:
                updates[field] = fallback_value

        fallback_party = fallback.party or []
        if not fallback_party:
            updates["party"] = None
        if fallback_party:
            # Explicit party words are a hard input fact. Replace model role
            # guesses rather than appending a second representation of them.
            updates["party"] = fallback_party
        explicit_slot = any(
            getattr(fallback, field) is not None
            for field in (
                "party",
                "party_size",
                "party_counts",
                "hard_constraints",
                "soft_preferences",
                "remove_hard_constraints",
                "remove_soft_preferences",
                "budget",
                "per_person_budget",
                "required_activities",
                "optional_activities",
                "remove_activities",
                "activity_order",
                "location_name",
                "time_window_start",
                "duration_minutes",
            )
        )
        generic_actionable = text.strip() in {"安排下午活动", "安排一个下午活动"}
        if not fallback.clarification_needed and (
            explicit_slot or fallback.clear_budget or fallback.clear_per_person_budget
            or (previous_spec is None and generic_actionable)
        ):
            # Deterministic extraction is the lower bound for actionable input;
            # do not pause on a model's generic clarification request.
            updates["clarification_needed"] = False
            updates["clarification_question"] = ""

        if previous_spec is not None and not fallback.clarification_needed and any(
            getattr(fallback, field) is not None
            for field in (
                "party",
                "hard_constraints",
                "soft_preferences",
                "remove_hard_constraints",
                "remove_soft_preferences",
                "budget",
                "location_name",
                "time_window_start",
                "duration_minutes",
            )
        ):
            # An explicit sparse edit is actionable against the previous spec;
            # do not repeat a generic model clarification for it.
            updates["clarification_needed"] = False
            updates["clarification_question"] = ""

        if fallback.clarification_needed:
            updates["clarification_needed"] = True
            updates["clarification_question"] = fallback.clarification_question
        if unsupported_hard:
            updates["clarification_needed"] = True
            updates["clarification_fields"] = ["unsupported"]
            updates["clarification_question"] = "这些要求尚不能自动核验，请补充具体条件：" + "、".join(unsupported_hard)
        if unresolved_bounds:
            updates["clarification_needed"] = True
            updates["clarification_fields"] = list(dict.fromkeys([*updates.get("clarification_fields", []), *unresolved_bounds]))
            updates["clarification_question"] = "请确认未能确定的预算、时长或距离/排队上限。"
        return output.model_copy(update=updates)

    @staticmethod
    def _fallback(
        text: str, memory_context: list[dict], previous_spec: TripSpec | None = None
    ) -> RequirementOutput:
        value = text or "安排一个下午活动"
        budget: float | None = None
        match = re.search(r"(?:预算|人均)[^0-9]{0,8}(\d+)", value) or re.search(r"(?:不超过|别超)\s*(\d+)\s*(?:元|块)", value)
        if match:
            budget = float(match.group(1))
        party: list[PartyMember] | None = None
        semantics = _semantic_patch(value, previous_spec)
        party = semantics.get("party")
        budget = semantics.get("budget")
        hard: list[str] = []
        soft: list[str] = []
        remove_hard: list[str] = []
        remove_soft: list[str] = []
        indoor_required: bool | None = None
        max_queue_minutes: int | None = None
        max_distance_km: float | None = None
        def negated(pattern: str) -> bool:
            return bool(re.search(r"(?:(?<!不)(?<!不要)(?<!不能)取消|去掉|不再|不要求|不要|不用|不需要)(?:要求|坚持|优先|选择)?(?:原来的|原有的|原来|原有)?\s*(?:" + pattern + r")|(?:" + pattern + r")(?:这个硬要求|这个要求|这个条件|这一项|限制|要求|规则)?(?:解除|撤掉|取消)", value))
        if any(word in value for word in ("孩子", "娃", "亲子")):
            if negated(r"亲子"):
                remove_hard.append("亲子友好")
            else:
                hard.append("亲子友好")
        if any(word in value for word in ("清淡", "减脂", "低卡")):
            (remove_hard if negated(r"清淡|减脂|低卡") else hard).append("清淡/减脂")
        if any(word in value for word in ("别太远", "就近", "附近")):
            hard.append("距离优先")
        if any(word in value for word in ("不排队", "别排队", "少排队", "低排队")):
            if negated(r"少排队|低排队|不排队"):
                remove_hard.extend(("低排队", "不排队", "少排队"))
            else:
                hard.append("低排队")
        avoid = re.findall(r"不吃([\u4e00-\u9fff]{1,4})", value)
        hard.extend(f"忌口:{item}" for item in avoid)
        if any(word in value for word in ("室内", "商场", "避雨")):
            if negated(r"室内|商场|避雨"):
                remove_soft.append("室内优先")
            else:
                soft.append("室内优先")
                if not re.search(r"(?:尽量|最好|优先|偏好|可以考虑)\s*(?:在)?\s*室内|室内优先|室内(?:场所)?也可以", value):
                    indoor_required = True
        if re.search(r"(?:必须|一定要|只要|只去|要求)\s*(?:在|是)?\s*室内|(?:不能|不要)\s*户外", value):
            indoor_required = True
        if negated(r"室内") or "允许户外" in value:
            indoor_required = False
        if negated(r"户外"):
            remove_soft.append("户外优先")
            semantics["outdoor_required"] = False
        if re.search(r"(?:不|不要|别|不能|不可)排队", value) and not negated(r"不排队"):
            max_queue_minutes = 0
            hard.append("低排队")
        queue_limit = re.search(r"排队(?:时间)?\s*(?:不超过|最多|至多)\s*(\d+)\s*分钟", value)
        if queue_limit:
            max_queue_minutes = int(queue_limit.group(1))
            hard.append("低排队")
        distance_limit = re.search(r"(?:距离|路程|步行|移动)\s*(?:不超过|最多|至多|上限)?\s*(\d+(?:\.\d+)?)\s*(?:公里|km)", value, re.I)
        if distance_limit:
            max_distance_km = float(distance_limit.group(1))
            hard.append("距离优先")
        for field, (candidates, complete) in _bound_candidates(value).items():
            label = "低排队" if field == "max_queue_minutes" else "距离优先"
            if candidates is None:
                if field == "max_queue_minutes":
                    max_queue_minutes = None
                else:
                    max_distance_km = None
                remove_hard.append(label)
                hard = [item for item in hard if item != label]
            elif complete:
                if field == "max_queue_minutes":
                    max_queue_minutes = int(candidates[0])
                    hard.append("低排队")
                else:
                    max_distance_km = candidates[0]
                    hard.append("距离优先")
                remove_hard = [item for item in remove_hard if (_constraint_key(item) or item) != label]
        if any(word in value for word in ("新鲜", "看展", "展览", "citywalk")):
            soft.append("新鲜感")
        # Persisted rules are context hints, not blindly converted into facts.
        for item in memory_context:
            if item.get("kind") == "rule" and item.get("action"):
                soft.append(str(item["action"]))
            elif item.get("kind") == "fact" and float(item.get("confidence", 0)) >= .8:
                payload = item.get("value")
                fact_text = payload.get("text", "") if isinstance(payload, dict) else payload
                key = _constraint_key(str(fact_text or ""))
                if key in {"室内优先", "户外优先", "新鲜感"}:
                    if key not in remove_soft and not re.search(r"取消|去掉|不再|不要求|不要|不用|不需要|不喜欢", str(fact_text)):
                        soft.append(key)
                elif key and key not in remove_hard:
                    hard.append(key)
        location_name: str | None = None
        location_matches = re.finditer(
            r"(?<!现)(?:在|去|从)\s*([\u4e00-\u9fffA-Za-z0-9·]{2,24}?)(?=出发|安排|玩|逛|吃|喝|看|参观|先|用餐|聚餐|附近|预算|下午|上午|晚上|今天|周六|，|,|$)",
            value,
        )
        generic_locations = {"附近", "周边", "周围", "这边", "那里", "室内", "户外", "公园", "动物园", "餐厅", "展览", "电影", "咖啡", "博物馆", "电影院", "城市漫步"}
        for location_match in location_matches:
            candidate = location_match.group(1).strip("的")
            if re.search(r"(?:排|放)$", value[:location_match.start()]) and re.search(r"(?:前|后)$", candidate):
                continue
            if not all(piece in generic_locations for piece in re.split(r"[和与及、]", candidate)) and not re.search(r"看展|看电影|吃饭|咖啡|之前|之后|以后|(?:排|放)(?:在|到)|(?:排|放)(?:最前|最后)|^[并且]+", candidate):
                location_name = candidate or location_name
        if explicit_location := re.search(r"(?:出发地点|起点|地点|位置)[:：]\s*([^，,；;。]+)", value):
            location_name = explicit_location.group(1).strip()
        time_start: str | None = None
        clock_match = re.search(r"(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)(?!\d)", value)
        time_match = _CLOCK_POINT.search(value)
        if clock_match:
            hour = int(clock_match.group(1))
            prefix = value[max(0, clock_match.start() - 3):clock_match.start()]
            if hour < 12 and any(word in prefix for word in ("下午", "晚上")):
                hour += 12
            time_start = f"{hour:02}:{int(clock_match.group(2)):02}"
        elif time_match:
            hour = min(23, int(_hours(time_match.group(1)) or 0))
            if hour < 12 and any(
                word in value[max(0, time_match.start() - 3) : time_match.start()]
                for word in ("下午", "晚上")
            ):
                hour += 12
            minute = 30 if time_match.group(2) else int(_hours(time_match.group(3) or time_match.group(4) or "0") or 0)
            time_start = f"{hour:02d}:{minute:02d}"
        elif any(word in value for word in ("晚上", "晚餐", "吃晚饭")):
            time_start = "18:00"
        duration: int | None = None
        duration_text = _duration_scope(value)
        duration_match = _TIME_AMOUNT.search(duration_text)
        if duration_match:
            minutes = _time_minutes(duration_match)
            if minutes is not None and minutes.is_integer() and 30 <= minutes <= 1440:
                duration = int(minutes)
        goal = value if previous_spec is None else f"{previous_spec.goal}；用户补充：{value}"
        vague = value.strip() in {
            "帮我安排一下",
            "帮我安排",
            "想出去玩",
            "想出去",
            "周末出门",
            "帮我安排活动",
            "周末想出去",
            "预算不限，地点也不限",
        } or any(phrase in value for phrase in ("还没想好去哪", "没想好去哪"))
        meal_without_context = (
            previous_spec is None
            and any(word in value for word in ("吃饭", "吃晚饭", "晚餐", "聚餐"))
            and location_name is None
            and budget is None
            and party is None
        )
        unhandled: list[str] = []
        # Explicit requirements must not depend on the model remembering to
        # emit them. Unknown mandatory clauses pause before planning.
        for clause in re.split(r"[，,。；;]|(?:而且|并且|但)", value):
            cancelling = bool(re.search(r"取消|去掉|不再要求|不需要|不要求", clause))
            if cancelling and previous_spec:
                for old in previous_spec.hard_constraints:
                    if old in clause or any(word in old and word in clause for word in ("无障碍", "轮椅", "电梯", "停留")):
                        remove_hard.append(old)
            mandatory = re.search(r"(?<!不再)(?<!取消)(?<!不)(?:必须|一定要|需要|要求)\s*(.+)", clause)
            if mandatory:
                body = mandatory.group(1).strip()
                generic = bool(re.fullmatch(r"(?:你)?(?:帮我)?(?:安排|规划)(?:一下|活动|行程|计划)?|一份计划|一个计划", body))
                known = bool(re.fullmatch(r"(?:都)?(?:在|是)?(?:室内|户外|亲子友好|低排队|少排队|不排队|清淡|减脂|低卡|距离优先)(?:活动|餐厅|场馆|优先|一点)?", body)) or bool(semantics.get("required_activities")) and bool(re.fullmatch(r"(?:在|是|全程|室内|户外|先|再|然后|依次|按顺序|看展|吃饭|喝咖啡|看电影|去公园|去动物园|城市漫步|和|并|以及|及|与|、|\s)+", body))
                known = known or bool(re.fullmatch(r"(?:仍然|仍)?保留", body) and re.search(r"室内|户外", clause))
                known = known or bool(body == "去" and semantics.get("required_activities") and re.search(r"电影|展览|看展|咖啡|公园|餐厅|城市漫步", clause[:mandatory.start()]))
                scalar = bool(re.fullmatch(r"(?:预算|人均)\s*(?:不超过|最多)?\s*\d+\s*元?", body))
                scalar = scalar or bool(
                    ((max_queue_minutes is not None and re.search(r"排队|等候|等位|等待", clause))
                     or (max_distance_km is not None and re.search(r"距离|路程|移动|每(?:一)?段", clause)))
                    and re.fullmatch(r"(?:在|为|是|到)?\s*\d+(?:\.\d+)?\s*(?:分钟|公里|km)?", body, re.I)
                )
                if not (generic or known or scalar):
                    unhandled.append(clause.strip())
            if not cancelling and re.search(r"无障碍|轮椅|电梯|无台阶", clause):
                unhandled.append(clause.strip())
            if not cancelling and re.search(r"(?:停留|待满|逗留)\s*[\d一二两三四五六七八九十半]+\s*(?:小时|分钟)", clause):
                unhandled.append(clause.strip())
        unhandled = list(dict.fromkeys(unhandled))
        hard.extend(unhandled)
        if re.search(r"尽量(?:少|不)排队|最好少排队", value):
            hard = [item for item in hard if item != "低排队"]
            soft.append("低排队")
        if negated(r"户外"):
            semantics["outdoor_required"] = False
        elif "户外" in value and not re.search(r"(?:尽量|最好|优先|偏好|可以考虑)\s*(?:在)?\s*户外|户外优先", value):
            semantics["outdoor_required"] = not bool(re.search(r"不要户外|不能户外|取消户外|不去户外|不再要求户外", value))
            if semantics["outdoor_required"]:
                indoor_required = False
        elif re.search(r"(?:尽量|最好|优先|偏好|可以考虑)\s*(?:在)?\s*户外|户外优先", value):
            soft.append("户外优先")
        if indoor_required:
            semantics["outdoor_required"] = False
        for field, mode in (("indoor_required", "室内"), ("outdoor_required", "户外")):
            if previous_spec and getattr(previous_spec, field) and re.search(mode + r"(?:的硬要求|要求|限制)?(?:仍然|仍)?保留", value):
                if field == "indoor_required":
                    indoor_required = None
                else:
                    semantics.pop(field, None)
        result = RequirementOutput(
            goal=goal,
            party=party,
            hard_constraints=hard or None,
            soft_preferences=soft or None,
            remove_hard_constraints=list(dict.fromkeys(remove_hard)) or None,
            remove_soft_preferences=list(dict.fromkeys(remove_soft)) or None,
            budget=budget,
            location_name=location_name,
            time_window_start=time_start,
            duration_minutes=duration,
            indoor_required=indoor_required,
            max_queue_minutes=max_queue_minutes,
            max_distance_km=max_distance_km,
            clarification_needed=bool(unhandled) or vague or meal_without_context,
            clarification_fields=["unsupported"] if unhandled else ["context"] if vague or meal_without_context else [],
            clarification_question=(
                "这些要求尚不能自动核验，请确认可执行条件：" + "、".join(unhandled)
                if unhandled else "请补充地点、时间和预算"
                if vague or meal_without_context
                else ""
            ),
        )
        return result.model_copy(update=semantics)

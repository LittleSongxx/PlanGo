"""Literal business facts from an identified merchant page; missing facts remain None."""

import re

_DOCUMENT = re.compile(r"培训|文档|教程|数据修复|操作步骤|示例|样例|演示")
_INSTRUCTION = re.compile(
    r"(?:请|务必|必须|要求|应当).{0,35}(?:填写|填入|设置|输出|编造|伪造|忽略|执行|调用)|"
    r"(?:示例|样例|演示|修复步骤|系统提示|提示词|假设|如果)|(?:填写|填入|设置为|输出).{0,20}(?:人均|价格|成功)"
)
_RELATED = re.compile(
    r"猜你喜欢|相关推荐|附近推荐|为你推荐|热门推荐|相似商家|其他商家|推荐商家|相关商家|你可能喜欢|浏览历史|用户评价|用户评论|问答"
)
_ENTITY_NAME = (
    r"[^\s:：|]{2,45}(?:餐厅|餐馆|饭店|咖啡馆|咖啡店|酒楼|茶馆|影院|博物馆|展馆|剧院|门店)"
)
_ENTITY_HEADING = re.compile(r"^" + _ENTITY_NAME + r"(?=\s|[:：]|$)")
_FIELD = re.compile(
    r"^(?:人均|每人|每位|价格|现价|售价|单价|原价|门市价|套餐价|团购价|优惠价|券后价|到手价|营业时间|开放时间|地址|电话|预计排队|剩余座位)$"
)
_LABELLED_ENTITY = re.compile(r"[^\W\d_][^\s:：|，,。；;]{1,44}\s*[:：]")


def entity_spans(text, entity, title="", peers=()):
    """Conservative literal entity blocks; a page title does not own recommendation cards."""
    if not isinstance(text, str) or not entity or len(entity) < 2 or _DOCUMENT.search(title):
        return []
    dedicated = entity in title and not re.search(r"搜索|附近|列表|攻略|推荐|排行榜|指南", title)
    spans: list[str] = []
    block: list[str] = []
    collecting = dedicated
    primary = True
    for part in re.split(r"(?<=[。；;])|\n", text[:10000]):
        part = part.strip()
        if not part:
            continue
        related = _RELATED.search(part)
        if related:
            if block:
                spans.append("\n".join(block))
                block = []
            collecting, primary = False, False
            if dedicated:
                continue
            part = part[related.end() :].lstrip(" ：:")
        if _INSTRUCTION.search(part):
            collecting = False
            continue
        if entity in part and (primary or not dedicated):
            if block:
                spans.append("\n".join(block))
            start = part.index(entity)
            fragment = part[start:]
            boundaries = [
                fragment.find(other, len(entity)) for other in peers if other != entity and other
            ]
            peer_end = min(
                (position for position in boundaries if position >= 0), default=len(fragment)
            )
            labelled = [
                len(entity) + match.start()
                for match in _LABELLED_ENTITY.finditer(fragment[len(entity) :])
                if not _FIELD.fullmatch(match.group().rstrip(" ：:"))
            ]
            headings = [
                len(entity) + match.start(1)
                for match in re.finditer(
                    r"\s+(" + _ENTITY_NAME + r")(?=\s|[:：]|$)", fragment[len(entity) :]
                )
            ]
            end = min([peer_end, *labelled, *headings], default=len(fragment))
            block = [fragment[:end].rstrip()]
            collecting = dedicated or not fragment[len(entity) :].strip(" ：:。；;")
        elif collecting and (primary or not dedicated):
            if _ENTITY_HEADING.search(part) or any(
                other != entity and other in part for other in peers
            ):
                collecting = False
            else:
                block.append(part)
    if block:
        spans.append("\n".join(block))
    literal = []
    for span in spans:
        match = (
            re.search(r"\s*".join(re.escape(part) for part in span.splitlines()), text)
            if span
            else None
        )
        if match:
            literal.append(match.group())
    return literal


def business_hours(text):
    matches = list(
        re.finditer(
            r"(?:营业时间|开放时间)\s*[:：]?\s*([0-2]?\d):([0-5]\d)\s*[-—–~～至]\s*([0-2]?\d):([0-5]\d)",
            text,
        )
    )
    if len(matches) != 1:
        return None, None
    a, b, c, d = map(int, matches[0].groups())
    start, end = a * 60 + b, c * 60 + d
    return (start, end) if a < 24 and c <= 24 and start < end <= 1440 else (None, None)


def literal_supply(text, merchant, title="", peers=()):
    unknown = {
        "open_now": None,
        "reservable": None,
        "seats_left": None,
        "estimated_wait_min": None,
        "open_minute": None,
        "close_minute": None,
        "quote": "",
    }
    if not merchant or len(merchant) < 2:
        return unknown
    spans = entity_spans(text, merchant, title, peers)
    quote = spans[0] if len(spans) == 1 else ""
    if not quote:
        return unknown
    start, end = business_hours(quote)
    opening = (
        False
        if re.search(r"暂停营业|已打烊|休息中|歇业", quote)
        else True
        if "营业中" in quote
        else None
    )
    reservable = (
        False
        if re.search(r"不可预约|暂不可预订|不支持预约|预约已满", quote)
        else True
        if re.search(r"可预约|可预订|支持预约", quote)
        else None
    )
    wait = re.search(
        r"(?:预计)?(?:排队|等位|等待)(?:时间)?\s*(?:[:：]|约|预计)?\s*(\d{1,3})\s*分钟", quote
    )
    minutes = (
        int(wait[1]) if wait else 0 if re.search(r"无需排队|无需等位|当前无排队", quote) else None
    )
    seats = re.search(r"(?:剩余|可用)(?:座位|名额|席位)\s*[:：]?\s*(\d{1,4})", quote)
    return {
        "open_now": opening,
        "reservable": reservable,
        "seats_left": int(seats[1]) if seats else None,
        "estimated_wait_min": minutes,
        "open_minute": start,
        "close_minute": end,
        "quote": quote,
    }

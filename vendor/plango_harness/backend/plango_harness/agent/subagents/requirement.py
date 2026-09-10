from __future__ import annotations

from typing import Any

from plango_harness.agent.contracts import TripSpec
from plango_harness.agent.decisions import RequirementOutput
from plango_harness.agent.model_adapter import ModelAdapter, ModelProviderUnavailable

REQUIREMENT_INSTRUCTIONS = (
    "你是 PlanGo 的需求理解节点。结合当前消息、上一版需求和对话理解本轮意图，返回稀疏需求补丁。"
    "省略、指代、否定、范围及单位由你统一理解；未要求修改的字段返回null，不要重建整份需求。"
    "旧值可以用于理解指代，无需重复询问已知信息；记忆只作上下文，不得冒充本轮用户修改。"
    "明确取消才用clear_*或remove_*，明确改为未知才用*_unknown；未提及与清除不同。"
    "取消排队上限用clear_max_queue，取消路程上限用clear_route_distance，取消搜索半径用clear_search_radius。"
    "同一字段的设置、清除和未知标志不能同时出现。真实歧义在clarification_fields列出具体字段，"
    "clarification_question只询问这些字段，其余已知修改照常返回。"
    "一个数值可能落在多个字段上时（例如只说“预算180”，既可能是总额budget也可能是人均per_person_budget），"
    "不要替用户挑一个写进去：把该字段列入clarification_fields并询问，其余能确定的修改照常返回。"
    "预算总额用budget，人均用per_person_budget；金额单位元，时长和排队单位分钟，距离单位公里。"
    "搜索范围用search_radius_km，实际路程上限用route_distance_km，不使用旧字段max_distance_km。"
    "party记录角色资料，party_size记录总人数，party_counts只修改已知角色人数，退出记0，不猜未知分配。"
    "必选、可选、移除活动分别用required_activities、optional_activities、remove_activities；"
    "activity_order只在调整顺序时返回，明确取消顺序返回空列表。"
    "区分真实新地址、当前起点、已选门店和活动类别，后面三种使用reference字段，不当作地名搜索。"
    "标量条件使用对应字段，不在hard_constraints重复；其他明确要求保留其含义，不能因没有预设类别而拒绝。"
    "规划资料来源planning_source可选browser当前网页或amap地图，未要求切换时留空；明确要求重新观测时refresh_sources为true。"
    "field_evidence仅作可选审计注记，缺少引用不代表用户歧义。不要选择门店，不执行写操作。"
)


class RequirementAgent:
    def __init__(self, model: ModelAdapter) -> None:
        self.model = model

    async def run(
        self,
        text: str,
        memory_context: list[dict],
        previous_spec: TripSpec | None = None,
        messages: list[Any] | None = None,
        *, reference_at: str | None = None,
    ) -> RequirementOutput:
        fallback = RequirementOutput()
        previous = previous_spec.model_dump_json() if previous_spec else "无"
        conversation = [getattr(item, "content", str(item)) for item in (messages or [])[-6:]]
        output = await self.model.structured(
            RequirementOutput,
            system=REQUIREMENT_INSTRUCTIONS,
            user=(
                f"当前用户消息：{text}\n上一版 TripSpec：{previous}\n"
                f"最近对话：{conversation}\n相关记忆：{memory_context}\n"
                f"本轮消息时间：{reference_at or '未提供'}；默认时区Asia/Shanghai"
            ),
            fallback=fallback,
        )
        if output is fallback:
            raise ModelProviderUnavailable("model_unavailable")
        return output

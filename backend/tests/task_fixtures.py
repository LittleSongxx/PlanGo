"""Explicit synthetic actors for transport tests; never imported by product code."""

import json

from plango.task import BrowserDecision, Citation, DeliveryDecision, TaskDecision


def browser_actor(next_step=None):
    async def actor(schema, *, fallback, **kwargs):
        if schema not in {TaskDecision, DeliveryDecision}:
            return await next_step(schema, fallback=fallback, **kwargs) if next_step else fallback
        context = json.loads(kwargs['user'])
        if not context.get('browser_steps'):
            return TaskDecision(operation='read')
        if next_step:
            decision = await next_step(BrowserDecision, fallback=BrowserDecision(), **kwargs)
            if decision.operation != 'finish':
                return TaskDecision(operation='read', browser=decision)
        sources = context['sources']
        citations = [Citation(artifact_id=source['artifact_id'], quote=record['text'])
                     for source in sources for record in source['records'] if record['text'].strip()][:1]
        return TaskDecision(operation='answer', answer='已读取测试页面，内容保留于来源卡。', citations=citations)
    return actor

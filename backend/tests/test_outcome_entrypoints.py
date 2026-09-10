"""Controlled actors test the full task loop; they do not measure language ability."""

import json

import pytest
from fastapi.testclient import TestClient
from plango.app import create_app
from plango.graph import ImageReading
from plango.task import Calculation, Citation, DeliveryDecision, TaskDecision
from plango_harness.agent.decisions import RequirementOutput
from test_browser_harness import TOKEN, fixture, settings, wait_for

IMAGE = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j/a0AAAAASUVORK5CYII='
TERMINAL = {'SUCCEEDED', 'PARTIAL_FAILED', 'FAILED', 'CANCELLED'}


@pytest.mark.parametrize('image', [False, True])
def test_sources_calculation_answer_and_sparse_followup_share_one_task(tmp_path, image):
    config = settings(tmp_path).model_copy(update={"openai_api_key": "test-only-no-network"})
    app = create_app(config, token=TOKEN)
    contexts = []
    text = '材料费每人28.5元；配送费用尚未给出。'
    original = '我们3人，预算100，算已知材料费；配送未知不影响小计。'

    async def actor(schema, **kwargs):
        if schema is ImageReading:
            return ImageReading(text=text)
        assert schema in {TaskDecision, DeliveryDecision}, 'No second intent classifier or restaurant extractor'
        context = json.loads(kwargs['user'])
        contexts.append(context)
        if not context['sources']:
            return TaskDecision(operation='read')
        source = context['sources'][0]
        citation = Citation(artifact_id=source['artifact_id'], quote=text)
        if not context['tool_results']:
            return TaskDecision(operation='calculate', requirements=RequirementOutput(party_size=3, budget=100),
                calculations=[Calculation(id='subtotal', operation='multiply', operands=['28.5', '3'], citations=[citation])])
        assert context['tool_results'][0]['value'] == '85.5'
        assert context['original_request'] == original
        return TaskDecision(operation='answer', answer='3人的已知材料费为85.5元；配送费未知，因此不是最终总价。', citations=[citation])

    app.state.runtime.model.structured = actor
    with TestClient(app, headers={'Authorization': 'Bearer ' + TOKEN}) as client:
        response = client.post('/api/v1/runs', json={'input_text': original, 'browser_session_id': 'fixture-desktop', **({'image': IMAGE} if image else {})})
        rid = response.json()['run_id']
        wait_for(client, rid, lambda v: v['phase'] in TERMINAL or v['state'].get('browser_wait'))
        if not image:
            command = client.get('/api/v1/browser/commands?browser_session_id=fixture-desktop').json()['commands'][0]
            client.post('/api/v1/browser/commands/' + command['command_id'] + '/result', json={**fixture(command), 'text': text, 'tables': []})
        done = wait_for(client, rid, lambda v: v['phase'] in TERMINAL)
        assert done['phase'] == 'SUCCEEDED', done
        outcome = done['state']['execution_outcome']
        assert outcome['summary'].startswith('3人的已知材料费为85.5元')
        assert outcome['data']['scope'] == 'task_answer' and outcome['data']['business_completed'] is False
        assert outcome['data']['calculations'][0]['ok']
        assert not done['state'].get('trip_spec'), 'Reading must not create an itinerary with default coordinates'
        assert len(contexts) == (2 if image else 3)
        original_artifacts = done['state']['browser_artifacts']

    # Restart and edit only a decided field. An ambiguous field retains its accepted value.
    restored = create_app(config, token=TOKEN)
    async def edit_actor(schema, **kwargs):
        assert schema in {TaskDecision, DeliveryDecision}
        context = json.loads(kwargs['user'])
        assert context['original_request'] == original and context['trip_spec'] is None
        assert context['sources'] and context['tool_results'] == []
        if context['turn_id'] == 3:
            assert context['question_being_answered'] == '180是总预算还是每人预算？'
            assert context['current_request'] == '总预算'
            return TaskDecision(operation='answer', answer='已按4人、总预算180元继续。')
        return TaskDecision(operation='ask', question='180是总预算还是每人预算？', requirements=RequirementOutput(
            party_size=4, clarification_needed=True, clarification_fields=['budget', 'per_person_budget'],
            clarification_question='180是总预算还是每人预算？'))
    restored.state.runtime.model.structured = edit_actor
    with TestClient(restored, headers={'Authorization': 'Bearer ' + TOKEN}) as client:
        response = client.post(f'/api/v1/runs/{rid}/messages', json={'text': '改4人，预算180，其余不变'})
        assert response.status_code == 202
        edited = wait_for(client, rid, lambda v: v['state'].get('turn_id', 1) == 2 and (v.get('interrupt_id') or v['phase'] in TERMINAL))
        assert not edited['state'].get('trip_spec')
        assert edited['state']['browser_task_context']['original_request'] == original
        assert edited['state']['browser_artifacts'] == original_artifacts
        assert edited['state']['clarification']['question'] == '180是总预算还是每人预算？'
        assert not edited['state'].get('action_results')
        response = client.post(f'/api/v1/runs/{rid}/messages', json={'text': '总预算'})
        assert response.status_code == 202
        resumed = wait_for(client, rid, lambda v: v['state'].get('turn_id') == 3 and v['phase'] in TERMINAL)
        assert resumed['phase'] == 'SUCCEEDED' and '180' in resumed['state']['reason']


def test_missing_model_is_failure_instead_of_a_fake_question_or_success(tmp_path):
    app = create_app(settings(tmp_path), token=TOKEN)
    with TestClient(app, headers={'Authorization': 'Bearer ' + TOKEN}) as client:
        rid = client.post('/api/v1/runs', json={'input_text': '分析已给资料', 'browser_session_id': 'fixture-desktop'}).json()['run_id']
        done = wait_for(client, rid, lambda v: v['phase'] in TERMINAL)
        assert done['phase'] == 'FAILED'
        assert not done['state'].get('clarification') and not done['state'].get('action_results')

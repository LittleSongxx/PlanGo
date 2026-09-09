"""Real UI wording regressions; only routing is isolated, no merchant facts fabricated."""
from plango.outcomes import TaskIntent, update_task_context


def test_negative_submission_stays_read_only_and_is_not_removed_from_request():
    request = '请只读取当前重庆市政府网页，保留来源网址。不要规划或提交任何操作。'
    context = update_task_context({'input_text': request, 'turn_id': 1})
    assert context['mode'] == 'browser'
    assert context['kind'] == 'extract'
    assert context['request'] == request
    assert update_task_context({'input_text': '读取当前页面的文字', 'turn_id': 1})['kind'] == 'extract'
    assert update_task_context({'input_text': '不要支付，但请帮我预约这家餐厅。', 'turn_id': 1})['kind'] == 'write'
    assert update_task_context({'input_text': '读取网页，然后提交预约。', 'turn_id': 1})['kind'] == 'write'
    request = '读取当前页面顺风123观音桥大融城店的真实门店地址、菜单和套餐使用条件；遇到登录请暂停供我人工接管，不登录、不下单。'
    context = update_task_context({'input_text': request, 'turn_id': 1})
    assert context['kind'] == 'extract' and context['read_kind'] == 'menu_read'
    assert context['request'] == request
    assert update_task_context({'input_text': '不下单，但请帮我预约这家餐厅。', 'turn_id': 1})['kind'] == 'write'


def test_user_image_reading_and_image_planning_use_distinct_intents():
    read = update_task_context({'input_text': '读取我上传的图片中的文字，仅整理图片已有内容并保留图片来源。', 'turn_id': 1})
    assert read['mode'] == 'browser'
    assert read['kind'] == 'extract'
    plan = update_task_context({'input_text': '结合这张图片和真实信息帮我规划一下重庆行程', 'turn_id': 1})
    assert plan['mode'] == 'planning'


def test_planning_field_and_source_edits_do_not_start_an_unrequested_browser_task():
    old = update_task_context({'input_text': '帮我规划重庆周末行程', 'turn_id': 1})
    for text in ['总共3人包含我，只使用当前网页这家餐厅，不增加其他地点。', '今天下午14点，2个人，总预算300元，不需要预约或取号。']:
        edited = update_task_context({'input_text': text, 'turn_id': 2, 'browser_task_context': dict(old)})
        assert edited['mode'] == 'planning' and edited['kind'] == 'planning'
        assert edited['edits'][-1] == text
    reading = update_task_context({'input_text': '不要规划，先读取当前网页菜单', 'turn_id': 2, 'browser_task_context': dict(old)})
    assert reading['mode'] == 'browser' and reading['kind'] == 'extract'


def test_source_analysis_needs_evidence_not_geolocation_and_preserves_write_intents():
    for text in ['依据所给条款判断适用性，并给出总价。', '根据给定路线能确认不超预算吗？',
                 '分析这份行程的费用和预算，不要重新规划。', '核算这份资料的总费用，只做条件分析。']:
        context = update_task_context({'input_text': text, 'turn_id': 1})
        assert context['mode'] == 'browser' and context['kind'] == 'reasoning' and context['source_analysis']
    for text in ['先判断适用性，再购买这份套餐。', '核算总价后提交预约。', '分析费用，并帮我支付。', '判断适用性后帮我买一份。']:
        assert update_task_context({'input_text': text, 'turn_id': 1})['kind'] == 'write'
    context = update_task_context({'input_text': '帮我规划重庆行程并分析预算。', 'turn_id': 1})
    assert context['mode'] == 'planning'


def test_semantic_objective_overrides_words_and_continuation_preserves_scope():
    original = update_task_context({'input_text': '安排重庆行程', 'turn_id': 1})
    for request in ('这里写了来回各收五元，三个人要留多少车钱？',
                    '别替我安排出游，只说表里两个选项差在哪里。',
                    '九点一刻到还来得及用这张券么？'):
        state = {'input_text': request, 'turn_id': 2, 'browser_task_context': original}
        context = update_task_context(state, TaskIntent(kind='reasoning'))
        assert context['kind'] == 'reasoning' and context['mode'] == 'browser'
        assert context['source_analysis'] and context['request'] == request
        assert original['kind'] == 'planning'
        continued = update_task_context({'input_text': '其余不变', 'turn_id': 3,
                                        'browser_task_context': context}, TaskIntent())
        assert continued['kind'] == 'reasoning' and continued['source_analysis']
        assert continued['comparison_scope'] == request
        assert update_task_context({'input_text': '其余不变', 'turn_id': 3,
                                   'browser_task_context': continued}, TaskIntent()) == continued
        another = update_task_context({'input_text': '其余不变', 'turn_id': 4,
                                      'browser_task_context': continued}, TaskIntent())
        assert another['edits'] == ['其余不变', '其余不变']


def test_changing_delivery_keeps_accepted_requirements():
    state = {'input_text': '不用重新安排，按原人数和预算核算这份资料够不够', 'turn_id': 2,
             'trip_spec': {'goal': '原草案', 'party_size': 3, 'budget': 300, 'per_person_budget': 100,
                           'visit_date': '2026-09-11', 'time_window_start': '18:30'},
             'browser_task_context': {'mode': 'planning', 'kind': 'planning', 'turn_id': 1}}
    context = update_task_context(state, TaskIntent(kind='reasoning'))
    expected = {'party_size': 3, 'total_budget': 300, 'per_person_budget': 100,
                'visit_date': '2026-09-11', 'time_window_start': '18:30'}
    assert all(context[field] == value for field, value in expected.items())
    continued = update_task_context({'input_text': '其余不变', 'turn_id': 3,
                                    'browser_task_context': context}, TaskIntent())
    assert all(continued[field] == value for field, value in expected.items())

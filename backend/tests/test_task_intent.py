"""Real UI wording regressions; only routing is isolated, no merchant facts fabricated."""
from plango.outcomes import update_task_context


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

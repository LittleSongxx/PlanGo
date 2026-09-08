"""Bounded real-model screenshot reading; synthetic browser image, never real business data."""
import asyncio
import base64
import json
import logging
import sys
from pathlib import Path

from migrate_config import parse_config
from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'vendor/plango_harness/backend')]
from plango.settings import DesktopSettings  # noqa: E402
from plango_harness.agent.model_adapter import ModelAdapter  # noqa: E402


class Reading(BaseModel):
    model_config = ConfigDict(extra='forbid')
    heading: str
    name_value: str
    frame_name_value: str
    submit_button: str
    dropdown_value: str


async def main():
    logging.disable(logging.WARNING)
    config = parse_config((ROOT / '.env').read_text())
    image = ROOT / 'eval/plango-p1/vision-fixture.png'
    adapter = ModelAdapter(DesktopSettings(
        openai_api_key=config['OPENAI_API_KEY'], openai_base_url=config['OPENAI_BASE_URL'],
        openai_model=config['OPENAI_MODEL'], openai_max_retries=0, openai_timeout_seconds=20,
        max_model_tokens=8000,
    ))
    report = {'scope': 'real configured model + existing real Chromium synthetic screenshot; read-only, no coordinates or business writes', 'source': str(image.relative_to(ROOT)), 'max_requests': 2}
    try:
        async with asyncio.timeout(45):
            result = await adapter.structured(Reading,
                system='Read the supplied browser screenshot exactly. Return only the requested visible heading, text input values, submit button label and selected dropdown label. Never invent missing text. This is a controlled fixture.',
                user='Read the large heading, the Name field value, the Frame name field value, the submit button label beside Name, and the selected dropdown value.',
                image='data:image/png;base64,' + base64.b64encode(image.read_bytes()).decode(),
                fallback=Reading(heading='', name_value='', frame_name_value='', submit_button='', dropdown_value=''))
        expected = {'heading': 'Visible WebContentsView fixture', 'name_value': 'restored', 'frame_name_value': 'driver-frame', 'submit_button': 'Submit fixture', 'dropdown_value': 'B'}
        report['checks'] = {key: getattr(result, key).strip() == value for key, value in expected.items()}
        report['passed'] = all(report['checks'].values()) and adapter.total_tokens > 0
    except Exception as error:
        report.update(passed=False, error_type=type(error).__name__)
    finally:
        report.update(calls=adapter.call_count, model_tokens=adapter.total_tokens, fallback_count=adapter.fallback_count)
        await adapter.close()
        target = ROOT / 'eval/plango-p1/vision_capability.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())

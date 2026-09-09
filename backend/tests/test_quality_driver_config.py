"""Pure collector configuration checks; no runtime, browser, server or model starts."""
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_paths = list(sys.path)
sys.path.insert(0, str(ROOT / "scripts"))
try:
    import quality_state_cases as state_cases
finally:
    sys.path[:] = _paths


def test_driver_dispatch_preserves_old_cases_and_requires_new_declarations():
    for case_id, expected in {"DEV-05": "edit", "DEV-06": "edit", "DEV-09": "save_restart", "DEV-10": "message_recovery"}.items():
        assert state_cases.case_driver({"case_id": case_id}) == expected
    for driver in ("edit", "save_restart", "message_recovery", "message_uncertain"):
        assert state_cases.case_driver({"case_id": "NEW-01", "environment": {"driver": driver}}) == driver
    for case in [{"case_id": "NEW-01"}, {"case_id": "../escape", "environment": {"driver": "edit"}}, {"case_id": "X" * 65}]:
        with pytest.raises(ValueError):
            state_cases.case_driver(case)


def test_inline_fixture_is_verified_against_the_actual_file_pointer(tmp_path, monkeypatch):
    fixture, legacy = state_cases.load_runtime_fixture()
    assert legacy["kind"] == "file" and legacy["json_pointer"] == ""
    source = tmp_path / "fixture.json"
    source.write_text(json.dumps({"fixtures": {"single/walk": fixture}}, ensure_ascii=False))
    monkeypatch.setattr(state_cases, "ROOT", tmp_path)
    provenance = {"path": "fixture.json", "json_pointer": "/fixtures/single~1walk"}
    loaded, origin = state_cases.load_runtime_fixture(copy.deepcopy(fixture), provenance)
    assert loaded == fixture and origin["kind"] == "inline_verified"
    assert origin["json_pointer"] == provenance["json_pointer"]
    assert origin["whole_file_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    changed = copy.deepcopy(fixture)
    changed["origin"]["name"] = "changed input"
    with pytest.raises(ValueError, match="does_not_match"):
        state_cases.load_runtime_fixture(changed, provenance)
    with pytest.raises(FileNotFoundError):
        state_cases.load_runtime_fixture(fixture, {"path": "missing.json", "json_pointer": ""})
    with pytest.raises(ValueError, match="requires_source"):
        state_cases.load_runtime_fixture(fixture)


def test_desktop_config_matches_declared_message_and_validates_fingerprinted_body():
    script = r"""
const assert=require('node:assert/strict'),crypto=require('node:crypto');
const {driverConfig,permittedMessage,permittedSave}=require('./scripts/quality_desktop_cases.cjs');
const message='人数改为4人，其他不变';
for(const driver of ['message_recovery','message_uncertain']) {
 const declared={case_id:'NEW-03',environment:{driver},agent_input:{user_turns:[{message}]}};
 const config=driverConfig({case_id:'NEW-03',driver,message},declared);
 assert.equal(config.driver,driver);
 const body={request_id:'unit-request',location_context:{city:'重庆'},text:message};
 const fingerprint=x=>crypto.createHash('sha256').update(JSON.stringify(x)).digest('hex');
 const payload={...body,request_fingerprint:fingerprint(body)};
 assert(permittedMessage(config,payload,null));
 assert(!permittedMessage(config,{...payload,text:'different'},null));
 const imageBody={...body,image:'not-permitted'};
 assert(!permittedMessage(config,{...imageBody,request_fingerprint:fingerprint(imageBody)},null));
 assert(!permittedMessage(config,payload,{request_id:'different-id',request_fingerprint:payload.request_fingerprint}));
 assert.throws(()=>driverConfig({...config,message:'different'},declared));
 assert.throws(()=>driverConfig(config,{...declared,agent_input:{user_turns:[{message},{message}]}}));
}
assert.equal(driverConfig({case_id:'DEV09'}).driver,'save_restart');
assert.equal(driverConfig({case_id:'DEV-10'}).message,'人数改为3人，其他不变');
assert.throws(()=>driverConfig({case_id:'../escape',driver:'save_restart'}));
assert.throws(()=>driverConfig({case_id:'NEW-03',driver:'message_recovery',message}));
const review={interrupt_id:'review-1',plan_id:'plan-1',plan_version:1};
assert(permittedSave({decision:'save',...review},review));
assert(!permittedSave({decision:'save',...review,plan_version:2},review));
assert(!permittedSave({decision:'prepare',...review},review));
console.log('Desktop declaration and exact POST gates passed; no browser started');
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr

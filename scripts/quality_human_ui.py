#!/usr/bin/env python3
"""Local, per-case human review UI. No actor/model/business operations.

python scripts/quality_human_ui.py --bundle sealed.json --events reviews.jsonl --stage gold
The printed URL grants access to this one local session; do not publish it.
Bundle content is immutable. Only explicit human review events are appended.
"""
from __future__ import annotations

import argparse
import fcntl
import hmac
import json
import os
import secrets
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from quality_human_import import (
    canonical_sha,
    import_reviews,
    required_acknowledgements,
    seal_bundle,
)
from quality_judge import prepare_case
from quality_scoring import require, unique_json_keys


def read_json(path):
    return json.loads(Path(path).read_text(), object_pairs_hook=unique_json_keys)


def read_events(stream):
    stream.seek(0)
    text = stream.read()
    require(not text or text.endswith("\n"), "审核日志末行不完整，请保留文件并检查后继续。")
    return [json.loads(line, object_pairs_hook=unique_json_keys) for line in text.splitlines() if line.strip()]


def append_event(stream, event):
    """Called under the store's exclusive file lock; never rewrite earlier rows."""
    stream.seek(0, os.SEEK_END)
    stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
    stream.flush()
    os.fsync(stream.fileno())


class ReviewStore:
    def __init__(self, bundle_path, events_path, stage):
        require(stage in {"gold", "output"}, "stage must be gold or output")
        self.bundle_path, self.events_path = Path(bundle_path).resolve(), Path(events_path).absolute()
        require(self.bundle_path != self.events_path.resolve(), "bundle and events must be different files")
        if self.events_path.exists():
            require(not os.path.samefile(self.bundle_path, self.events_path), "events must not alias the bundle")
        self.bundle = read_json(self.bundle_path)
        sealed = seal_bundle(self.bundle)
        require(self.bundle == sealed, "请先用 seal_bundle 固定资料版本；界面不会自动修改或重签资料。")
        self.bundle_sha = canonical_sha(self.bundle)
        self.stage = stage
        self.cases = {case["case_id"]: case for case in self.bundle["cases"]}
        self.events_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        import_reviews(self.bundle, self.events())

    def unchanged(self):
        require(canonical_sha(read_json(self.bundle_path)) == self.bundle_sha, "资料版本已变化，请关闭此界面并重新加载新审核包。")

    def events(self):
        if not self.events_path.exists():
            return []
        descriptor = os.open(self.events_path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_SH)
            return read_events(stream)

    def bindings(self, case):
        return {"dataset_sha": self.bundle["dataset_sha"], "product_sha": self.bundle["product_sha"],
                "collection_sha": self.bundle["collection_sha"], "gold_sha": case["gold_sha"], "packet_sha": case["packet_sha"],
                "bundle_sha": self.bundle_sha, "view_sha": canonical_sha({"case": case, "stage": self.stage, "bundle_sha": self.bundle_sha})}

    def progress(self, events=None):
        report = import_reviews(self.bundle, self.events() if events is None else events)
        pending = {item["case_id"]: item["reasons"] for item in report["pending"]}
        events = self.events() if events is None else events
        latest = {(event["case_id"], event["stage"]): event for event in events}
        rows = []
        for case in self.bundle["cases"]:
            event = latest.get((case["case_id"], self.stage))
            problems = pending.get(case["case_id"], [])
            confirmed = bool(event and event["decision"] == "approve" and
                             (not problems if self.stage == "output" else not any(reason.startswith("gold_") for reason in problems)))
            rows.append({"case_id": case["case_id"], "family": case["family"], "confirmed": confirmed,
                         "status": "已确认" if confirmed else "已退回" if event and event["decision"] == "reject" else "待补充" if event else "待审核"})
        confirmed = sum(row["confirmed"] for row in rows)
        return {"stage": self.stage, "dataset": self.bundle["dataset"]["name"], "cases": rows,
                "confirmed": confirmed, "pending": len(rows) - confirmed, "total": len(rows)}

    def case_view(self, case_id):
        self.unchanged()
        require(case_id in self.cases, "unknown case")
        case = self.cases[case_id]
        packet = case["packet"]
        result = {"case_id": case_id, "case_class": case["case_class"], "family": case["family"], "stage": self.stage,
                  "bindings": self.bindings(case), "task": packet["task"], "gold": packet["gold"], "source_packets": packet["source_packets"]}
        if self.stage == "gold":
            result["acknowledgements"] = required_acknowledgements(case, "gold")
        else:
            proposal = case.get("proposed_annotation")
            result["proposed_annotation"] = proposal
            result["proposal_ready"] = isinstance(proposal, dict) and isinstance(proposal.get("claims"), list) and bool(proposal.get("checks"))
            try:
                prepared = prepare_case(packet)
                result.update(references=prepared["references"], required_output_surfaces=prepared["required_output_surfaces"],
                              preparation_issues=prepared["preparation_issues"],
                              acknowledgements=required_acknowledgements(case, "output", proposal if result["proposal_ready"] else {"claims": []}))
            except (ValueError, KeyError, TypeError) as error:
                result.update(proposal_ready=False, preparation_issues=[str(error)], references={}, required_output_surfaces=[], acknowledgements={})
        return result

    def validate_submission(self, payload):
        self.unchanged()
        require(isinstance(payload, dict) and payload.get("stage") == self.stage and payload.get("case_id") in self.cases, "提交必须绑定当前阶段及一题。")
        case = self.cases[payload["case_id"]]
        for field, value in self.bindings(case).items():
            require(payload.get(field) == value, "资料或本题版本不一致，请重新加载该题后审核。")
        require(isinstance(payload.get("reviewer"), str) and 0 < len(payload["reviewer"].strip()) <= 120, "请填写审核人。")
        require(payload.get("decision") in {"approve", "reject"} and payload.get("confirmed_all") is True, "请逐题手动确认已核对全部资料。")
        require(isinstance(payload.get("notes", ""), str) and len(payload.get("notes", "")) <= 20000, "审核备注格式无效。")
        require(payload["decision"] != "reject" or payload.get("notes", "").strip(), "退回该题时请填写原因。")
        require(isinstance(payload.get("event_id"), str), "missing submission identity")
        uuid.UUID(payload["event_id"])
        review = payload.get("review")
        if self.stage == "output":
            proposal = case.get("proposed_annotation")
            require(isinstance(proposal, dict) and isinstance(proposal.get("claims"), list) and bool(proposal.get("checks")), "本题等待AI初标，不能提交空表审核。")
            require(isinstance(review, dict) and review.get("coverage_complete") is True, "请手动确认本题全部事实及输出范围。")
        expected = required_acknowledgements(case, self.stage, review)
        for field, ids in expected.items():
            actual = payload.get(field)
            require(isinstance(actual, list) and len(actual) == len(set(actual)) and set(actual) == set(ids), "已核对材料ID与当前题目或事实列表不一致，请重新确认。")
        return {"event_id": payload["event_id"], "origin": "human_ui", "case_id": case["case_id"], "stage": self.stage,
                "reviewer": payload["reviewer"].strip(), "decision": payload["decision"], "notes": payload.get("notes", ""),
                "confirmed_all": True, **self.bindings(case), **{field: payload[field] for field in expected},
                **({"review": review} if self.stage == "output" else {}), "submission_sha": canonical_sha(payload)}

    def submit(self, payload):
        event = self.validate_submission(payload)
        descriptor = os.open(self.events_path, os.O_CREAT | os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "a+", encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            events = read_events(stream)
            existing = next((row for row in events if row.get("event_id") == event["event_id"]), None)
            if existing:
                require(existing.get("submission_sha") == event["submission_sha"], "该提交编号已用于其他内容，请重新确认。")
                return {"stored": True, "replayed": True, "progress": self.progress(events)}
            event["reviewed_at"] = datetime.now(timezone.utc).isoformat()
            # Schema, source pointers, coverage and previous gold approval use
            # exactly the shared importer. Uncertain judgments remain pending.
            report = import_reviews(self.bundle, [*events, event])
            self.unchanged()
            append_event(stream, event)
            problems = next((item["reasons"] for item in report["pending"] if item["case_id"] == event["case_id"]), [])
            if self.stage == "gold":
                problems = [reason for reason in problems if reason.startswith("gold_")]
            return {"stored": True, "replayed": False, "remaining_issues": problems, "progress": self.progress([*events, event])}


def authorized(headers, token, origin, *, write=False):
    return headers.get("Host") == urlsplit(origin).netloc and hmac.compare_digest(headers.get("Authorization", ""), "Bearer " + token) and (
        headers.get("Origin") == origin if write else headers.get("Origin") in {None, origin})


def handler_for(store, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Do not log tokens, source text or review payloads.

        def reply(self, code, value, content_type="application/json; charset=utf-8"):
            data = value.encode() if isinstance(value, str) else json.dumps(value, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'none'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != urlsplit(origin).netloc:
                self.reply(403, {"error": "host rejected"})
                return
            path = urlsplit(self.path).path
            if path == "/":
                self.reply(200, HTML, "text/html; charset=utf-8")
                return
            if path == "/app.js":
                self.reply(200, JS, "text/javascript; charset=utf-8")
                return
            if not authorized(self.headers, token, origin):
                self.reply(403, {"error": "本地审核会话未认证，请使用本次启动提供的地址。"})
                return
            try:
                store.unchanged()
                if path == "/api/index":
                    self.reply(200, store.progress())
                elif path.startswith("/api/case/"):
                    self.reply(200, store.case_view(unquote(path[len("/api/case/"):])) )
                else:
                    self.reply(404, {"error": "unknown endpoint"})
            except (ValueError, KeyError, TypeError, OSError) as error:
                self.reply(409, {"error": str(error)[:600]})

        def do_POST(self):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if not authorized(self.headers, token, origin, write=True):
                self.reply(403, {"error": "审核请求的会话或来源不匹配。"})
                return
            if self.path != "/api/review" or self.headers.get_content_type() != "application/json":
                self.reply(404, {"error": "only review append is supported"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                require(0 < size <= 4_000_000, "审核提交大小无效。")
                self.connection.settimeout(10)
                payload = json.loads(self.rfile.read(size), object_pairs_hook=unique_json_keys)
                self.reply(200, store.submit(payload))
            except (ValueError, KeyError, TypeError, OSError) as error:
                self.reply(400, {"error": str(error)[:600]})
    return Handler


HTML = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PlanGo · 逐题人工审核</title><style>
:root{color-scheme:light;font-family:Inter,"PingFang SC","Microsoft YaHei",sans-serif;color:#292524;background:#f8f7f3}*{box-sizing:border-box}body{margin:0}header{position:sticky;top:0;background:#fffdf6;border-bottom:1px solid #e8e3d6;padding:17px 25px;z-index:2;display:flex;align-items:center;gap:20px}h1{font-size:20px;margin:0}header p{font-size:12px;color:#78716c;margin:5px 0 0}#reviewer{width:180px;margin-left:auto}main{display:grid;grid-template-columns:210px minmax(0,1fr);max-width:1600px;margin:auto}nav{position:sticky;top:91px;height:calc(100vh - 91px);overflow:auto;padding:18px 12px}nav button{display:block;width:100%;margin-bottom:7px;text-align:left;background:white}nav button.current{background:#fff1b8;border-color:#d7aa14}article{min-width:0;padding:24px 28px 80px}h2{font-size:17px;margin:0 0 14px}h3{font-size:14px;margin:0 0 9px}.card{padding:20px;border:1px solid #e8e3d6;background:white;border-radius:16px;margin-bottom:16px}.row{padding:12px 0;border-bottom:1px solid #eee9df}.row:last-child{border-bottom:0}.muted{color:#78716c;font-size:12px;line-height:1.7}.badge{display:inline-block;background:#fff4c4;border-radius:8px;padding:5px 9px;font-size:12px;margin-bottom:12px}.warning{padding:12px 15px;background:#fff4dd;border:1px solid #f4d697;border-radius:10px;line-height:1.7;font-size:13px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.field{display:block;margin:10px 0;font-size:12px;font-weight:600}.field input,.field select,.field textarea{display:block;width:100%;margin-top:6px;font-weight:400}input,select,textarea,button{font:inherit;font-size:13px;border:1px solid #ddd6c8;border-radius:9px;padding:9px 11px;color:#292524;background:#fff}textarea{min-height:72px;resize:vertical;line-height:1.65}input[type=checkbox]{accent-color:#8a5a00;width:17px;height:17px;margin:0 9px 0 0;vertical-align:middle}button{cursor:pointer}button.primary{background:#ffd100;border-color:#e4bb00;font-weight:650}button:disabled{opacity:.45;cursor:not-allowed}button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible,summary:focus-visible{outline:2px solid #8a5a00;outline-offset:2px}.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:15px}.confirm{display:block;line-height:1.8;font-size:14px;background:#fff8dc;padding:14px;border-radius:10px}pre{font:13px/1.75 "Microsoft YaHei",sans-serif;white-space:pre-wrap;overflow-wrap:anywhere;margin:10px 0;max-height:440px;overflow:auto;background:#faf9f6;padding:14px;border-radius:10px}summary{cursor:pointer;font-size:13px;line-height:1.8}details{margin:12px 0}#notice{white-space:pre-wrap;margin-bottom:16px}code{font-size:11px;overflow-wrap:anywhere}.fact{border:1px solid #e8e3d6;border-radius:12px;padding:15px;margin:12px 0}.support{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}#progress{font-size:12px;color:#78716c;white-space:nowrap}@media(max-width:850px){main{grid-template-columns:140px minmax(0,1fr)}article{padding:18px 12px}.grid,.support{grid-template-columns:1fr}header{padding:12px;gap:8px}#reviewer{width:120px}}
</style><header><div><h1>PlanGo · 逐题人工审核</h1><p id="subtitle">AI建议需要你逐题核对，确认会记录审核人及本题资料版本。</p></div><span id="progress"></span><input id="reviewer" aria-label="审核人" placeholder="填写审核人姓名"></header>
<main><nav id="nav" aria-label="题目导航"></nav><article><div id="notice" role="status"></div><div id="content"><p>正在加载本地审核资料…</p></div></article></main><script src="/app.js"></script></html>'''


JS = r'''
'use strict';
const $=id=>document.getElementById(id), token=new URLSearchParams(location.hash.slice(1)).get('token')||sessionStorage.getItem('review_token');
if(token)sessionStorage.setItem('review_token',token);history.replaceState(null,'',location.pathname);
let index=null,current=null,busy=false,checked=false,pending=null;const drafts=new Map(),errors=new Set();
const copy=value=>JSON.parse(JSON.stringify(value));
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);if(cls)n.className=cls;return n}
function notice(text,error=false){$('notice').textContent=text;$('notice').className=text?(error?'warning':'muted'):''}
async function api(path,body){const r=await fetch(path,{method:body?'POST':'GET',headers:{Authorization:'Bearer '+token,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});const value=await r.json();if(!r.ok)throw Error(value.error||'本地审核服务暂不可用');return value}
function invalidate(){checked=false;pending=null;const n=$('ack');if(n)n.checked=false;if(current?.review)current.review.coverage_complete=false;buttons()}
function field(label,value,update,multiline=false){const l=node('label',label,'field'),input=node(multiline?'textarea':'input');input.value=value??'';input.addEventListener('input',()=>{update(input.value);invalidate()});l.append(input);return l}
function select(label,value,options,update){const l=node('label',label,'field'),input=node('select');for(const [id,text]of options){const o=node('option',text);o.value=id;input.append(o)}input.value=value||'uncertain';input.onchange=()=>{update(input.value);invalidate()};l.append(input);return l}
function jsonEditor(label,value,update){const key=crypto.randomUUID(),l=field(label,JSON.stringify(value??[],null,2),text=>{try{update(JSON.parse(text));errors.delete(key);l.querySelector('textarea').setCustomValidity('')}catch{errors.add(key);l.querySelector('textarea').setCustomValidity('请填写有效JSON')}},true);return l}
function details(label,value,open=false){const d=node('details'),s=node('summary',label);d.open=open;d.append(s,node('pre',typeof value==='string'?value:JSON.stringify(value,null,2)));return d}
function card(title){const c=node('section',undefined,'card');c.append(node('h2',title));$('content').append(c);return c}
const families={reading:'资料读取',offers:'优惠适用性',edits:'修改已有安排',routes:'路线与费用',recovery:'保存与恢复',boundaries:'能力与证据边界'};
function summaryTable(value){const labels={party_size:'同行人数',visit_date:'到店日期',time_window_start:'开始时间',duration_minutes:'活动时长（分钟）',budget:'总预算（元）',per_person_budget:'每人预算（元）',search_radius_km:'搜索半径（公里）',route_distance_km:'单段路程上限（公里）',travel_mode:'出行方式',open_now:'营业状态',reservable:'能否预约',seats_left:'剩余座位',estimated_wait_min:'预计排队（分钟）'};const table=node('table');table.style.cssText='width:100%;font-size:13px;line-height:1.8;border-collapse:collapse';for(const[key,label]of Object.entries(labels)){if(!Object.hasOwn(value||{},key))continue;const row=node('tr'),cell=node('td',value[key]===null?'未提供 / 待核对':typeof value[key]==='boolean'?(value[key]?'是':'否'):({walking:'步行',driving:'驾车',transit:'公交 / 地铁'}[value[key]]||value[key]));row.append(node('th',label),cell);row.firstChild.style.cssText='text-align:left;font-weight:500;color:#78716c;width:45%;padding:5px';cell.style.padding='5px';table.append(row)}return table}
function sourceContent(parent,s){const v=s.content||{};parent.append(node('h3',v.title||(s.source_kind==='explicit_synthetic_world'?'固定运行环境':s.source_id)),node('p',`资料 ${s.source_id} · ${s.observed_at?'采集于 '+s.observed_at:'采集时间未提供'}`,'muted'));
 if(typeof v.text==='string')parent.append(node('pre',v.text));else if(typeof v==='string')parent.append(node('pre',v));
 if(v.initial_state?.spec){parent.append(node('h3','已有安排的初始条件'),summaryTable(v.initial_state.spec));const poi=v.initial_state.spec.selected_poi;if(poi)parent.append(node('p','已选门店：'+(poi.name||'')+' · '+(poi.address||''),'muted'))}
 if(s.source_kind==='explicit_synthetic_world'){if(v.scope)parent.append(node('p',v.scope,'muted'));if(v.origin?.name)parent.append(node('p','固定起点：'+v.origin.name));if(v.route){const r=v.route;parent.append(node('p','预声明单程路线：'+(r.mode==='walking'?'步行':r.mode||'方式未提供')+' · '+(r.distance_km??'未知')+' 公里 · '+(r.walking_min??'未知')+' 分钟 · '+(r.cost_per_person===null||r.cost_per_person===undefined?'费用未提供':r.cost_per_person+' 元/人')))}if(v.supply)parent.append(summaryTable(v.supply));if(v.initial_draft?.unknowns?.length)parent.append(node('p','仍未核验：'+v.initial_draft.unknowns.join('；'),'warning'))}
 parent.append(details('查看完整原始资料、固定配置与来源校验信息',s));}
function resolvePointer(value,pointer){for(const part of pointer.split('/').slice(1))value=value?.[part.replace(/~1/g,'/').replace(/~0/g,'~')];return value}
const verdicts=[['pass','通过 / 未出现禁止项'],['fail','不通过 / 出现禁止项'],['uncertain','尚不能确定']];
const labels=[['supported','有证据支持'],['contradicted','与证据矛盾'],['unsupported','支持不足'],['conflicting','来源冲突'],['unverifiable','材料不足无法复核'],['non_factual','非事实内容'],['duplicate','同范围重复事实']];
function verdictRow(parent,id,description,obj){const r=node('div',undefined,'row');r.append(node('h3',id+' · '+description),select('判定',obj.verdict,verdicts,v=>obj.verdict=v),field('理由',obj.reason,v=>obj.reason=v,true),details('当前支持引用',obj.evidence||[]),jsonEditor('编辑支持引用（ref_id / pointer / quote）',obj.evidence||[],v=>obj.evidence=v));parent.append(r)}
function nav(){const root=$('nav');root.replaceChildren();for(const row of index.cases){const b=node('button',row.case_id+' · '+row.status,row.case_id===current?.data.case_id?'current':'');b.type='button';b.onclick=()=>openCase(row.case_id);root.append(b)}$('progress').textContent=`已确认 ${index.confirmed} / ${index.total} · 待审核 ${index.pending}`}
async function openCase(id){if(busy)return;try{const data=await api('/api/case/'+encodeURIComponent(id));let draft=drafts.get(id);if(!draft||draft.data.bindings.view_sha!==data.bindings.view_sha){draft={data,review:data.proposal_ready?copy(data.proposed_annotation):null,notes:''};drafts.set(id,draft)}current=draft;current.data=data;invalidate();render();nav();notice('')}catch(e){notice(e.message,true)}}
function render(){errors.clear();$('content').replaceChildren();const d=current.data;current.review&&(current.review.coverage_complete=false);checked=false;
 $('subtitle').textContent=d.stage==='gold'?'标准审核：先核对资料和验收条件，每题独立确认。':'输出审核：AI初标仅作参考；确认标注不等于认定任务成功。';
 const intro=card(d.case_id+' · '+(d.stage==='gold'?'审核资料与标准':'审核实际输出'));
 intro.append(node('span','受控验收 · '+(families[d.family]||d.family),'badge'));const synthetic=d.source_packets.some(s=>/synthetic|simulated|controlled|合成|受控/i.test(String(s.source_kind||s.kind||'')+' '+String(s.content?.fixture_kind||'')));
 if(synthetic)intro.append(node('p','本题包含明确合成资料，用于受控验收；不是真实商家或交易事实。','warning'));
 intro.append(node('h3','用户要求'));for(const turn of d.task.agent_input?.user_turns||[])intro.append(node('pre',turn.message));
 if(!d.task.agent_input?.user_turns?.length)intro.append(node('p','本题没有新聊天输入，按预声明的界面动作验收。','muted'));
 intro.append(node('p',d.task.scope||'范围见原始任务'));if(d.task.environment?.initial_state?.spec)intro.append(summaryTable(d.task.environment.initial_state.spec));intro.append(details('初态、固定环境与驱动动作',d.task.environment||{}),details('本题版本绑定',d.bindings));
 const sources=card('原始资料');for(const s of d.source_packets){const row=node('div',undefined,'row');sourceContent(row,s);sources.append(row)}
 if(!d.source_packets.length)sources.append(node('p','本题没有来源包；请核对是否符合题目设计。','warning'));
 const rubric=card('必要条件与禁止项');for(const check of d.gold.must_pass)rubric.append(node('div',check.id+' · '+(check.description||JSON.stringify(check)),'row'));
 d.gold.forbidden_claims.forEach((text,i)=>rubric.append(node('div',`F${i+1} · 禁止：${text}`,'row')));rubric.append(details('允许的等价表述与补充标准',d.gold.accepted_variations||[]));
 if(d.stage==='output')renderOutput();
 const finish=card('确认当前这一题');finish.append(field('本题备注（退回时必填）',current.notes,v=>current.notes=v,true));
 const label=node('label',undefined,'confirm'),ack=node('input');ack.type='checkbox';ack.id='ack';ack.onchange=()=>{checked=ack.checked;if(current.review)current.review.coverage_complete=checked;pending=null;buttons()};label.append(ack,document.createTextNode(d.stage==='gold'?'我已核对本题全部原始资料、必要条件和禁止项。':'我已核对本题全部事实、标签、支持引用及所有输出范围。'));finish.append(label);
 finish.append(details('本次确认涵盖的材料ID',acks()));const actions=node('div',undefined,'actions');for(const [id,text,decision]of [['approve','记录本题审核','approve'],['reject','退回本题并说明原因','reject']]){const b=node('button',text,id==='approve'?'primary':'');b.id=id;b.type='button';b.onclick=()=>submit(decision);actions.append(b)}
 const next=node('button','下一题');next.type='button';next.onclick=()=>{const i=index.cases.findIndex(row=>row.case_id===d.case_id);if(i+1<index.cases.length)openCase(index.cases[i+1].case_id)};actions.append(next);finish.append(actions,node('p','修改任一标注、理由或审核人后，必须重新勾选本题确认。不会一次确认其他题。','muted'));buttons();}
function acks(){const result=copy(current.data.acknowledgements||{});if(current.data.stage==='output'&&current.review)result.reviewed_claim_ids=(current.review.claims||[]).map(c=>c.claim_id);return result}
function renderOutput(){const d=current.data,output=card('实际交付输出');for(const s of d.required_output_surfaces||[]){const r=node('div',undefined,'row');r.append(node('code',s.ref_id+'#'+s.pointer),node('pre',resolvePointer(d.references?.[s.ref_id],s.pointer)??'该输出位置不可用'));output.append(r)}
 output.append(details('独立状态与传输记录',{independent_state:d.references?.independent_state,transport_checks:d.references?.transport_checks}));
 if(d.preparation_issues?.length)output.append(node('p','待补充材料：'+d.preparation_issues.join('；'),'warning'));
 if(!d.proposal_ready){output.append(node('p','等待AI初标：当前没有可供逐条审核的标注表，不能用空表确认。','warning'));return}
 output.append(details('AI建议原始标注（尚未人工确认）',d.proposed_annotation));const review=current.review;
 const checks=card('逐项核对结果');for(const [name,items]of [['checks',d.gold.must_pass],['forbidden_checks',d.gold.forbidden_claims.map((text,i)=>({id:'F'+(i+1),description:text}))]]){review[name]||={};for(const item of items){review[name][item.id]||={verdict:'uncertain',reason:'',evidence:[]};verdictRow(checks,item.id,item.description,review[name][item.id])}}
 for(const [name,title]of [['side_effects_absent','无越权或重复副作用'],['false_completion_absent','无误报业务完成']]){review[name]||={verdict:'uncertain',reason:'',evidence:[]};verdictRow(checks,name,title,review[name])}
 const claims=card('事实清单与证据');review.claims.forEach((claim,i)=>{const box=node('section',undefined,'fact');box.append(node('h3',claim.claim_id),field('原子事实（保留金额、人数、时间与确定性）',claim.canonical_claim,v=>claim.canonical_claim=v,true),select('事实标签',claim.label,labels,v=>{claim.label=v;if(v!=='duplicate')delete claim.duplicate_of}),field('判定理由',claim.reason,v=>claim.reason=v,true));
 const grid=node('div',undefined,'grid');grid.append(field('实体 / 分店',claim.entity_id,v=>claim.entity_id=v),field('时间、人数及适用范围',claim.temporal_scope,v=>claim.temporal_scope=v));box.append(grid);
 box.append(jsonEditor('输出原文定位',claim.output||{},v=>claim.output=v),jsonEditor('支持材料定位',claim.evidence||[],v=>claim.evidence=v),field('若为重复事实，填写更早的事实ID',claim.duplicate_of||'',v=>{if(v.trim())claim.duplicate_of=v.trim();else delete claim.duplicate_of}));
 const support=node('div',undefined,'support');claim.support_checks||={};for(const [key,label]of [['locatable','原文可定位'],['available_before_output','输出前已取得'],['same_entity','同一实体'],['time_scope','同一时间与范围'],['meaning_units','含义单位一致'],['derivation_conflicts','推导及冲突处理正确']])support.append(select(label,claim.support_checks[key],verdicts,v=>claim.support_checks[key]=v));box.append(support);
 const remove=node('button','删除这条初标');remove.type='button';remove.onclick=()=>{review.claims.splice(i,1);invalidate();render()};box.append(remove);claims.append(box)});
 const add=node('button','补充遗漏的事实');add.type='button';add.onclick=()=>{let n=1;while(review.claims.some(c=>c.claim_id==='C'+n))n++;review.claims.push({claim_id:'C'+n,canonical_claim:'',entity_id:'',temporal_scope:'',label:'unverifiable',reason:'',output:{ref_id:'',pointer:'',quote:''},evidence:[],support_checks:{}});invalidate();render()};claims.append(add);
 const coverage=card('逐处确认输出覆盖');review.surface_coverage||=[];for(const s of d.required_output_surfaces||[]){let row=review.surface_coverage.find(r=>r.ref_id===s.ref_id&&r.pointer===s.pointer);if(!row){row={...s,claim_ids:[],non_factual_reason:''};review.surface_coverage.push(row)}const box=node('div',undefined,'row');box.append(node('code',s.ref_id+'#'+s.pointer),field('对应事实ID（逗号分隔，可复用同一事实）',(row.claim_ids||[]).join(', '),v=>row.claim_ids=v.split(/[,，]/).map(x=>x.trim()).filter(Boolean)),field('若此处没有事实，说明排除理由',row.non_factual_reason||'',v=>row.non_factual_reason=v,true));coverage.append(box)}coverage.append(field('完整性说明与尚未裁决事项',review.coverage_notes||'',v=>review.coverage_notes=v,true));}
function buttons(){const unavailable=busy||!current||!checked||!$('reviewer').value.trim()||errors.size>0||(current.data.stage==='output'&&!current.data.proposal_ready);if($('approve'))$('approve').disabled=!!unavailable;if($('reject'))$('reject').disabled=!!unavailable||!current.notes.trim()}
$('reviewer').addEventListener('input',invalidate);
async function submit(decision){if(busy)return;buttons();if($(decision).disabled)return;busy=true;buttons();const d=current.data;
 const payload=pending&&pending.decision===decision?pending:{event_id:crypto.randomUUID(),case_id:d.case_id,stage:d.stage,reviewer:$('reviewer').value,decision,notes:current.notes,confirmed_all:checked,...d.bindings,...acks(),...(d.stage==='output'?{review:copy(current.review)}:{})};pending=payload;
 try{const result=await api('/api/review',payload);index=result.progress;pending=null;invalidate();nav();notice(result.remaining_issues?.length?'审核已记录；以下问题仍待补充：\n'+result.remaining_issues.join('\n'):'本题审核已记录并保存。可继续下一题。')}
 catch(e){notice(e.message+'\n输入仍保留；如网络中断，再点原按钮会核对同一提交。',true)}finally{busy=false;buttons()}}
(async()=>{try{index=await api('/api/index');nav();if(index.cases.length)await openCase(index.cases.find(c=>!c.confirmed)?.case_id||index.cases[0].case_id)}catch(e){notice(e.message,true)}})();
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--stage", required=True, choices=["gold", "output"])
    parser.add_argument("--ready-file", type=Path, help="Optional private readiness JSON; existing file is refused")
    args = parser.parse_args()
    os.umask(0o077)
    store = ReviewStore(args.bundle, args.events, args.stage)
    token = secrets.token_urlsafe(32)
    server = HTTPServer(("127.0.0.1", 0), handler_for(store, token))
    ready = {"url": f"http://127.0.0.1:{server.server_port}/#token={token}", "stage": args.stage,
             "pid": os.getpid(), "cases": len(store.cases), "events": str(store.events_path)}
    if args.ready_file:
        with args.ready_file.open("x", encoding="utf-8") as stream:
            json.dump(ready, stream, ensure_ascii=False)
            stream.write("\n")
        args.ready_file.chmod(0o600)
    print(json.dumps(ready, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

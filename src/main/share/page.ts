// 手机端只读方案页（自包含 HTML，前端 fetch /api/s/:id 渲染）。
// 布局参考 yoyu share.html（时间线 + POI 图 + 评分/人均/推荐），
// 协作参考 weplan（👍/🤔/🙅 投票 + 留想法/预算）。
export function renderSharePage(id: string): string {
  return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<title>PlanGo · 给你的周末安排</title>
<style>
  :root { --brand:#bce8d4; --ink:#183e32; --ink2:#63786a; --bg:#f2f5f1; --card:#fff; --line:#dfe8e0; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif; background:var(--bg); color:var(--ink); }
  .wrap { max-width:600px; margin:0 auto; padding:24px 18px 48px; }
  .banner { background:linear-gradient(135deg,#edf7f1,#d0eadb); border:1px solid #c5dfcf; border-radius:24px; padding:24px; margin-bottom:16px; }
  .banner .tag { font-size:12px; color:#296c53; font-weight:600; letter-spacing:1px; }
  .banner h1 { font-size:24px; margin:12px 0 10px; line-height:1.4; }
  .banner .meta { font-size:12px; color:var(--ink2); line-height:1.8; }
  .readonly { font-size:12px; color:var(--ink2); margin:12px 2px 16px; line-height:1.8; }
  .notice { background:#fffbef; border:1px solid #ecdcb1; border-radius:16px; color:#816025; padding:16px; font-size:12px; line-height:1.8; margin-bottom:16px; }
  .notice ul { padding-left:18px; margin:6px 0 0; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:16px; padding:14px; margin-bottom:12px; }
  .node { display:flex; gap:10px; padding:10px 0; border-bottom:1px dashed var(--line); }
  .node:last-child { border-bottom:none; }
  .photo { position:relative; width:72px; height:72px; border-radius:16px; background:#edf3ee; flex:none; display:grid; place-items:center; color:#829288; font-size:10px; overflow:hidden; }
  .photo img { position:absolute; width:100%; height:100%; object-fit:cover; }
  .node .t { font-size:12px; color:var(--ink2); }
  .node .n { font-weight:600; margin:2px 0; }
  .node .s { font-size:12px; color:var(--ink2); }
  .tags { margin-top:4px; }
  .tags span { display:inline-block; font-size:10px; background:#edf7f1; color:#296c53; border-radius:20px; padding:2px 8px; margin:2px 4px 0 0; }
  .cta { display:flex; gap:8px; margin:6px 0 14px; }
  .btn { flex:1; border:none; border-radius:12px; padding:12px; font-size:14px; font-weight:600; cursor:pointer; }
  .btn.up { background:var(--brand); color:var(--ink); }
  .btn.meh { background:#eef1f4; color:#555; }
  .btn.down { background:#fdeaea; color:#c0392b; }
  .btn.sel { outline:3px solid #70a68b; outline-offset:2px; }
  button:focus-visible,input:focus-visible,textarea:focus-visible { outline:3px solid #70a68b; outline-offset:2px; }
  button:disabled { opacity:.55; cursor:wait; }
  .idea { width:100%; border:1px solid var(--line); border-radius:12px; padding:10px; font-size:14px; font-family:inherit; resize:vertical; min-height:64px; }
  .row { display:flex; gap:8px; margin-top:8px; align-items:center; }
  .row input[type=text],.row input[type=number] { flex:1; min-width:0; border:1px solid var(--line); border-radius:10px; padding:9px; font-size:14px; }
  .send { background:var(--ink); color:#fff; border:none; border-radius:10px; padding:10px 14px; font-size:14px; font-weight:600; }
  .done { text-align:center; color:#1a9e5a; font-size:14px; padding:10px; }
  .foot { text-align:center; color:var(--ink2); font-size:12px; margin-top:16px; }
  .err { text-align:center; color:#c0392b; padding:40px 10px; }
</style>
</head>
<body>
<div class="wrap" id="app"><div class="err">加载中…</div></div>
<script>
const ID = ${JSON.stringify(id)};
const esc = (s) => String(s==null?'':s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const imageUrl = value => { try { const u=new URL(value); return ['https:','http:'].includes(u.protocol)?esc(u.href):''; } catch { return ''; } };
async function load() {
  let data;
  try { data = await fetch('/api/s/'+ID).then(r=>r.json()); } catch(e){ data = {error:'net'}; }
  const app = document.getElementById('app');
  if (!data || data.error || !data.plan) { app.innerHTML = '<div class="err">这个分享失效了，让 TA 再发一次～</div>'; return; }
  const p = data.plan;
  const nodes = (p.nodes||[]).map(n => {
    const poi = n.poi || {};
    const img = imageUrl(poi.image);
    const score = poi.filtered_score || poi.raw_score;
    const rec = (poi.recommended||[]).slice(0,3).map(r=>'<span>'+esc(r)+'</span>').join('');
    return '<div class="node"><div class="photo"><span>暂无实景图</span>'+(img?'<img alt="'+esc(poi.name||n.title)+'" src="'+img+'" onerror="this.remove()"/>':'')+'</div><div><div class="t">'+esc(n.time_start)+(n.time_end?('–'+esc(n.time_end)):'')+'</div><div class="n">'+esc(n.title)+'</div><div class="s">'+(score?('★'+esc(score)+' '):'')+(poi.price_per_person?('· 人均¥'+esc(poi.price_per_person)):'')+'</div><div class="s">'+esc(n.reason||'')+'</div><div class="tags">'+rec+'</div></div></div>';
  }).join('');
  const notes = Array.isArray(p.validation_notes) ? p.validation_notes : [];
  const pending = notes.length || (p.nodes||[]).some(n=>n.verify_state==='suggested');
  app.innerHTML =
    '<div class="banner"><div class="tag">PlanGo · 一起安排下一站</div><h1>'+esc(p.title||'出行安排')+'</h1>'+
    '<div class="meta">'+esc(data.city||'')+(p.visit_date?' · '+esc(p.visit_date):'')+' · '+(typeof p.total_cost==='number'?'合计约¥'+p.total_cost:'费用待核验')+' · '+(p.nodes||[]).length+' 站'+(p.total_travel_min?(' · 通勤约'+p.total_travel_min+'分钟'):'')+'</div></div>'+
    '<div class="readonly">这是分享时的方案快照。你可以投票或留下建议，发起人确认并入后再调整安排。</div>'+
    (pending?'<div class="notice"><b>待核验草案 · 不代表已预约或可直接执行</b><ul>'+notes.map(n=>'<li>'+esc(n)+'</li>').join('')+'</ul></div>':'')+
    '<div class="card">'+nodes+'</div>'+
    '<div class="cta">'+
      '<button class="btn up" onclick="vote(\\'up\\',this)">👍 可以</button>'+
      '<button class="btn meh" onclick="vote(\\'meh\\',this)">🤔 一般</button>'+
      '<button class="btn down" onclick="vote(\\'down\\',this)">🙅 不行</button>'+
    '</div><div id="votestatus" role="status" class="readonly"></div>'+
    '<div class="card"><div style="font-weight:600;margin-bottom:8px">💬 提点想法（可选）</div>'+
      '<textarea class="idea" id="idea" placeholder="比如：正餐想吃清淡点 / 想多个室内的 / 预算再低点…"></textarea>'+
      '<div class="row"><input type="text" id="who" placeholder="你的昵称（可选）"/><input type="number" id="budget" placeholder="期望人均¥"/></div>'+
      '<div class="row"><button class="send" style="flex:1" onclick="sendIdea(this)">发送给 TA</button></div>'+
      '<div id="ideadone"></div>'+
    '</div>'+
    '<div class="foot">PlanGo · 请在能访问分享电脑的网络中打开</div>';
}
async function vote(v, btn) {
  const who = (document.getElementById('who')||{}).value || '朋友';
  const buttons=document.querySelectorAll('.cta .btn');
  buttons.forEach(b=>b.disabled=true);
  try {
    const response=await fetch('/api/s/'+ID+'/vote',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({voter:who,vote:v})});
    if(!response.ok) throw new Error('not_saved');
    buttons.forEach(b=>b.classList.remove('sel')); btn.classList.add('sel');
    document.getElementById('votestatus').textContent='投票已保存，可随时修改。';
  } catch(e) { document.getElementById('votestatus').textContent='未收到投票确认，请检查连接后重试。'; }
  finally { buttons.forEach(b=>b.disabled=false); }
}
async function sendIdea(btn) {
  const idea = (document.getElementById('idea')||{}).value || '';
  const who = (document.getElementById('who')||{}).value || '朋友';
  const budget = (document.getElementById('budget')||{}).value || '';
  if (!idea && !budget) { document.getElementById('ideadone').innerHTML='<div class="done">写点想法或期望预算再发哦～</div>'; return; }
  btn.disabled = true;
  try {
    const response=await fetch('/api/s/'+ID+'/pref',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({member:who,idea:idea,budget:budget})});
    if(!response.ok) throw new Error('not_saved');
    document.getElementById('ideadone').textContent='意见已保存，等待发起人确认并入方案。';
  } catch(e) { document.getElementById('ideadone').textContent='未收到发送确认，请检查连接后重试。'; btn.disabled=false; }
}
load();
</script>
</body>
</html>`
}

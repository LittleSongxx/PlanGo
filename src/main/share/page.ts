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
  :root { --brand:#ffb800; --ink:#1a1a1a; --ink2:#8a8a8a; --bg:#f6f6f7; --card:#fff; --line:#ececec; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif; background:var(--bg); color:var(--ink); }
  .wrap { max-width:520px; margin:0 auto; padding:14px 14px 40px; }
  .banner { background:linear-gradient(135deg,#fff5d6,#ffe49b); border-radius:18px; padding:16px; margin-bottom:12px; }
  .banner .tag { font-size:12px; color:#9a7b12; font-weight:600; }
  .banner h1 { font-size:19px; margin:6px 0 4px; }
  .banner .meta { font-size:12px; color:#7a6a2a; }
  .readonly { font-size:12px; color:var(--ink2); margin:8px 2px 12px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:16px; padding:14px; margin-bottom:12px; }
  .node { display:flex; gap:10px; padding:10px 0; border-bottom:1px dashed var(--line); }
  .node:last-child { border-bottom:none; }
  .node img { width:64px; height:64px; border-radius:12px; object-fit:cover; background:#eee; flex:none; }
  .node .t { font-size:12px; color:var(--ink2); }
  .node .n { font-weight:600; margin:2px 0; }
  .node .s { font-size:12px; color:var(--ink2); }
  .tags { margin-top:4px; }
  .tags span { display:inline-block; font-size:10px; background:#fff6db; color:#9a7b12; border-radius:20px; padding:2px 8px; margin:2px 4px 0 0; }
  .cta { display:flex; gap:8px; margin:6px 0 14px; }
  .btn { flex:1; border:none; border-radius:12px; padding:12px; font-size:14px; font-weight:600; cursor:pointer; }
  .btn.up { background:var(--brand); color:#5a4400; }
  .btn.meh { background:#eef1f4; color:#555; }
  .btn.down { background:#fdeaea; color:#c0392b; }
  .btn.sel { outline:3px solid rgba(255,184,0,.4); }
  .idea { width:100%; border:1px solid var(--line); border-radius:12px; padding:10px; font-size:14px; font-family:inherit; resize:vertical; min-height:64px; }
  .row { display:flex; gap:8px; margin-top:8px; align-items:center; }
  .row input[type=text],.row input[type=number] { flex:1; border:1px solid var(--line); border-radius:10px; padding:9px; font-size:14px; }
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
const CAT_IMG = { dining:'https://images.unsplash.com/photo-1517248135467-4c7edcad34c4?w=400&q=70', activity:'https://images.unsplash.com/photo-1533107862482-0e6974b06ec4?w=400&q=70' };
let myVote = '';
async function load() {
  let data;
  try { data = await fetch('/api/s/'+ID).then(r=>r.json()); } catch(e){ data = {error:'net'}; }
  const app = document.getElementById('app');
  if (!data || data.error || !data.plan) { app.innerHTML = '<div class="err">这个分享失效了，让 TA 再发一次～</div>'; return; }
  const p = data.plan;
  const nodes = (p.nodes||[]).map(n => {
    const poi = n.poi || {};
    const img = poi.image || CAT_IMG[n.category==='dining'?'dining':'activity'];
    const score = poi.filtered_score || poi.raw_score;
    const rec = (poi.recommended||[]).slice(0,3).map(r=>'<span>'+esc(r)+'</span>').join('');
    return '<div class="node"><img src="'+esc(img)+'" onerror="this.src=CAT_IMG.dining"/><div><div class="t">'+esc(n.time_start)+(n.time_end?('–'+esc(n.time_end)):'')+'</div><div class="n">'+esc(n.title)+'</div><div class="s">'+(score?('★'+score+' '):'')+(poi.price_per_person?('· 人均¥'+poi.price_per_person):'')+'</div><div class="s">💡 '+esc(n.reason||'')+'</div><div class="tags">'+rec+'</div></div></div>';
  }).join('');
  const per = typeof p.total_cost==='number' && p.party_size>0 ? Math.round(p.total_cost/p.party_size) : null;
  app.innerHTML =
    '<div class="banner"><div class="tag">有人给你分享了一套周末方案 🎁</div><h1>'+esc(p.title||'周末安排')+'</h1>'+
    '<div class="meta">'+esc(data.city||'')+' · '+(typeof p.total_cost==='number'?'合计约¥'+p.total_cost:'费用待核验')+' · '+(p.nodes||[]).length+' 站'+(p.total_travel_min?(' · 通勤约'+p.total_travel_min+'分钟'):'')+'</div></div>'+
    '<div class="readonly">👀 只读分享页：看看行程，投个票，或写句想法给 TA，PlanGo会据此改方案。</div>'+
    '<div class="card">'+nodes+'</div>'+
    '<div class="cta">'+
      '<button class="btn up" onclick="vote(\\'up\\',this)">👍 可以</button>'+
      '<button class="btn meh" onclick="vote(\\'meh\\',this)">🤔 一般</button>'+
      '<button class="btn down" onclick="vote(\\'down\\',this)">🙅 不行</button>'+
    '</div>'+
    '<div class="card"><div style="font-weight:600;margin-bottom:8px">💬 提点想法（可选）</div>'+
      '<textarea class="idea" id="idea" placeholder="比如：正餐想吃清淡点 / 想多个室内的 / 预算再低点…"></textarea>'+
      '<div class="row"><input type="text" id="who" placeholder="你的昵称（可选）"/><input type="number" id="budget" placeholder="期望人均¥"/></div>'+
      '<div class="row"><button class="send" style="flex:1" onclick="sendIdea(this)">发送给 TA</button></div>'+
      '<div id="ideadone"></div>'+
    '</div>'+
    '<div class="foot">由「PlanGo · AI 本地生活浏览器」生成 · 仅同一 WiFi 可见</div>';
}
async function vote(v, btn) {
  myVote = v;
  document.querySelectorAll('.cta .btn').forEach(b=>b.classList.remove('sel'));
  btn.classList.add('sel');
  const who = (document.getElementById('who')||{}).value || '朋友';
  try { await fetch('/api/s/'+ID+'/vote',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({voter:who,vote:v})}); } catch(e){}
}
async function sendIdea(btn) {
  const idea = (document.getElementById('idea')||{}).value || '';
  const who = (document.getElementById('who')||{}).value || '朋友';
  const budget = (document.getElementById('budget')||{}).value || '';
  if (!idea && !budget) { document.getElementById('ideadone').innerHTML='<div class="done">写点想法或期望预算再发哦～</div>'; return; }
  btn.disabled = true;
  try { await fetch('/api/s/'+ID+'/pref',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({member:who,idea:idea,budget:budget})}); } catch(e){}
  document.getElementById('ideadone').innerHTML='<div class="done">已发给 TA ✅ PlanGo会参考你的意见改方案</div>';
}
load();
</script>
</body>
</html>`
}

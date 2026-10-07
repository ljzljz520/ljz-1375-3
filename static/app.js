/* 沙漠驿站资料馆 — 前端。
   原则：所有“当前状态”均来自 /api（服务端实际发布记录）；
   前端不根据同名、未批准关系或未证实坐标自行推断。 */
'use strict';

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const lowbw = () => $('#lowbw').checked;

const TYPE_NAME = {well: '水井', caravan: '商队', dwelling: '民居', craft: '工艺'};
const CAT_NAME = {evidence: '历史证据', speculation: '口述推测', tip: '当代提示'};
const SUPPLY_NAME = {open: '开放', closed: '已关闭', temporarily_closed: '临时关闭'};
const REL_NAME = {uses_well: '使用水井', lodges_at: '驻留', draws_from: '取材于',
                  practiced_at: '从业于', serves: '当代供给'};

let entitiesCache = [];
let sourcesCache = [];

function toast(msg, isErr) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast' + (isErr ? ' err' : '');
  setTimeout(() => t.classList.add('hidden'), 3500);
  t.classList.remove('hidden');
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: {'Content-Type': 'application/json'},
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.message || ('HTTP ' + res.status));
    err.code = data.error;
    throw err;
  }
  return data;
}

/* ------------------------------------------------------------- tabs */
$$('.tab').forEach(btn => btn.addEventListener('click', () => {
  $$('.tab').forEach(b => b.classList.toggle('active', b === btn));
  $$('.tabpane').forEach(p => p.classList.remove('active'));
  $('#tab-' + btn.dataset.tab).classList.add('active');
  if (btn.dataset.tab === 'graph') loadGraph();
  if (btn.dataset.tab === 'travel') loadItineraries();
  if (btn.dataset.tab === 'admin') loadAdmin();
}));
$('#lowbw').addEventListener('change', () => { loadBrowse(currentFilter); loadGraph(); });

/* ------------------------------------------------------------- browse */
let currentFilter = 'all';
$$('.fbtn').forEach(b => b.addEventListener('click', () => {
  $$('.fbtn').forEach(x => x.classList.toggle('active', x === b));
  currentFilter = b.dataset.type;
  loadBrowse(currentFilter);
}));

async function loadBrowse(type = 'all') {
  const qs = new URLSearchParams();
  if (type !== 'all') qs.set('type', type);
  qs.set('lowbw', lowbw() ? '1' : '0');   // logged server-side; rendering honors published docs
  const data = await api('/api/browse?' + qs);
  const groups = type === 'all' ? data : {[type]: data};
  const grid = $('#browseGrid');
  grid.innerHTML = '';
  let newest = '';
  Object.entries(groups).forEach(([etype, list]) => {
    list.forEach(e => {
      if (e.published_at > newest) newest = e.published_at;
      const div = document.createElement('div');
      div.className = 'entity';
      let supplyBadge = '';
      if (e.supply) {
        supplyBadge = `<span class="badge b-${e.supply.status}">${SUPPLY_NAME[e.supply.status]}</span>`;
      }
      div.innerHTML = `<h4>${e.name} ${supplyBadge}</h4>
        <div class="era"><span class="badge b-${etype}">${TYPE_NAME[etype]}</span> ${e.era || ''}</div>
        <div class="src">发布于 ${e.published_at} · rev ${e.revision}${e.photo_count ? ' · 📷' + e.photo_count : ''}</div>`;
      div.onclick = () => loadArticle(e.id);
      grid.appendChild(div);
    });
  });
  $('#pubtime').textContent = newest ? `页面状态以服务端发布为准（最近发布 ${newest}）` : '';
  if (!grid.children.length) grid.innerHTML = '<p class="hint">该分类暂无已发布内容。</p>';
}

async function loadArticle(id) {
  const view = $('#articleView');
  view.classList.remove('hidden');
  view.innerHTML = '<p>加载服务端发布版本…</p>';
  try {
    const {doc, revision, published_at, cards} = await api('/api/article?entity_id=' + id);
    const ent = doc.entity;
    const stmts = doc.statements.map(s => `
      <div class="stmt ${s.category}">
        <b>${s.predicate}</b>：${s.value}${s.value_ref ? ' → 关联实体' : ''}
        <span class="tag ${s.confidence}">${s.confidence}</span>
        <div class="src">[${CAT_NAME[s.category]}] 来源：${s.source.title}
          ${s.source.citation ? '（' + s.source.citation + '）' : ''}
          ${s.source.authored_at ? '· ' + s.source.authored_at : ''}</div>
      </div>`).join('') || '<p class="hint">暂无已批准陈述。</p>';
    const rels = doc.relations.map(r =>
      `<span class="relchip">${r.direction === 'out' ? '→' : '←'} ${REL_NAME[r.rtype]}
        ${r.other.name}（${TYPE_NAME[r.other.etype]}${r.other.era ? '·' + r.other.era : ''}）</span>`
    ).join('') || '<span class="hint">无已批准关系</span>';
    let loc = '<span class="hint">无定位记录</span>';
    if (doc.location) {
      if (doc.location.precision === 'exact' && doc.location.lat != null)
        loc = `精确坐标 ${doc.location.lat.toFixed(4)}, ${doc.location.lng.toFixed(4)}（证据已批准）`;
      else if (doc.location.approximate)
        loc = `⚠ 仅为近似位置 ${doc.location.lat?.toFixed(3)}, ${doc.location.lng?.toFixed(3)}，非精确图钉`;
      else loc = '📍 ' + (doc.location.note || '未定位');
    }
    const photos = doc.photos.length
      ? doc.photos.map(p => `<div class="photo">📷 ${p.caption}
          <div class="src">许可 ${p.license}${p.license_until ? ' · 截止 ' + p.license_until : ''}</div></div>`).join('')
      : '<p class="hint">无有效授权照片（许可过期的照片不会发布）。</p>';
    let supply = '';
    if (doc.supply) {
      const wins = doc.supply.windows.map(w =>
        `<li>${w.valid_from} ~ ${w.valid_to} ${w.season_note ? '· ' + w.season_note : ''}</li>`).join('');
      supply = `<h4>当代服务（独立服务实体）</h4>
        <p>状态：<b class="b-${doc.supply.status}">${SUPPLY_NAME[doc.supply.status]}</b>
        ｜服务：${(doc.supply.services || []).join('、') || '—'}</p>
        <ul>${wins}</ul>
        <div class="src">状态更新于 ${doc.supply.updated_at}；历史井址与此处为不同实体，需经已批准关系才可视为可补给。</div>`;
    }
    const lbNote = doc.low_bandwidth
      ? '<div class="lowbw-note">低带宽模式：已省略照片与坐标，但保留时间与来源说明。</div>' : '';
    view.innerHTML = `
      <button onclick="document.getElementById('articleView').classList.add('hidden')">← 返回列表</button>
      <h2>${ent.name} <span class="badge b-${ent.etype}">${TYPE_NAME[ent.etype]}</span>
        <span class="era">${ent.era || ''}</span></h2>
      <p>${ent.summary || ''}</p>${lbNote}
      <h4>已批准陈述（含历史证据 / 口述推测 / 当代提示）</h4>${stmts}
      <h4>已批准关系</h4><div>${rels}</div>
      <h4>位置</h4><p>${loc}</p>${supply}
      <h4>照片</h4>${photos}
      <div class="src">本页内容来自服务端发布 rev ${revision}（${published_at}）。</div>
      <h4>打印卡</h4><pre class="card" style="white-space:pre-wrap">${cards.default?.text || ''}</pre>`;
  } catch (e) { view.innerHTML = `<p class="warn">${e.message}</p>`; }
}

/* ------------------------------------------------------------- graph */
async function loadGraph() {
  const data = await api('/api/graph');
  const svg = $('#mapSvg');
  svg.innerHTML = '';
  const located = data.nodes.filter(n => n.located);
  const unlocated = data.nodes.filter(n => !n.located);
  const lats = located.map(n => n.pin.lat), lngs = located.map(n => n.pin.lng);
  const W = 800, H = 460, PAD = 70;
  const project = (lat, lng) => {
    if (located.length === 1) return [W / 2, H / 2];
    const minLa = Math.min(...lats), maxLa = Math.max(...lats);
    const minLn = Math.min(...lngs), maxLn = Math.max(...lngs);
    const x = PAD + (lng - minLn) / Math.max(maxLn - minLn, 1e-9) * (W - 2 * PAD);
    const y = H - PAD - (lat - minLa) / Math.max(maxLa - minLa, 1e-9) * (H - 2 * PAD);
    return [x, y];
  };
  const pos = {};
  located.forEach(n => { pos[n.id] = project(n.pin.lat, n.pin.lng); });

  const NS = 'http://www.w3.org/2000/svg';
  data.edges.forEach(e => {
    if (!pos[e.from] || !pos[e.to]) return; // edge to unlocated can't be drawn geographically
    const [x1, y1] = pos[e.from], [x2, y2] = pos[e.to];
    const line = document.createElementNS(NS, 'line');
    line.setAttribute('x1', x1); line.setAttribute('y1', y1);
    line.setAttribute('x2', x2); line.setAttribute('y2', y2);
    line.setAttribute('stroke', '#9c5a2e'); line.setAttribute('stroke-width', 2);
    svg.appendChild(line);
    const t = document.createElementNS(NS, 'text');
    t.setAttribute('x', (x1 + x2) / 2); t.setAttribute('y', (y1 + y2) / 2 - 4);
    t.setAttribute('font-size', 11); t.setAttribute('fill', '#7a4a22');
    t.textContent = e.label;
    svg.appendChild(t);
  });
  located.forEach(n => {
    const [x, y] = pos[n.id];
    const c = document.createElementNS(NS, 'circle');
    c.setAttribute('cx', x); c.setAttribute('cy', y);
    c.setAttribute('r', n.pin.precision === 'exact' ? 7 : 11);
    c.setAttribute('fill', n.pin.precision === 'exact' ? '#9c5a2e' : '#d8b36a');
    c.setAttribute('stroke', '#5d3517'); c.setAttribute('class', 'pin-dot');
    c.addEventListener('click', () => { loadArticle(n.id); });
    svg.appendChild(c);
    if (n.pin.precision === 'approximate') {
      const ring = document.createElementNS(NS, 'circle');
      ring.setAttribute('cx', x); ring.setAttribute('cy', y); ring.setAttribute('r', 16);
      ring.setAttribute('fill', 'none'); ring.setAttribute('stroke', '#b8862c');
      ring.setAttribute('stroke-dasharray', '3 3');
      svg.appendChild(ring);
    }
    const t = document.createElementNS(NS, 'text');
    t.setAttribute('x', x + 10); t.setAttribute('y', y + 4);
    t.setAttribute('font-size', 12); t.setAttribute('fill', '#3d3226');
    t.textContent = `${n.name}${n.pin.precision === 'approximate' ? '（约）' : ''}`;
    svg.appendChild(t);
  });
  $('#unlocated').innerHTML =
    '<li><b>未定位（不生成图钉）</b></li>' +
    unlocated.map(n => `<li>📍 ${n.name}
      <div class="src">${TYPE_NAME[n.etype]} · ${n.era || ''} · ${n.location_precision}</div></li>`).join('')
    + `<li class="src">${data.note}</li>`;
}

/* ------------------------------------------------------------- travel */
async function loadItineraries() {
  const list = await api('/api/itineraries');   // reviewed only
  $('#itinSelect').innerHTML = list.map(i =>
    `<option value="${i.id}">${i.name}（窗口 ${i.valid_from} ~ ${i.valid_to}）</option>`).join('')
    || '<option value="">（暂无已审行程）</option>';
  $('#travelDate').value = new Date().toISOString().slice(0, 10);
}
$('#runCheck').addEventListener('click', async () => {
  const id = $('#itinSelect').value;
  if (!id) return toast('没有可选的已审行程', true);
  const box = $('#checkResult');
  box.innerHTML = '<p>按服务端发布记录检查中…</p>';
  try {
    const res = await api('/api/travel-check', {
      method: 'POST',
      body: {itinerary_id: id, on_date: $('#travelDate').value},
    });
    const segs = res.segments.map(s => `
      <div class="seg ${s.state}">
        <div class="num">${s.seq}</div>
        <div><b>${s.label}</b>
          ${s.state === 'available' ? ' — <span style="color:var(--green)">信息可支持停靠</span>' : ''}
          ${s.state === 'gap' ? ` — <span style="color:var(--red)">资料缺口：${s.assessment?.reason || s.reason}</span>` : ''}
          <div class="src">${s.assessment?.detail || s.detail || ''}</div>
          ${s.sources?.length ? `<div class="src">依据：${s.sources.map(x => x.title).join('；')}</div>` : ''}
          ${s.as_of ? `<div class="src">发布时间：${s.as_of}${s.assessment?.windows ? '｜窗口：' + s.assessment.windows.map(w => w.valid_from + '~' + w.valid_to).join('，') : ''}</div>` : ''}
        </div>
      </div>`).join('');
    const gaps = res.gaps.length ? `
      <div class="gapbox"><b>补给信息缺口（${res.gaps.length} 处，需人工核实）：</b>
      <ul>${res.gaps.map(g => `<li>${g.label}：${g.detail || g.reason}</li>`).join('')}</ul></div>`
      : '<p class="banner">各节点均有开放窗口内的已发布信息，但仍不构成通行保证。</p>';
    box.innerHTML = `<h3>${res.itinerary.name}｜检查日期 ${res.checked_on}
        ｜窗口 ${res.itinerary.window[0]} ~ ${res.itinerary.window[1]}</h3>
      ${segs}${gaps}
      <p class="disclaimer">${res.disclaimer}</p>`;
  } catch (e) {
    box.innerHTML = `<div class="gapbox"><b>无法检查：</b>${e.message}</div>`;
  }
});

/* ------------------------------------------------------------- admin */
async function loadAdmin() {
  entitiesCache = await api('/api/admin/entities');
  sourcesCache = await api('/api/admin/sources');
  fillSelects();
  await refreshWorkbench();
}

function entityOptions(selected) {
  return entitiesCache.map(e =>
    `<option value="${e.id}" ${e.id === selected ? 'selected' : ''}>${e.name}（${TYPE_NAME[e.etype]}·${e.era || '—'}）</option>`).join('');
}
function sourceOptions() {
  return sourcesCache.map(s => `<option value="${s.id}">[${CAT_NAME[s.kind === 'historical' ? 'evidence' : s.kind === 'oral' ? 'speculation' : 'tip']}] ${s.title}</option>`).join('');
}
function statementOptions() {
  return Promise.all(entitiesCache.map(e =>
    api('/api/admin/statements?entity_id=' + e.id).then(ss => ss)))
    .then(groups => groups.flat())
    .then(ss => ss.map(s => {
      const ent = entitiesCache.find(e => e.id === s.entity_id);
      return `<option value="${s.id}">${ent.name} / ${s.predicate} [${s.confidence}]</option>`;
    }).join(''));
}
async function fillSelects() {
  ['#st-entity', '#rel-from', '#rel-to', '#loc-entity', '#sp-entity', '#ph-entity'].forEach(sel => {
    $(sel).innerHTML = entityOptions();
  });
  $('#st-source').innerHTML = sourceOptions();
  const so = await statementOptions();
  $('#rel-stmt').innerHTML = so;
  $('#loc-stmt').innerHTML = so;
}

function bindForm(id, fn) {
  const form = $(id);
  form.addEventListener('submit', async ev => {
    ev.preventDefault();
    const body = Object.fromEntries(new FormData(form).entries());
    if (form.querySelector('[name=reviewed]')) body.reviewed = form.reviewed.checked;
    ['lat', 'lng', 'seq'].forEach(k => { if (body[k] !== undefined) body[k] = parseFloat(body[k]); });
    try { await fn(body); toast('已保存（工作稿，未发布）'); form.reset(); await loadAdmin(); }
    catch (e) { toast(e.message, true); }
  });
}
bindForm('#f-source', b => api('/api/admin/sources', {method: 'POST', body: b}));
bindForm('#f-entity', b => api('/api/admin/entities', {method: 'POST',
  body: {type: b.etype, name: b.name, era: b.era, summary: b.summary}}));
bindForm('#f-statement', b => api('/api/admin/statements', {method: 'POST',
  body: {...b, value_ref: b.value_ref || null}}));
bindForm('#f-relation', b => api('/api/admin/relations', {method: 'POST', body: b}));
bindForm('#f-location', async b => {
  await api('/api/admin/locations', {method: 'POST', body: b});
});
bindForm('#f-supply', async b => {
  await api('/api/admin/supply', {method: 'POST',
    body: {entity_id: b.entity_id, status: b.status, services: [], note: b.note}});
  await api('/api/admin/supply-window', {method: 'POST',
    body: {entity_id: b.entity_id, valid_from: b.valid_from, valid_to: b.valid_to}});
});
bindForm('#f-itin', b => api('/api/admin/itineraries', {method: 'POST', body: b}));
bindForm('#f-photo', b => api('/api/admin/photos', {method: 'POST', body: b}));

$('#btn-refresh').onclick = refreshWorkbench;
$('#btn-publish-all').onclick = async () => {
  await api('/api/admin/publish', {method: 'POST', body: {all: true, reason: '批量重发'}});
  toast('已为所有实体排队'); await refreshWorkbench();
};
$('#btn-run-publish').onclick = async () => {
  const r = await api('/api/admin/run-publish', {method: 'POST', body: {}});
  toast(`已发布 ${r.published.length} 篇，打印卡同步重建`); await refreshWorkbench(); loadBrowse(currentFilter);
};
$('#btn-package').onclick = () => makePackage(false);
$('#btn-package-low').onclick = () => makePackage(true);
async function makePackage(lb) {
  const p = await api('/api/admin/package', {method: 'POST', body: {ttl_days: 30, low_bandwidth: lb}});
  toast(`离线包 ${p.id} 已生成，${p.expires_at} 过期（${p.document_count} 篇）`);
  await refreshWorkbench();
}

async function refreshWorkbench() {
  const [conflicts, pending, rels, tasks, pubs] = await Promise.all([
    api('/api/admin/conflicts?status=open'),
    api('/api/admin/statements?confidence=proposed'),
    api('/api/admin/relations'),
    api('/api/admin/tasks'),
    api('/api/admin/publications'),
  ]);
  $('#conflicts').innerHTML = '<h4>来源冲突（不会自动取舍）</h4>' + (conflicts.map(c => `
    <div class="workitem">冲突 ${c.id.slice(0, 6)}：${c.reason}
      <div class="actions">
        <button onclick="resolveC('${c.id}','resolved_a')">采陈述A</button>
        <button onclick="resolveC('${c.id}','resolved_b')">采陈述B</button>
        <button onclick="resolveC('${c.id}','reconciled')">两存/调和</button>
      </div></div>`).join('') || '<p class="hint">无未决冲突。</p>');
  window.resolveC = async (id, res) => {
    try { await api('/api/admin/conflict/' + id, {method: 'POST', body: {resolution: res}});
      toast('冲突已处理，相关发布任务可重排'); await refreshWorkbench();
    } catch (e) { toast(e.message, true); }
  };

  $('#pendingStatements').innerHTML = pending.map(s => {
    const ent = entitiesCache.find(e => e.id === s.entity_id);
    const src = sourcesCache.find(x => x.id === s.source_id);
    return `<div class="workitem"><span class="tag proposed">待批</span>
      <b>${ent?.name}</b> · ${s.predicate}: ${s.value || ''}
      <div class="src">[${CAT_NAME[s.category]}] 来源：${src?.title}｜版本留痕于 statement_version</div>
      <div class="actions">
        <button onclick="stmtAct('${s.id}','approve')">批准</button>
        <button onclick="stmtRevise('${s.id}')">考证修订（新建版本）</button>
        <button onclick="stmtAct('${s.id}','withdraw')">撤回</button>
      </div></div>`;
  }).join('') || '<p class="hint">无待批陈述。</p>';

  window.stmtAct = async (id, act) => {
    try { await api('/api/admin/statement/' + id + '/' + act, {method: 'POST', body: {}});
      toast(act === 'withdraw' ? '已撤回；其关系与精确坐标同步失效，需重发下线' : '已更新');
      await refreshWorkbench();
    } catch (e) { toast(e.message, true); }
  };
  window.stmtRevise = async (id) => {
    const note = prompt('修订说明（新陈述进入待批，旧陈述在新陈述批准时自动撤回）：', '井址考证修订');
    if (note === null) return;
    try { await api('/api/admin/statement/' + id + '/revise', {method: 'POST', body: {note}});
      toast('已创建修订陈述（待批）'); await refreshWorkbench();
    } catch (e) { toast(e.message, true); }
  };

  $('#allRelations').innerHTML = rels.map(r => {
    const a = entitiesCache.find(e => e.id === r.from_entity);
    const b = entitiesCache.find(e => e.id === r.to_entity);
    const state = !r.active ? '<span class="tag withdrawn">已失效</span>'
      : r.approved ? '<span class="tag approved">已批准/上线</span>'
      : '<span class="tag proposed">待批（不上线）</span>';
    return `<div class="workitem">${state} ${a?.name} —${REL_NAME[r.rtype]}→ ${b?.name}
      <div class="src">同名不构成关联；批准前须先批准其支撑陈述。</div>
      <div class="actions">${(!r.approved && r.active) ? `<button onclick="appRel('${r.id}')">批准关系</button>` : ''}
        ${r.active ? `<button onclick="deactRel('${r.id}')">停用</button>` : ''}</div></div>`;
  }).join('') || '<p class="hint">暂无关系。</p>';
  window.appRel = async id => {
    try { await api('/api/admin/relation/' + id + '/approve', {method: 'POST', body: {}});
      toast('关系已批准'); await refreshWorkbench();
    } catch (e) { toast(e.message, true); }
  };
  window.deactRel = async id => { await api('/api/admin/relation/' + id + '/deactivate', {method: 'POST', body: {}});
    toast('关系已停用'); await refreshWorkbench(); };

  $('#taskList').innerHTML = tasks.slice().reverse().slice(0, 10).map(t =>
    `<div class="workitem"><span class="tag ${t.status}">${t.status}</span>
      ${t.reason} <div class="src">建 ${t.created_at}${t.finished_at ? '｜完成 ' + t.finished_at : ''}</div>
      <div class="actions">${t.status === 'pending'
        ? `<button onclick="cancelTask('${t.id}')">取消（撤下提示不再上线）</button>` : ''}</div></div>`
  ).join('') || '<p class="hint">暂无任务。</p>';
  window.cancelTask = async id => {
    await api('/api/admin/cancel-task', {method: 'POST', body: {task_id: id}});
    toast('任务已取消，不会再执行'); await refreshWorkbench();
  };

  $('#pubInfo').innerHTML =
    pubs.map(p => `<div class="workitem">${p.entity_name}：rev ${p.revision} · ${p.published_at}</div>`).join('')
    + '<p class="src">前台“当前状态”始终读取此发布记录，而非工作稿。</p>';
}

/* boot */
loadBrowse('all');

"""编辑台与只读视图的大块 HTML 构造。"""
from .render import esc, KIND_CN, CERTAINTY_CN, TIP_STATE_CN
from . import services as sv

def browse_page(conn, lowbw):
    ent, svc = sv.list_targets(conn)
    groups = {"well": [], "caravan": [], "dwelling": [], "craft": []}
    for e in ent:
        e["pub"] = conn.execute("SELECT 1 FROM published_docs WHERE doc_key=?",
                                (f"entity:{e['id']}",)).fetchone() is not None
        groups[e["kind"]].append(e)
    h = ['<h1>按关系浏览</h1><p class="src">水井、商队、民居、工艺是<b>历史地理实体</b>；'
         '补给点是<b>当前服务实体</b>。两类各自独立，名称相同不构成同一关系。</p>']
    for k, cn in (("well", "水井"), ("caravan", "商队"), ("dwelling", "民居"), ("craft", "工艺")):
        h.append(f'<div class="card"><h3>{cn}</h3><ul>')
        for e in groups[k]:
            badge = "" if e["pub"] else '<span class="tag">未发布</span>'
            h.append(f'<li><a href="/doc/entity:{e["id"]}">{esc(e["name"])}</a> {badge}</li>')
        h.append("</ul></div>")
    h.append('<div class="card"><h3>当前补给点（服务实体）</h3><ul>')
    for s in svc:
        pub = conn.execute("SELECT 1 FROM published_docs WHERE doc_key=?",
                           (f"service:{s['id']}",)).fetchone() is not None
        badge = "" if pub else '<span class="tag">未发布</span>'
        h.append(f'<li><a href="/doc/service:{s["id"]}">{esc(s["name"])}</a> '
                 f'{badge} <span class="src">{s["id"]}</span></li>')
    h.append("</ul></div>")
    return "".join(h)

def _statement_rows(conn, sts):
    out = []
    for st in sts:
        src = sv.source_ref(conn, st["source_id"])
        out.append(f"""<tr><td>{st['id']}<br><span class="src">v{st['version']}</span></td>
<td><span class="tag t-{st['kind']}">{st['kind']}</span>{st['value_type']}</td>
<td>{esc(st['claim'])}<div class="src">{esc(st['value'] or '')}</div></td>
<td>{esc(src['ref'] if src else '—')}</td><td>{st['status']}</td>
<td>{esc(st['valid_from'] or '—')}<br>{esc(st['valid_to'] or '—')}</td>
<td>
<form method="post" action="/e/statement/{st['id']}/revise" style="display:inline">
<input name="claim" value="{esc(st['claim'])}" size="22"><button>修订新版</button></form>
<form method="post" action="/e/statement/{st['id']}/withdraw" style="display:inline">
<button>撤回</button></form></td></tr>""")
    return "".join(out)

def editor_home(conn, day):
    ent, svc = sv.list_targets(conn)
    targets = [(t, i, n) for t, i, n in
               [("entity", e["id"], e["name"]) for e in ent]
               + [("service", s["id"], s["name"]) for s in svc]]
    srcs = conn.execute("SELECT * FROM sources ORDER BY id").fetchall()
    src_opts = "".join(f'<option value="{s["id"]}">{esc(s["ref"])} — {esc(s["title"])}</option>'
                       for s in srcs)
    target_opts = "".join(f'<option value="{t}:{i}">[{t}] {esc(n)} ({i})</option>'
                          for t, i, n in targets)
    pending = conn.execute(
        "SELECT COUNT(*) c FROM publish_tasks WHERE status='pending'").fetchone()["c"]
    dirty = conn.execute(
        "SELECT (SELECT COUNT(*) FROM entities WHERE dirty=1)+"
        "(SELECT COUNT(*) FROM service_points WHERE dirty=1) c").fetchone()["c"]
    cands = conn.execute("SELECT COUNT(*) c FROM relations WHERE status='candidate'").fetchone()["c"]
    h = [f"""<h1>编辑台</h1>
<p class="src">评估基准日 {day} ｜ 待处理发布任务 {pending} ｜ 脏条目 {dirty} ｜ 待审关系 {cands}</p>
<div class="card"><h3>保存陈述（历史证据 / 口述推测 / 当代提示）</h3>
<form method="post" action="/e/statement">
目标 <select name="target">{target_opts}</select><br>
类型 <select name="kind">
<option value="evidence">历史证据 evidence</option>
<option value="oral">口述推测 oral</option>
<option value="tip">当代提示 tip</option>
<option value="photo">照片 photo</option></select>
内容性质 <select name="value_type">
<option value="fact">事实 fact</option><option value="coordinate">坐标 coordinate</option>
<option value="tip">提示 tip</option><option value="photo">照片 photo</option></select><br>
陈述 <input name="claim" size="60" required><br>
归一化值（坐标填 lat,lng；提示可填 JSON） <input name="value" size="40"><br>
来源 <select name="source_id">{src_opts}</select><br>
有效窗口 <input type="date" name="valid_from"> 至 <input type="date" name="valid_to">
备注 <input name="note" size="24"><br>
<button>保存陈述</button></form></div>
<p><form method="post" action="/e/infer"><button>运行带证据关系推断</button></form>
<form method="post" action="/e/publish"><button>执行后台发布（重建受影响文章与打印卡）</button></form></p>"""]

    h.append('<h2>关系台</h2>')
    rels = conn.execute("SELECT * FROM relations ORDER BY id").fetchall()
    h.append('<table><tr><th>ID</th><th>连接</th><th>类型</th><th>来源方式</th><th>状态</th>'
             '<th>依据/备注</th><th>人工决定</th></tr>')
    for r in rels:
        an = sv.target_name(conn, r["a_table"], r["a_id"])
        bn = sv.target_name(conn, r["b_table"], r["b_id"])
        decide = ""
        if r["status"] == "candidate":
            decide = (f'<form method="post" action="/e/relation/{r["id"]}/decide" style="display:inline">'
                      f'<button name="decision" value="approved">批准</button> '
                      f'<button name="decision" value="rejected">驳回</button></form>')
        h.append(f"""<tr><td>{r['id']}</td>
<td>[{r['a_table']}]{esc(an)}<br>↔ [{r['b_table']}]{esc(bn)}</td>
<td>{r['rtype']}</td><td>{r['provenance']}</td><td>{r['status']}</td>
<td>{esc(r['note'] or '')}<div class="src">{esc(r['evidence_statement_ids'] or '')}
审：{esc(r['reviewer'] or '—')}</div></td><td>{decide}</td></tr>""")
    h.append("</table>")

    h.append(f"""<div class="card"><h3>登记完全人工确认的关系</h3>
<form method="post" action="/e/relation">
A <select name="a">{target_opts}</select> 类型
<input name="rtype" value="uses" size="10">
B <select name="b">{target_opts}</select>
审校 <input name="reviewer" value="馆长" size="8">
<button>确认连接</button><p class="src">人工确认即 approved；推断候选必须在此表逐条批准后公开。</p>
</form></div>""")

    h.append('<h2>陈述与版本</h2><table><tr><th>ID/版本</th><th>类型</th><th>陈述</th>'
             '<th>出处</th><th>状态</th><th>窗口</th><th>操作</th></tr>')
    h.append(_statement_rows(conn, conn.execute(
        "SELECT * FROM statements ORDER BY id DESC").fetchall()))
    h.append("</table>")

    h.append('<h2>发布记录</h2><table><tr><th>键</th><th>标题</th><th>哈希</th><th>发布日</th></tr>')
    for d in conn.execute("SELECT * FROM published_docs ORDER BY doc_key"):
        h.append(f"<tr><td>{esc(d['doc_key'])}</td><td>{esc(d['title'])}</td>"
                 f"<td>{esc(d['source_hash'])}</td><td>{esc(d['published_at'])}</td></tr>")
    h.append("</table>")
    return "".join(h)

def itinerary_form(conn, result=None, error=None):
    its = conn.execute("SELECT * FROM itineraries").fetchall()
    h = ['<h1>旅行资料检查</h1>',
         '<p class="src">只使用用户选定的<b>已审行程</b>与<b>有效窗口</b>；'
         '补给信息不足时报缺口；系统不以算法自动保证可通行。</p>']
    if error:
        h.append(f'<div class="banner banner-stale">{esc(error)}</div>')
    h.append('<form method="post" action="/itinerary/check">')
    h.append('已审行程 <select name="iid">')
    for it in its:
        h.append(f'<option value="{it["id"]}">{esc(it["name"])}'
                 f'（{it["valid_from"]}~{it["valid_to"]}；{it["status"]}）</option>')
    h.append('</select> 出行日 <input type="date" name="travel_date" value="2026-10-07">')
    h.append(' <button>检查</button></form>')
    if result:
        color = "#dcefdc" if result["ok"] else "#f3d4d0"
        h.append(f'<div class="card" style="background:{color}"><b>'
                 f'{"通过：每个停靠点均有发布记录支持（仍非通行保证）" if result["ok"] else "未通过"}'
                 f'</b><div class="src">评估于 {esc(result["checked_at"])}，依据服务端发布记录</div></div>')
        for s in result["stops"]:
            b = {"ok": "dcefdc", "gap": "f8e3b0", "blocked": "f3d4d0"}[s["verdict"]]
            h.append(f'<div class="card" style="background:#{b}"><b>第{s["stop"]}站 '
                     f'{esc(s["name"])}（{s["id"]}）— {s["verdict"]}</b><p>{esc(s["detail"])}</p>')
            if s["tips"]:
                h.append('<ul>')
                for t in s["tips"]:
                    h.append(f'<li>{esc(t["claim"])} <span class="src">出处 {esc(t["source_ref"])}'
                             f'（{t["kind"]}）</span></li>')
                h.append('</ul>')
            else:
                h.append('<p class="src">无窗口内有效提示。</p>')
            h.append('</div>')
        h.append(f'<p class="src">{esc(result["disclaimer"])}</p>')
    return "".join(h)

def bundles_page(conn, day, msg=None):
    docs = conn.execute("SELECT doc_key,title,published_at FROM published_docs ORDER BY doc_key").fetchall()
    boxes = "".join(f'<label><input type="checkbox" name="keys" value="{d["doc_key"]}"> '
                    f'{esc(d["doc_key"])} {esc(d["title"])}</label><br>' for d in docs)
    h = ['<h1>离线包</h1>',
         f'<p class="src">基准日 {day}：离线包含有效期，过期后内容须显式标“过期”，时间与来源保留。</p>']
    if msg:
        h.append(f'<div class="banner">{msg}</div>')
    h.append(f'<form method="post" action="/bundles/build" class="card"><h3>构建离线包</h3>'
             f'{boxes}<label>有效期天数 <input name="days" value="30" size="4"></label>'
             f'<button>构建</button></form>')
    h.append('<table><tr><th>ID</th><th>含</th><th>构建日</th><th>有效期至</th><th>打开</th></tr>')
    import json as _json
    for b in conn.execute("SELECT * FROM bundles ORDER BY id"):
        keys = _json.loads(b["doc_keys"])
        expired = day > b["valid_to"]
        h.append(f'<tr><td>{b["id"]}</td><td>{esc("、".join(keys))}</td>'
                 f'<td>{b["built_at"]}</td><td>{b["valid_to"]}'
                 f'{" <b>[已过期]</b>" if expired else ""}</td>'
                 f'<td><a href="/bundles/{b["id"]}">当前打开</a> '
                 f'<a href="/bundles/{b["id"]}?day=2027-01-01">模拟2027打开</a></td></tr>')
    h.append("</table>")
    return "".join(h)

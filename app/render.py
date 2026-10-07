"""渲染层：发布快照、打印卡、低带宽变体、地图。

- 发布时固化的 html 内嵌一份 JSON 数据岛（#art-data），
  服务端低带宽模式从【已发布快照的数据岛】重渲染——不回读当前库。
- 历史坐标只渲染“约/待考”虚圈；当前服务点不确定坐标也只是虚圈，
  任何模式都不会把不确定坐标补成精确图钉。
"""
import html
import json

CSS = """
body{font-family:system-ui,'PingFang SC','Microsoft YaHei',sans-serif;margin:0;background:#f6f1e7;color:#2b2620}
header{background:#5a3d2b;color:#f6f1e7;padding:10px 16px}
header a{color:#f6e6c8;margin-right:14px;text-decoration:none}
main{max-width:960px;margin:0 auto;padding:16px}
.card{background:#fff;border:1px solid #d8c9ad;border-radius:8px;padding:12px 14px;margin:12px 0}
.src{color:#7a6a52;font-size:12px}
.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:10px;margin-right:4px}
.t-evidence{background:#e3edda}.t-oral{background:#f6e0c2}.t-tip{background:#dceaf2}
.t-photo{background:#efe2f0}
.st-expired{background:#f3d4d0}.st-active{background:#dcefdc}.st-upcoming{background:#ece7d6}
.banner{background:#f8e3b0;border:1px solid #d8a93a;padding:8px 12px;border-radius:6px;margin:10px 0}
.banner-stale{background:#f3d4d0;border-color:#c06a54}
table{border-collapse:collapse;width:100%;background:#fff}
td,th{border:1px solid #d8c9ad;padding:6px 8px;text-align:left;font-size:14px}
button{background:#5a3d2b;color:#fff;border:0;border-radius:5px;padding:6px 12px;cursor:pointer}
input,select,textarea{padding:5px 8px;border:1px solid #b9a98c;border-radius:4px;margin:3px 0}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.thumb{background:#2b2620;color:#eee;height:120px;display:flex;align-items:center;justify-content:center;border-radius:6px}
.photo-blocked{background:#ddd;color:#666;height:120px;display:flex;align-items:center;justify-content:center;border-radius:6px;font-size:13px;text-align:center;padding:8px}
.lite{background:#fff}.lite .thumb,.lite .photo-art{display:none}
@media print{header,.no-print{display:none}.card{border:1px solid #000}}
"""
KIND_CN = {"well": "水井", "caravan": "商队", "dwelling": "民居", "craft": "工艺",
           "supply": "补给点"}
CERTAINTY_CN = {"approx": "约（历史推算）", "oral-uncertain": "待考（口述指认）",
                "uncertain": "位置未确定", "precise": "实测定位"}
TIP_STATE_CN = {"active": "窗口内", "expired": "已过期", "upcoming": "未到生效日"}

def esc(x):
    return html.escape("" if x is None else str(x))

def src_line(item):
    ref = item.get("source_ref")
    if not ref:
        return '<div class="src">出处：未登记来源</div>'
    return f'<div class="src">出处：{esc(ref)} ｜ {esc(item.get("source_title") or "")}</div>'

def _photo_html(p, lite):
    tag = '<span class="tag t-photo">照片</span>'
    if not p["allowed"]:
        lite_note = "低带宽模式同样不传输影像；" if lite else ""
        return (f'<div class="card"><div class="photo-blocked">照片许可已失效（{esc(p["expires"])} 到期）'
                f'，不显示影像，仅保留来源与说明</div>{tag}{esc(p["claim"])}'
                f'<div class="src">{lite_note}出处与许可状态保留</div>'
                f'{src_line(p)}</div>')
    if lite:
        return (f'<div class="card">{tag}{esc(p["claim"])}'
                f'<div class="src">低带宽模式：影像已省略 ｜ 授权至 {esc(p["expires"])}</div>'
                f'{src_line(p)}</div>')
    return (f'<div class="card"><div class="thumb photo-art">【照片占位】{esc(p["claim"])}</div>'
            f'{tag}{esc(p["claim"])}<div class="src">授权至 {esc(p["expires"])}</div>'
            f'{src_line(p)}</div>')

def render_article_html(art, lite=False):
    cls = "lite" if lite else ""
    h = [f'<article class="{cls}">',
         f'<h2>{esc(art["name"])} '
         f'<span class="tag t-evidence">{esc(KIND_CN.get(art["kind"], art["kind"]))}</span></h2>',
         f'<p>{esc(art["summary"])}</p>']

    if art.get("service"):
        sv = art["service"]
        if sv["status_on_day"] == "closed":
            h.append(f'<div class="banner banner-stale">该补给点已于 '
                     f'<b>{esc(sv["closed_effective"])}</b> 关闭，当前不可接受补给。</div>')
        else:
            h.append('<div class="banner">当前状态（据服务端发布记录）：在营；'
                     '此状态不自动保证道路可通行。</div>')
        h.append(f'<div class="src">服务：{esc("、".join(sv["services"]))} '
                 f'｜ 坐标：{"已实测" if sv["coord_certain"] else "未确定（不做精确图钉）"}'
                 f'</div>')

    if art["conflicts"]:
        h.append('<div class="banner banner-stale"><b>来源冲突：</b><ul>')
        for grp in art["conflicts"]:
            for c in grp:
                h.append(f'<li>陈述 #{c["sid"]}（{esc(c["kind"])}）：{esc(c["claim"])}'
                         f' <span class="src">出处 {esc(c["source_ref"])}</span></li>')
        h.append('</ul>冲突未裁定前，坐标按“待考”展示，不取平均值、不钉精确点。</div>')

    if art["coords"]:
        h.append('<h3>定位（均为陈述，附出处）</h3>')
        for c in art["coords"]:
            mark = "待考虚圈" if c["certainty"] in ("oral-uncertain", "uncertain") else (
                "约圈" if c["certainty"] == "approx" else "精确图钉")
            h.append(f'<div class="card">{esc(c["claim"])} '
                     f'<span class="tag">{esc(CERTAINTY_CN.get(c["certainty"], c["certainty"]))}'
                     f' → {mark}</span>'
                     f'<div class="src">坐标 {c["lat"]:.4f},{c["lng"]:.4f}'
                     f'{" ｜ 与其他陈述冲突" if c["conflict"] else ""}</div>'
                     f'{src_line(c)}</div>')

    if art["facts"]:
        h.append('<h3>历史证据与事实</h3>')
        for f in art["facts"]:
            h.append(f'<div class="card"><span class="tag t-evidence">历史证据</span>'
                     f'{esc(f["claim"])}{src_line(f)}</div>')

    if art["tips"]:
        h.append('<h3>当代提示（带时间窗）</h3>')
        for t in art["tips"]:
            h.append(f'<div class="card"><span class="tag t-tip">当代提示</span>'
                     f'<span class="tag st-{t["state"]}">{esc(TIP_STATE_CN[t["state"]])}</span>'
                     f'{esc(t["claim"])}'
                     f'<div class="src">有效窗口：{esc(t["valid_from"] or "—")} 至 '
                     f'{esc(t["valid_to"] or "—")}（陈述 #{t["sid"]}）</div>{src_line(t)}</div>')

    for p in art["photos"]:
        h.append(_photo_html(p, lite))

    if art["neighbors"]:
        h.append('<h3>已批准关系</h3><ul>')
        for n in art["neighbors"]:
            prov = "人工确认" if n["provenance"] == "manual" else "推断后人工批准"
            link = (f'<a href="/doc/{n["other_table"]}:{n["other_id"]}">'
                    f'{esc(n["other_name"])}</a>' if n["other_published"]
                    else f'{esc(n["other_name"])}（未发布）')
            arrow = "→" if n["dir"] == "out" else "←"
            h.append(f'<li>{esc(n["rtype_label"])} {arrow} {link} '
                     f'<span class="src">[{prov}]</span></li>')
        h.append('</ul>')

    h.append(f'<div class="src">文章构建日 {esc(art["generated_at"])} ｜ 内容哈希 '
             f'{esc(art["source_hash"])}</div>')
    body = "\n".join(h) + "</article>"
    island = json.dumps(art, ensure_ascii=False)
    return body + f'\n<script type="application/json" id="art-data">{esc(island)}</script>'

def extract_art(full_html):
    marker = '<script type="application/json" id="art-data">'
    i = full_html.find(marker)
    if i < 0:
        return None
    j = full_html.find("</script>", i)
    return json.loads(html.unescape(full_html[i + len(marker):j]))

def render_card_html(art, lite=False):
    sv = art.get("service")
    status_line = ""
    if sv:
        status_line = (f'<p><b>状态：</b>{"已关闭（" + esc(sv["closed_effective"]) + " 起）" if sv["status_on_day"]=="closed" else "在营（发布记录）"}'
                       f' ｜ 坐标：{"实测" if sv["coord_certain"] else "未确定"}</p>')
    refs = sorted({x.get("source_ref") for x in
                   art["facts"] + art["tips"] + art["coords"] + art["photos"]
                   if x.get("source_ref")})
    def tip_line(t):
        state_cn = esc(TIP_STATE_CN[t["state"]])
        vf = esc(t["valid_from"] or "—")
        vt = esc(t["valid_to"] or "—")
        return f'<li>[{state_cn}] {esc(t["claim"])} （{vf}~{vt}）</li>'
    tips = "".join(tip_line(t) for t in art["tips"])
    return f"""<article class="{'lite' if lite else ''}">
<h2>打印卡 ｜ {esc(art['name'])}</h2>
<p class="src">{esc(KIND_CN.get(art['kind'], art['kind']))} ｜ 键 {esc(art['doc_key'])}
｜ 构建日 {esc(art['generated_at'])} ｜ 哈希 {esc(art['source_hash'])}</p>
{status_line}
<p>{esc(art['summary'])}</p>
<h4>时间窗提示</h4><ul>{tips or '<li>无</li>'}</ul>
<h4>来源（任何模式下保留）</h4><p class="src">{esc('；'.join(refs) or '无登记来源')}</p>
<p class="src">低带宽/打印保留必要时间与来源说明。当前状态以服务端发布记录为准。</p>
</article>"""

def publish_renderers(art):
    return render_article_html(art, False), render_card_html(art, False)

# ---------- 外壳 ----------

def shell(title, body, lowbw=False, banner=None):
    bw = ' ｜ <a href="?lowbw=1">低带宽</a>' if "?" not in title else ""
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><style>{'' if lowbw else CSS}</style></head>
<body><header><b>沙漠驿站资料馆</b>
<a href="/">浏览</a><a href="/map">地图</a><a href="/itinerary">行程核验</a>
<a href="/editor">编辑台</a><a href="/bundles">离线包</a>
{'<span>低带宽模式</span>' if lowbw else ''}</header><main>
{banner or ''}{body}</main></body></html>"""

def stale_banner(state, published_at):
    if state == "stale":
        return ('<div class="banner banner-stale">数据已有修订，当前显示的仍是服务端'
                f'上一版发布记录（{esc(published_at)} 构建）。新版本要经后台发布重建后才上线。</div>')
    if state == "unpublished":
        return '<div class="banner">该条目尚未发布，公开页无内容。</div>'
    return ""

# ---------- 地图 ----------

def render_map_svg(points, lite=False):
    geo = [p for p in points if p.get("lat") is not None]
    if not geo:
        return '<div class="banner">暂无带坐标的已发布条目。</div>'
    minlat = min(p["lat"] for p in geo) - 0.01
    maxlat = max(p["lat"] for p in geo) + 0.01
    minlng = min(p["lng"] for p in geo) - 0.01
    maxlng = max(p["lng"] for p in geo) + 0.01
    W, H = 720, 460
    def xy(lat, lng):
        x = (lng - minlng) / (maxlng - minlng or 1) * (W - 80) + 40
        y = H - 40 - (lat - minlat) / (maxlat - minlat or 1) * (H - 80)
        return x, y
    parts = [f'<svg width="{W}" height="{H if not lite else 200}" '
             'style="background:#efe6d2;border:1px solid #b9a98c">']
    for p in geo:
        x, y = xy(p["lat"], p["lng"])
        if p["marker"] == "pin":
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="#b5402f"/>')
        elif p["marker"] == "approx":
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="14" fill="none" '
                         'stroke="#5a3d2b" stroke-width="2" stroke-dasharray="5 4"/>')
            parts.append(f'<text x="{x:.1f}" y="{y+4:.1f}" font-size="12" '
                         f'text-anchor="middle">约</text>')
        else:  # oral-uncertain / uncertain
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="16" fill="none" '
                         'stroke="#c08a2e" stroke-width="2" stroke-dasharray="3 3"/>')
            parts.append(f'<text x="{x:.1f}" y="{y+4:.1f}" font-size="12" '
                         f'text-anchor="middle">?</text>')
        parts.append(f'<a href="/doc/{esc(p["key"])}">'
                     f'<text x="{x+10:.1f}" y="{y+4:.1f}" font-size="12">'
                     f'{esc(p["name"])}</text></a>')
    unloc = [p for p in points if p.get("lat") is None]
    parts.append('</svg>')
    if unloc:
        parts.append('<div class="banner">以下条目坐标未确定，地图上不补精确图钉：'
                     + "、".join(esc(p["name"]) for p in unloc) + "</div>")
    parts.append('<p class="src">红钉＝当前服务点实测坐标；棕虚圈“约”＝历史推算；'
                 '黄虚圈“?”＝口述指认/位置未确定。地图只呈现已发布条目。</p>')
    return "".join(parts)

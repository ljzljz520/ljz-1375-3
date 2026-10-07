"""HTTP 层：只读公开站 + 编辑台接口。

公开页的当前状态一律取自 published_docs / published_cards（服务端实际发布记录）；
低带宽模式从已发布快照的数据岛重渲染，不回读编辑库。
"""
import json
import re
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler
from . import services as sv
from . import render as rd
from . import views

def _qs(qs, key, default=None):
    v = parse_qs(qs).get(key)
    return v[0] if v else default

class App:
    def __init__(self, conn, default_day="2026-10-07"):
        self.conn = conn
        self.default_day = default_day

    def day(self, qs):
        return _qs(qs, "day", self.default_day)

    # ---------- 公开文档 ----------
    def doc_page(self, key, qs, card=False):
        lite = _qs(qs, "lowbw") == "1"
        day = self.day(qs)
        table, tid = key.split(":", 1)
        doc = self.conn.execute(
            "SELECT * FROM published_cards WHERE card_key=?", (f"card:{key}",)).fetchone() if card \
            else self.conn.execute("SELECT * FROM published_docs WHERE doc_key=?", (key,)).fetchone()
        if not doc:
            body = '<div class="banner">该条目尚无服务端发布记录。</div>'
            return rd.shell("未发布", body, lite), 404
        state, art, _ = sv.publish_state(self.conn, table, tid, day)
        # 关键：当前网页内容来自发布快照（数据岛），不回读编辑库；
        # 仅按查询日对快照内已有的关闭日/时间窗/许可到期日重算“当前状态”。
        snap = rd.extract_art(doc["html"]) or art
        snap = sv.rebase_snapshot(snap, day)
        banner = rd.stale_banner(state, doc["published_at"])
        if card:
            body = rd.render_card_html(snap, lite)
            title = f"打印卡 {snap['name']}"
        else:
            body = rd.render_article_html(snap, lite)
            title = snap["name"]
        banner += (f'<p class="no-print"><a href="/card/{key}">打印卡</a> '
                   f'<a href="/doc/{key}?lowbw=1">低带宽版</a> '
                   f'<span class="src">发布哈希 {doc["source_hash"]}；'
                   f'发布日 {doc["published_at"]}</span></p>')
        return rd.shell(title, banner + body, lite), 200

    def map_page(self, qs):
        lite = _qs(qs, "lowbw") == "1"
        points = sv.public_map_points(self.conn)
        body = "<h1>关系地图</h1>" + rd.render_map_svg(points, lite)
        return rd.shell("地图", body, lite), 200

    # ---------- 编辑台 ----------
    def editor_page(self, qs):
        return rd.shell("编辑台", views.editor_home(self.conn, self.day(qs))), 200

    def save_statement(self, form):
        table, tid = form["target"].split(":", 1)
        sid = sv.save_statement(
            self.conn, table, tid, form["kind"], form["value_type"],
            form["claim"], form.get("value") or None, int(form["source_id"]),
            valid_from=form.get("valid_from") or None,
            valid_to=form.get("valid_to") or None,
            note=form.get("note") or None, now=self.day(""))
        return f"/editor?msg=saved-{sid}"

    def revise(self, sid, form):
        sv.revise_statement(self.conn, sid, form["claim"],
                            value=form.get("value") or None, now=self.day(""))
        return "/editor?msg=revised"

    def publish_all(self, form):
        sv.enqueue_dirty(self.conn, self.day(""))
        rebuilt = sv.run_publish_tasks(self.conn, self.day(""), rd.publish_renderers)
        return "/editor?msg=published:" + ",".join(f"{t}:{i}" for t, i in rebuilt)

    # ---------- 行程 ----------
    def itinerary_page(self, qs):
        return rd.shell("行程核验", views.itinerary_form(self.conn)), 200

    def itinerary_check(self, form):
        day = form.get("day") or self.day("")
        res = sv.check_itinerary(self.conn, form["iid"], form["travel_date"], today=day)
        if not res.get("ok") and "error" in res:
            body = views.itinerary_form(self.conn, error=res["error"])
        else:
            body = views.itinerary_form(self.conn, result=res)
        return rd.shell("行程核验", body), 200

    # ---------- 离线包 ----------
    def bundles_page(self, qs):
        msg = _qs(qs, "msg")
        return rd.shell("离线包", views.bundles_page(self.conn, self.day(qs), msg)), 200

    def bundle_build(self, form):
        keys = form.getlist("keys")
        days = int(form.get("days", "30"))
        bid, valid_to = sv.build_bundle(self.conn, keys, self.day(""), days)
        return f"/bundles?msg=built-{bid}-until-{valid_to}"

    def bundle_open(self, bid, qs):
        day = self.day(qs)
        data = sv.open_bundle(self.conn, bid, day)
        if not data:
            return rd.shell("离线包", '<div class="banner">离线包不存在。</div>'), 404
        lite = _qs(qs, "lowbw") == "1"
        banner = (f'<div class="banner banner-stale"><b>离线包已过期</b>（有效期至 '
                  f'{data["valid_to"]}，当前打开日 {data["opened_at"]}）。'
                  f'以下为过期快照：时间窗与来源说明保留，不能当作当前可通行依据。</div>'
                  if data["expired"] else
                  f'<div class="banner">离线包有效期至 {data["valid_to"]}（打开日 {data["opened_at"]}）。</div>')
        parts = [f'<h1>离线包（{len(data["docs"])} 篇）</h1>', banner]
        for d in data["docs"]:
            snap = rd.extract_art(d["html"])
            if snap:
                snap = sv.rebase_snapshot(snap, day)
                parts.append(rd.render_article_html(snap, lite))
            else:
                parts.append(esc_note(d))
        return rd.shell("离线包", "".join(parts), lite), 200

def esc_note(d):
    return f'<div class="card">{rd.esc(d["title"])}（快照无可渲染数据）</div>'

ROUTES_GET = [
    (r"^/$", "home"),
    (r"^/map$", "map"),
    (r"^/itinerary$", "itinerary"),
    (r"^/editor$", "editor"),
    (r"^/bundles$", "bundles"),
    (r"^/doc/(.+)$", "doc"),
    (r"^/card/(.+)$", "card"),
    (r"^/bundles/(\d+)$", "bundle"),
]

class Handler(BaseHTTPRequestHandler):
    app = None
    def log_message(self, *a):
        pass

    def _send(self, content, status=200, ctype="text/html; charset=utf-8", redirect=False):
        if redirect:
            self.send_response(303)
            self.send_header("Location", content)
            self.end_headers()
            return
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        app = self.app
        try:
            for pat, name in ROUTES_GET:
                m = re.match(pat, u.path)
                if not m:
                    continue
                if name == "home":
                    return self._send(*rd_tuple(app, u.query))
                if name == "map":
                    return self._send(*app.map_page(u.query))
                if name == "itinerary":
                    return self._send(*app.itinerary_page(u.query))
                if name == "editor":
                    return self._send(*app.editor_page(u.query))
                if name == "bundles":
                    return self._send(*app.bundles_page(u.query))
                if name == "doc":
                    return self._send(*app.doc_page(m.group(1), u.query))
                if name == "card":
                    return self._send(*app.doc_page(m.group(1), u.query, card=True))
                if name == "bundle":
                    return self._send(*app.bundle_open(int(m.group(1)), u.query))
            self._send("not found", 404, "text/plain; charset=utf-8")
        except Exception as e:  # noqa
            self._send(f"500 {e}", 500, "text/plain; charset=utf-8")

    def do_POST(self):
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
        class F:
            def get(self, k, d=None):
                vals = form.get(k)
                return vals[0] if vals else d
            def __getitem__(self, k):
                return form[k][0]
            def getlist(self, k):
                return form.get(k, [])
        f = F()
        app = self.app
        try:
            if u.path == "/e/statement":
                return self._send(app.save_statement(f), redirect=True)
            if u.path == "/e/infer":
                sv.run_inference(app.conn, app.day(""))
                return self._send("/editor?msg=inferred", redirect=True)
            if u.path == "/e/publish":
                return self._send(app.publish_all(f), redirect=True)
            m = re.match(r"^/e/statement/(\d+)/(revise|withdraw)$", u.path)
            if m:
                sid = int(m.group(1))
                if m.group(2) == "revise":
                    app.revise(sid, f)
                else:
                    sv.withdraw_statement(app.conn, sid, app.day(""))
                return self._send("/editor?msg=ok", redirect=True)
            m = re.match(r"^/e/relation/(\d+)/decide$", u.path)
            if m:
                sv.decide_relation(app.conn, int(m.group(1)), f["decision"],
                                   f.get("reviewer", "馆长"), app.day(""))
                return self._send("/editor?msg=decided", redirect=True)
            if u.path == "/e/relation":
                at, ai = f["a"].split(":", 1)
                bt, bi = f["b"].split(":", 1)
                sv.add_manual_relation(app.conn, at, ai, bt, bi, f["rtype"],
                                       f.get("reviewer", "馆长"), now=app.day(""))
                return self._send("/editor?msg=manual", redirect=True)
            if u.path == "/itinerary/check":
                return self._send(*app.itinerary_check(f))
            if u.path == "/bundles/build":
                return self._send(app.bundle_build(f), redirect=True)
            self._send("not found", 404, "text/plain; charset=utf-8")
        except Exception as e:  # noqa
            self._send(f"500 {e}", 500, "text/plain; charset=utf-8")

def rd_tuple(app, qs):
    return rd.shell("沙漠驿站资料馆", views.browse_page(app.conn, _qs(qs, "lowbw") == "1"),
                    _qs(qs, "lowbw") == "1"), 200

def make_server(conn, host="127.0.0.1", port=8080, day="2026-10-07"):
    from http.server import HTTPServer
    Handler.app = App(conn, day)
    httpd = HTTPServer((host, port), Handler)
    return httpd

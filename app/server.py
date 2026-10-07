"""HTTP layer (Python stdlib only).

Two surfaces are deliberately separated:

* ``/api/*``      – public website.  Reads ONLY the publication table and
                    approved relations; current state is server-side state.
* ``/api/admin/*`` – editorial console.  Reads/writes working evidence and
                     enqueues publish tasks.
"""
from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import core
from .db import connect, init_db

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

RELATION_LABELS = {
    "uses_well": "使用水井", "lodges_at": "驻留", "draws_from": "取材于",
    "practiced_at": "从业于", "serves": "当代供给",
}


# ----------------------------------------------------------------- helpers
def _json_body(handler):
    length = int(handler.headers.get("Content-Length") or 0)
    if not length:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise core.DomainError("bad_json", f"请求体不是合法 JSON: {exc}", 400)


def _need(body, *keys):
    out = []
    for k in keys:
        if k not in body or body[k] in (None, ""):
            raise core.DomainError("missing_field", f"缺少字段: {k}", 422)
        out.append(body[k])
    return out


# ----------------------------------------------------------------- public
def public_browse(conn, qs):
    etype = (qs.get("type") or [None])[0]
    out = {"well": [], "caravan": [], "dwelling": [], "craft": []}
    for ent in core.list_entities(conn, etype):
        pub = core.latest_publication(conn, ent["id"])
        if not pub:
            continue  # website shows only published material
        doc = json.loads(pub["doc"])
        lowbw = qs.get("lowbw", ["0"])[0] == "1"
        out[ent["etype"]].append({
            "id": ent["id"], "name": ent["name"], "era": ent["era"],
            "summary": ent["summary"], "revision": pub["revision"],
            "published_at": pub["published_at"],
            "supply": doc.get("supply"),
            "location": None if lowbw else doc.get("location"),
            "photo_count": 0 if lowbw else len(doc.get("photos", [])),
        })
    if etype:
        return out[etype]
    return out


def _low_bw(doc):
    """Low-bandwidth view of an ALREADY PUBLISHED document: drop photos and
    coordinate numbers, but keep dates, windows and source attribution."""
    import copy
    d = copy.deepcopy(doc)
    d["low_bandwidth"] = True
    d["photos"] = []
    if d.get("location") and d["location"].get("precision") == "exact":
        d["location"] = {"precision": "exact", "note": "低带宽模式省略坐标（定位证据仍在）"}
    elif d.get("location") and d["location"].get("approximate"):
        d["location"] = {"precision": "approximate", "note": "低带宽模式省略坐标"}
    return d


def public_article(conn, qs):
    eid = qs.get("entity_id", [None])[0]
    if not eid:
        raise core.DomainError("missing_field", "缺少 entity_id", 422)
    pub = core.latest_publication(conn, eid)
    if not pub:
        raise core.DomainError("not_published", "该实体尚未发布，前台不展示工作稿", 404)
    doc = json.loads(pub["doc"])
    if qs.get("lowbw", ["0"])[0] == "1":
        doc = _low_bw(doc)
    return {"doc": doc, "revision": pub["revision"],
            "published_at": pub["published_at"],
            "cards": json.loads(pub["cards"]),
            "relation_labels": RELATION_LABELS}


def public_graph(conn, qs):
    """Approved connections only; unlocated nodes are listed, never pinned."""
    nodes, edges = {}, []
    published = {ent["id"] for ent in core.list_entities(conn)
                 if core.latest_publication(conn, ent["id"])}
    for r in core.list_relations(conn, approved_only=True):
        if r["from_entity"] not in published or r["to_entity"] not in published:
            continue
        edges.append({"from": r["from_entity"], "to": r["to_entity"],
                      "rtype": r["rtype"], "label": RELATION_LABELS.get(r["rtype"], r["rtype"]),
                      "statement_id": r["statement_id"]})
    for eid in published:
        ent = core.get_entity(conn, eid)
        loc_row = None
        for l in core.active_locations(conn):
            if l["entity_id"] == eid:
                loc_row = l
                break
        pin = None
        if loc_row and loc_row["precision"] in ("exact", "approximate"):
            pin = {"lat": loc_row["lat"], "lng": loc_row["lng"],
                   "precision": loc_row["precision"]}
        # unlocated (or location whose pin can't be confirmed) → pin stays None
        nodes[eid] = {"id": eid, "name": ent["name"], "etype": ent["etype"],
                      "era": ent["era"], "pin": pin,
                      "located": bool(pin),
                      "location_precision": loc_row["precision"] if loc_row else "unlocated"}
    return {"nodes": list(nodes.values()), "edges": edges,
            "note": "仅显示已批准连接；未证实坐标以未定位列出，不生成精确图钉。"}


def public_itineraries(conn, qs):
    return [{"id": i["id"], "name": i["name"], "reviewed": bool(i["reviewed"]),
             "valid_from": i["valid_from"], "valid_to": i["valid_to"]}
            for i in core.list_itineraries(conn) if i["reviewed"]]


def public_card(conn, qs):
    eid = qs.get("entity_id", [None])[0]
    if not eid:
        raise core.DomainError("missing_field", "缺少 entity_id", 422)
    pub = core.latest_publication(conn, eid)
    if not pub:
        raise core.DomainError("not_published", "尚未发布，无法打印", 404)
    cards = json.loads(pub["cards"])
    return {"card": cards.get("default", {}), "published_at": pub["published_at"]}


# ----------------------------------------------------------------- admin
def admin_dispatch(conn, method, parts, query, body):
    """parts: path segments after /api/admin/"""
    resource = parts[0] if parts else ""

    # ---- sources
    if resource == "sources":
        if method == "GET":
            return core.list_sources(conn, query.get("kind", [None])[0])
        if method == "POST":
            kind, title = _need(body, "kind", "title")
            return core.create_source(conn, kind, title, body.get("citation", ""),
                                      body.get("authored_at"), body.get("notes", ""))

    # ---- entities
    if resource == "entities":
        if method == "GET":
            return core.list_entities(conn, query.get("type", [None])[0])
        if method == "POST":
            etype, name = _need(body, "type", "name")
            return core.create_entity(conn, etype, name, body.get("era", ""),
                                      body.get("summary", ""))

    if resource == "entity":
        return core.get_entity(conn, parts[1])

    if resource == "name-search":
        return core.find_entities_by_name(conn, query.get("name", [""])[0])

    # ---- statements
    if resource == "statements":
        if method == "GET":
            return core.list_statements(
                conn, query.get("entity_id", [None])[0],
                query.get("confidence", [None])[0],
                include_versions=query.get("versions", ["0"])[0] == "1")
        if method == "POST":
            entity_id, source_id, predicate = _need(body, "entity_id", "source_id", "predicate")
            return core.create_statement(
                conn, entity_id, source_id, predicate, body.get("value", ""),
                body.get("value_ref"), body.get("category"), body.get("note", ""),
                body.get("created_by", "editor"))

    if resource == "statement":
        sid = parts[1]
        action = parts[2] if len(parts) > 2 else None
        if method == "GET" and not action:
            return core.list_statements(conn, sid) if False else core.get_statement(conn, sid)
        if action == "approve":
            return core.set_statement_confidence(conn, sid, "approved")
        if action == "withdraw":
            return core.set_statement_confidence(conn, sid, "withdrawn")
        if action == "propose":
            return core.set_statement_confidence(conn, sid, "proposed")
        if action == "edit":
            return core.edit_statement(
                conn, sid, value=body.get("value"), note=body.get("note"),
                source_id=body.get("source_id"), value_ref=body.get("value_ref"))
        if action == "revise":
            return core.revise_statement(
                conn, sid, value=body.get("value"), value_ref=body.get("value_ref"),
                note=body.get("note", ""), source_id=body.get("source_id"))

    # ---- conflicts
    if resource == "conflicts":
        return core.list_conflicts(conn, query.get("status", [None])[0])
    if resource == "conflict":
        resolution, = _need(body, "resolution")
        return core.resolve_conflict(conn, parts[1], resolution, body.get("note", ""))

    # ---- relations
    if resource == "relations":
        if method == "GET":
            return core.list_relations(conn, query.get("approved_only", ["0"])[0] == "1")
        if method == "POST":
            f, t, rt, st = _need(body, "from_entity", "to_entity", "rtype", "statement_id")
            return core.create_relation(conn, f, t, rt, st)
    if resource == "relation" and parts[2] == "approve":
        return core.approve_relation(conn, parts[1])
    if resource == "relation" and parts[2] == "deactivate":
        return core.deactivate_relation(conn, parts[1])

    # ---- locations
    if resource == "locations":
        if method == "GET":
            return core.active_locations(conn)
        if method == "POST":
            eid, sid, precision = _need(body, "entity_id", "statement_id", "precision")
            return core.add_location(conn, eid, sid, precision,
                                     body.get("lat"), body.get("lng"))

    # ---- photos
    if resource == "photos":
        if method == "POST":
            eid, lic = _need(body, "entity_id", "license")
            return core.add_photo(conn, eid, body.get("caption", ""), lic,
                                  body.get("license_until"))
    if resource == "photo":
        if method == "GET":
            return core.get_photo(conn, parts[1])
    if resource == "refresh-photos":
        return {"lapsed": core.refresh_photo_status(conn)}

    # ---- supply
    if resource == "supply":
        if method == "POST":
            eid, status = _need(body, "entity_id", "status")
            return core.upsert_supply_point(conn, eid, status,
                                            body.get("services", []), body.get("note", ""))
        if method == "GET":
            eid = query.get("entity_id", [None])[0]
            if not eid:
                raise core.DomainError("missing_field", "缺少 entity_id", 422)
            sp = core.get_supply_for_entity(conn, eid)
            if sp is None:
                raise core.DomainError("not_found", "无补给记录", 404)
            return sp
    if resource == "supply-window":
        eid, vf, vt = _need(body, "entity_id", "valid_from", "valid_to")
        core.add_service_window(conn, eid, vf, vt, body.get("season_note", ""))
        return {"ok": True}
    if resource == "supply-state":
        eid = query.get("entity_id", [None])[0]
        if not eid:
            raise core.DomainError("missing_field", "缺少 entity_id", 422)
        return core.supply_state_at(conn, eid, query.get("date", [None])[0])

    # ---- itineraries
    if resource == "itineraries":
        if method == "GET":
            return core.list_itineraries(conn)
        if method == "POST":
            name, vf, vt = _need(body, "name", "valid_from", "valid_to")
            return core.create_itinerary(conn, name, vf, vt, body.get("note", ""),
                                         bool(body.get("reviewed")))
    if resource == "itinerary":
        iid = parts[1]
        if method == "GET" and len(parts) == 2:
            return core.get_itinerary(conn, iid)
    if resource == "segment":
        iid, seq, label = _need(body, "itinerary_id", "seq", "label")
        return core.add_segment(conn, iid, int(seq), label, body.get("entity_id"),
                                body.get("kind", "waypoint"), body.get("note", ""))
    if resource == "review-itinerary":
        iid, = _need(body, "itinerary_id")
        return core.set_itinerary_reviewed(conn, iid, bool(body.get("reviewed", True)))

    # ---- travel check (may be used from console too)
    if resource == "travel-check" and method == "POST":
        iid, = _need(body, "itinerary_id")
        return core.travel_check(conn, iid, body.get("on_date"))

    # ---- publishing
    if resource == "publish":
        if method == "POST":
            ids = body.get("entity_ids") or []
            if body.get("all"):
                ids = [e["id"] for e in core.list_entities(conn)]
            reason = body.get("reason", "编辑更新")
            return {"tasks": core.enqueue_publish(conn, ids, reason)}
    if resource == "run-publish" and method == "POST":
        return {"published": core.run_pending_tasks(conn)}
    if resource == "cancel-task" and method == "POST":
        tid, = _need(body, "task_id")
        return core.cancel_task(conn, tid)
    if resource == "tasks":
        return core.list_tasks(conn, query.get("status", [None])[0])
    if resource == "publications":
        out = []
        for e in core.list_entities(conn):
            pub = core.latest_publication(conn, e["id"])
            if pub:
                out.append({"entity_id": e["id"], "entity_name": e["name"],
                            "revision": pub["revision"], "published_at": pub["published_at"]})
        return out

    # ---- offline packages
    if resource == "package":
        if method == "POST":
            return core.build_package(conn, int(body.get("ttl_days", 30)),
                                      bool(body.get("low_bandwidth")))
        if method == "GET":
            return core.get_package(conn, parts[1], query.get("date", [None])[0])

    if resource == "today":
        if method == "POST" and body.get("date"):
            core.TODAY_OVERRIDE = body["date"]
        return {"today": core.today_iso(), "override": core.TODAY_OVERRIDE}

    raise core.DomainError("no_route", f"未知编辑接口: {'/'.join(parts)}", 404)


ROUTES_PUBLIC = {
    "browse": public_browse,
    "article": public_article,
    "graph": public_graph,
    "itineraries": public_itineraries,
    "card": public_card,
}


def make_handler(db_path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet by default
            pass

        def _send(self, payload, status=200):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_file(self, path: Path):
            ctype = {"index.html": "text/html; charset=utf-8",
                     "app.js": "application/javascript; charset=utf-8",
                     "styles.css": "text/css; charset=utf-8"}.get(path.name,
                                                                  "application/octet-stream")
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def _dispatch(self, method):
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            conn = connect(db_path)
            try:
                if path in ("/", "/index.html"):
                    return self._send_file(STATIC_DIR / "index.html")
                if path in ("/app.js", "/styles.css"):
                    return self._send_file(STATIC_DIR / path.lstrip("/"))

                m = re.fullmatch(r"/api/([a-z\-]+)(?:/(.*))?", path)
                if m and m.group(1) == "travel-check":
                    body = _json_body(self) if method == "POST" else {}
                    iid = body.get("itinerary_id") or query.get("itinerary_id", [None])[0]
                    if not iid:
                        raise core.DomainError("missing_field", "缺少 itinerary_id", 422)
                    return self._send(core.travel_check(
                        conn, iid, body.get("on_date") or query.get("date", [None])[0]))
                if m and m.group(1) == "package":
                    pid = m.group(2)
                    if not pid:
                        raise core.DomainError("missing_field", "缺少 package id", 422)
                    return self._send(core.get_package(conn, pid,
                                                       query.get("date", [None])[0]))
                if m and m.group(1) in ROUTES_PUBLIC:
                    return self._send(ROUTES_PUBLIC[m.group(1)](conn, query))

                if path.startswith("/api/admin/"):
                    parts = [p for p in path[len("/api/admin/"):].split("/") if p]
                    body = _json_body(self) if method == "POST" else {}
                    return self._send(admin_dispatch(conn, method, parts, query, body))

                raise core.DomainError("no_route", f"未知路径: {path}", 404)
            except core.DomainError as e:
                self._send({"error": e.code, "message": e.message}, e.http_status)
            except Exception as e:  # pragma: no cover - defensive
                self._send({"error": "internal", "message": str(e)}, 500)
            finally:
                conn.close()

    return Handler


def serve(db_path="data/archive.db", host="127.0.0.1", port=8000):
    conn = connect(db_path)
    init_db(conn)
    conn.close()
    httpd = ThreadingHTTPServer((host, port), make_handler(db_path))
    print(f"沙漠驿站资料馆 http://{host}:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    db_path = sys.argv[2] if len(sys.argv) > 2 else "data/archive.db"
    serve(db_path, port=port)

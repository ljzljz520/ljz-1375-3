"""Domain logic for the desert station archive.

All invariants live here, not in the HTTP layer:

* A name match between a historical well and a modern supply point never
  creates a link.  Links are *relations* backed by a statement and must be
  explicitly approved.
* Exact map pins require an approved supporting statement; approximate and
  unlocated places are shown as such.
* Statements are versioned; withdrawals deactivate derived relations and
  locations; withdrawn contemporary tips disappear on next publish.
* Public pages are rendered solely from the ``publication`` table, which is
  rebuilt by discrete publish tasks.  Cancelled tasks never run, so a tip
  withdrawn before publication cannot be resurrected by an old task.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, timedelta

from .db import jdumps

TODAY_OVERRIDE: str | None = None  # tests can freeze "today"


class DomainError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def today() -> date:
    return date.fromisoformat(TODAY_OVERRIDE) if TODAY_OVERRIDE else date.today()


def today_iso() -> str:
    return today().isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _row(conn, sql, args=()):
    return conn.execute(sql, args).fetchone()


def _rows(conn, sql, args=()):
    return conn.execute(sql, args).fetchall()


# =============================================================== sources
def create_source(conn, kind, title, citation="", authored_at=None, notes=""):
    if kind not in ("historical", "oral", "contemporary"):
        raise DomainError("bad_kind", "来源类型必须是 historical/oral/contemporary", 422)
    sid = _new_id()
    conn.execute(
        "INSERT INTO source (id,kind,title,citation,authored_at,created_at,notes)"
        " VALUES (?,?,?,?,?,?,?)",
        (sid, kind, title, citation, authored_at, today_iso(), notes),
    )
    conn.commit()
    return get_source(conn, sid)


def get_source(conn, sid):
    row = _row(conn, "SELECT * FROM source WHERE id=?", (sid,))
    if not row:
        raise DomainError("not_found", "来源不存在", 404)
    return dict(row)


def list_sources(conn, kind=None):
    sql = "SELECT * FROM source"
    args = ()
    if kind:
        sql += " WHERE kind=?"
        args = (kind,)
    return [dict(r) for r in _rows(conn, sql + " ORDER BY created_at, title", args)]


# =============================================================== entities
def create_entity(conn, etype, name, era="", summary=""):
    if etype not in ("well", "caravan", "dwelling", "craft"):
        raise DomainError("bad_type", "实体类型非法", 422)
    eid = _new_id()
    conn.execute(
        "INSERT INTO entity (id,etype,name,era,summary,created_at) VALUES (?,?,?,?,?,?)",
        (eid, etype, name, era, summary, today_iso()),
    )
    conn.commit()
    return get_entity(conn, eid)


def get_entity(conn, eid):
    row = _row(conn, "SELECT * FROM entity WHERE id=?", (eid,))
    if not row:
        raise DomainError("not_found", "实体不存在", 404)
    return dict(row)


def list_entities(conn, etype=None):
    sql = "SELECT * FROM entity"
    args = ()
    if etype:
        sql += " WHERE etype=?"
        args = (etype,)
    return [dict(r) for r in _rows(conn, sql + " ORDER BY name", args)]


def find_entities_by_name(conn, name):
    """Name lookup is *search only*.  Callers must never turn a hit into a
    relation or supply assumption."""
    return [dict(r) for r in _rows(
        conn, "SELECT * FROM entity WHERE name=? ORDER BY created_at", (name,))]


# =============================================================== statements
def _bump_version(conn, st):
    n = _row(conn,
             "SELECT COALESCE(MAX(version),0) n FROM statement_version WHERE statement_id=?",
             (st["id"],))["n"]
    conn.execute(
        "INSERT INTO statement_version (id,statement_id,version,source_id,predicate,"
        "value,value_ref,confidence,category,note,changed_at,changed_by)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (_new_id(), st["id"], n + 1, st["source_id"], st["predicate"], st["value"],
         st["value_ref"], st["confidence"], st["category"], st["note"],
         today_iso(), st.get("created_by", "editor")),
    )
    return n + 1


def _scan_conflicts(conn, stmt):
    """Open a conflict for same-entity/same-predicate active claims from
    different sources whose values disagree.  Never auto-resolves."""
    others = _rows(
        conn,
        "SELECT s.* FROM statement s WHERE s.entity_id=? AND s.predicate=? "
        "AND s.confidence!='withdrawn' AND s.id!=? AND s.source_id!=?",
        (stmt["entity_id"], stmt["predicate"], stmt["id"], stmt["source_id"]),
    )
    created = []
    for o in others:
        o = dict(o)
        same = (o["value"] == stmt["value"] and o["value_ref"] == stmt["value_ref"])
        if same:
            continue
        exists = _row(
            conn,
            "SELECT 1 FROM conflict WHERE entity_id=? AND status='open' AND "
            "((statement_a=? AND statement_b=?) OR (statement_a=? AND statement_b=?))",
            (stmt["entity_id"], o["id"], stmt["id"], stmt["id"], o["id"]),
        )
        if exists:
            continue
        cid = _new_id()
        conn.execute(
            "INSERT INTO conflict (id,entity_id,statement_a,statement_b,reason,created_at)"
            " VALUES (?,?,?,?,?,?)",
            (cid, stmt["entity_id"], o["id"], stmt["id"],
             "不同来源对同一属性给出不一致表述，待人工考辨", today_iso()),
        )
        created.append(cid)
    return created


def create_statement(conn, entity_id, source_id, predicate, value="",
                     value_ref=None, category=None, note="", created_by="editor"):
    get_entity(conn, entity_id)
    get_source(conn, source_id)
    if value_ref:
        get_entity(conn, value_ref)
    if category is None:
        category = {"historical": "evidence", "oral": "speculation",
                    "contemporary": "tip"}[get_source(conn, source_id)["kind"]]
    if category not in ("evidence", "speculation", "tip"):
        raise DomainError("bad_category", "陈述类别非法", 422)
    sid = _new_id()
    conn.execute(
        "INSERT INTO statement (id,entity_id,source_id,predicate,value,value_ref,"
        "confidence,category,note,created_at,created_by) VALUES (?,?,?,?,?,?,'proposed',?,?,?,?)",
        (sid, entity_id, source_id, predicate, value, value_ref,
         category, note, today_iso(), created_by),
    )
    conn.commit()
    st = get_statement(conn, sid)
    _bump_version(conn, st)
    _scan_conflicts(conn, st)
    conn.commit()
    return st


def get_statement(conn, sid):
    row = _row(conn, "SELECT * FROM statement WHERE id=?", (sid,))
    if not row:
        raise DomainError("not_found", "陈述不存在", 404)
    return dict(row)


def list_statements(conn, entity_id=None, confidence=None, include_versions=False):
    sql = "SELECT * FROM statement WHERE 1=1"
    args = []
    if entity_id:
        sql += " AND entity_id=?"
        args.append(entity_id)
    if confidence:
        sql += " AND confidence=?"
        args.append(confidence)
    out = [dict(r) for r in _rows(conn, sql + " ORDER BY created_at", args)]
    if include_versions:
        for st in out:
            st["versions"] = [dict(v) for v in _rows(
                conn, "SELECT * FROM statement_version WHERE statement_id=? ORDER BY version",
                (st["id"],))]
    return out


def edit_statement(conn, sid, *, value=None, note=None, source_id=None,
                   value_ref=None, changed_by="editor"):
    """An edit creates a new version and returns the claim to 'proposed'
    unless it was withdrawn (withdrawn claims stay withdrawn)."""
    st = get_statement(conn, sid)
    if st["confidence"] == "withdrawn":
        raise DomainError("withdrawn", "已撤回陈述不可直接编辑，请新建修订陈述", 409)
    if value is not None:
        st["value"] = value
    if note is not None:
        st["note"] = note
    if source_id is not None:
        get_source(conn, source_id)
        st["source_id"] = source_id
    if value_ref is not None:
        get_entity(conn, value_ref)
        st["value_ref"] = value_ref or None
    st["confidence"] = "proposed"   # changed claim needs re-approval
    conn.execute(
        "UPDATE statement SET value=?,note=?,source_id=?,value_ref=?,confidence='proposed'"
        " WHERE id=?",
        (st["value"], st["note"], st["source_id"], st["value_ref"], sid),
    )
    conn.commit()
    st = get_statement(conn, sid)
    _bump_version(conn, st)
    _scan_conflicts(conn, st)
    # relations backed by an edited statement lose approval until re-confirmed
    conn.execute("UPDATE relation SET approved=0 WHERE statement_id=?", (sid,))
    conn.commit()
    return st


def set_statement_confidence(conn, sid, confidence, by="editor"):
    if confidence not in ("proposed", "approved", "withdrawn"):
        raise DomainError("bad_confidence", "审批状态非法", 422)
    st = get_statement(conn, sid)
    conn.execute("UPDATE statement SET confidence=? WHERE id=?", (confidence, sid))
    conn.commit()
    st = get_statement(conn, sid)
    _bump_version(conn, {**st, "created_by": by})
    if confidence == "approved":
        # approving a revision withdraws the claim it supersedes
        if st["supersedes"]:
            old = get_statement(conn, st["supersedes"])
            if old["confidence"] != "withdrawn":
                conn.execute("UPDATE statement SET confidence='withdrawn' WHERE id=?",
                             (old["id"],))
                _bump_version(conn, {**old, "confidence": "withdrawn", "created_by": by})
                _deactivate_derived(conn, old["id"])
    if confidence == "withdrawn":
        _deactivate_derived(conn, sid)
    _scan_conflicts(conn, st)
    conn.commit()
    return get_statement(conn, sid)


def _deactivate_derived(conn, sid):
    """Withdrawn evidence must not keep links alive or precise pins standing."""
    conn.execute("UPDATE relation SET active=0, approved=0 WHERE statement_id=?", (sid,))
    conn.execute("UPDATE location SET status='superseded' WHERE statement_id=?", (sid,))


def revise_statement(conn, sid, *, value=None, value_ref=None, note="", source_id=None):
    """井址考证修订: create a new proposed revision pointing at the old claim.
    The old claim stays until the revision is approved, at which point it is
    withdrawn automatically."""
    old = get_statement(conn, sid)
    if old["confidence"] == "withdrawn":
        raise DomainError("withdrawn", "不应基于已撤回陈述修订", 409)
    new = create_statement(
        conn, old["entity_id"], source_id or old["source_id"], old["predicate"],
        value=value if value is not None else old["value"],
        value_ref=value_ref if value_ref is not None else old["value_ref"],
        category=old["category"], note=note,
    )
    conn.execute("UPDATE statement SET supersedes=? WHERE id=?", (sid, new["id"]))
    conn.commit()
    return get_statement(conn, new["id"])


def resolve_conflict(conn, cid, resolution, note=""):
    if resolution not in ("resolved_a", "resolved_b", "reconciled"):
        raise DomainError("bad_resolution", "冲突处理方式非法", 422)
    row = _row(conn, "SELECT * FROM conflict WHERE id=?", (cid,))
    if not row:
        raise DomainError("not_found", "冲突不存在", 404)
    conn.execute("UPDATE conflict SET status=?, reason=?, resolved_at=? WHERE id=?",
                 (resolution, (note or dict(row)["reason"]), today_iso(), cid))
    if resolution in ("resolved_a", "resolved_b"):
        loser = dict(row)["statement_b"] if resolution == "resolved_a" else dict(row)["statement_a"]
        st = get_statement(conn, loser)
        # Editorial ruling rejects the losing claim whatever its prior state.
        if st["confidence"] != "withdrawn":
            conn.execute("UPDATE statement SET confidence='withdrawn' WHERE id=?", (loser,))
            _bump_version(conn, {**st, "confidence": "withdrawn"})
            _deactivate_derived(conn, loser)
    conn.commit()
    return dict(_row(conn, "SELECT * FROM conflict WHERE id=?", (cid,)))


def list_conflicts(conn, status=None):
    sql = "SELECT * FROM conflict"
    args = ()
    if status:
        sql += " WHERE status=?"
        args = (status,)
    return [dict(r) for r in _rows(conn, sql + " ORDER BY created_at", args)]


# =============================================================== relations
RELATION_TYPES = {"uses_well", "lodges_at", "draws_from", "practiced_at", "serves"}


def create_relation(conn, from_entity, to_entity, rtype, statement_id):
    if rtype not in RELATION_TYPES:
        raise DomainError("bad_rtype", "关系类型非法", 422)
    get_entity(conn, from_entity)
    get_entity(conn, to_entity)
    st = get_statement(conn, statement_id)
    if st["entity_id"] != from_entity or st["value_ref"] != to_entity:
        raise DomainError("statement_mismatch",
                          "支撑陈述必须是起始实体指向目标实体的陈述", 422)
    rid = _new_id()
    conn.execute(
        "INSERT INTO relation (id,from_entity,to_entity,rtype,statement_id,approved,active,created_at)"
        " VALUES (?,?,?,?,?,0,1,?)",
        (rid, from_entity, to_entity, rtype, statement_id, today_iso()),
    )
    conn.commit()
    return get_relation(conn, rid)


def get_relation(conn, rid):
    row = _row(conn, "SELECT * FROM relation WHERE id=?", (rid,))
    if not row:
        raise DomainError("not_found", "关系不存在", 404)
    return dict(row)


def approve_relation(conn, rid):
    rel = get_relation(conn, rid)
    st = get_statement(conn, rel["statement_id"])
    if not rel["active"]:
        raise DomainError("inactive", "关系已失效（支撑陈述撤回或修订），不能批准", 409)
    if st["confidence"] != "approved":
        raise DomainError("statement_not_approved",
                          "必须先批准支撑陈述，才能批准关系；同名不等于关联", 409)
    conn.execute("UPDATE relation SET approved=1 WHERE id=?", (rid,))
    conn.commit()
    return get_relation(conn, rid)


def deactivate_relation(conn, rid):
    get_relation(conn, rid)
    conn.execute("UPDATE relation SET active=0 WHERE id=?", (rid,))
    conn.commit()
    return get_relation(conn, rid)


def list_relations(conn, approved_only=False):
    sql = "SELECT * FROM relation"
    if approved_only:
        sql += " WHERE approved=1 AND active=1"
    return [dict(r) for r in _rows(conn, sql + " ORDER BY created_at")]


# =============================================================== locations
def add_location(conn, entity_id, statement_id, precision, lat=None, lng=None):
    """Exact coordinates are 'precise pins' and demand approved evidence."""
    get_entity(conn, entity_id)
    st = get_statement(conn, statement_id)
    if st["entity_id"] != entity_id:
        raise DomainError("statement_mismatch", "坐标陈述必须属于该实体", 422)
    if precision not in ("exact", "approximate", "unlocated"):
        raise DomainError("bad_precision", "坐标精度非法", 422)
    if precision == "exact":
        if st["confidence"] != "approved":
            raise DomainError("unverified_pin",
                              "支撑陈述尚未批准：未确定坐标不能补成精确图钉", 409)
        if lat is None or lng is None:
            raise DomainError("bad_coords", "精确坐标需要经纬度", 422)
    if precision == "unlocated":
        lat = lng = None
    lid = _new_id()
    conn.execute("UPDATE location SET status='superseded' WHERE entity_id=? AND status='active'",
                 (entity_id,))
    conn.execute(
        "INSERT INTO location (id,entity_id,statement_id,lat,lng,precision,status,created_at)"
        " VALUES (?,?,?,?,?,?,'active',?)",
        (lid, entity_id, statement_id, lat, lng, precision, today_iso()),
    )
    conn.commit()
    return dict(_row(conn, "SELECT * FROM location WHERE id=?", (lid,)))


def active_locations(conn):
    return [dict(r) for r in _rows(conn,
        "SELECT l.* FROM location l WHERE l.status='active' ORDER BY l.entity_id")]


# =============================================================== photos
def add_photo(conn, entity_id, caption, license, license_until=None):
    if license not in ("cc-by", "cc-by-sa", "permission", "public-domain", "none"):
        raise DomainError("bad_license", "许可类型非法", 422)
    get_entity(conn, entity_id)
    pid = _new_id()
    status = "active" if license != "none" else "permission_lapsed"
    conn.execute(
        "INSERT INTO photo (id,entity_id,caption,license,license_until,status,created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (pid, entity_id, caption, license, license_until, status, today_iso()),
    )
    conn.commit()
    return get_photo(conn, pid)


def get_photo(conn, pid):
    row = _row(conn, "SELECT * FROM photo WHERE id=?", (pid,))
    if not row:
        raise DomainError("not_found", "照片不存在", 404)
    return dict(row)


def photo_usable(photo, on_date=None):
    on = date.fromisoformat(on_date) if on_date else today()
    if photo["status"] != "active":
        return False
    if photo["license_until"]:
        if date.fromisoformat(photo["license_until"]) < on:
            return False
    return True


def refresh_photo_status(conn, on_date=None):
    """Lapse photos whose permission window has closed."""
    on = date.fromisoformat(on_date) if on_date else today()
    n = 0
    for p in conn.execute("SELECT * FROM photo WHERE status='active' AND license_until IS NOT NULL"):
        if date.fromisoformat(dict(p)["license_until"]) < on:
            conn.execute("UPDATE photo SET status='permission_lapsed' WHERE id=?",
                         (dict(p)["id"],))
            n += 1
    conn.commit()
    return n


# =============================================================== supply
def upsert_supply_point(conn, entity_id, status, services=None, note=""):
    ent = get_entity(conn, entity_id)
    if ent["etype"] != "well":
        # supply facilities are modelled on the (modern) well-named entity;
        # historical wells are separate entities and carry no service record.
        pass
    if status not in ("open", "closed", "temporarily_closed"):
        raise DomainError("bad_status", "补给点状态非法", 422)
    row = _row(conn, "SELECT * FROM supply_point WHERE entity_id=?", (entity_id,))
    if row:
        conn.execute("UPDATE supply_point SET status=?,services=?,note=?,updated_at=? WHERE id=?",
                     (status, jdumps(services or []), note, today_iso(), row["id"]))
    else:
        conn.execute(
            "INSERT INTO supply_point (id,entity_id,status,services,note,updated_at)"
            " VALUES (?,?,?,?,?,?)",
            (_new_id(), entity_id, status, jdumps(services or []), note, today_iso()),
        )
    conn.commit()
    return dict(_row(conn, "SELECT * FROM supply_point WHERE entity_id=?", (entity_id,)))


def add_service_window(conn, supply_entity_id, valid_from, valid_to, season_note=""):
    sp = _row(conn, "SELECT * FROM supply_point WHERE entity_id=?", (supply_entity_id,))
    if not sp:
        raise DomainError("not_found", "请先登记补给点", 404)
    if date.fromisoformat(valid_to) < date.fromisoformat(valid_from):
        raise DomainError("bad_window", "窗口结束早于开始", 422)
    conn.execute(
        "INSERT INTO service_window (id,supply_id,valid_from,valid_to,season_note)"
        " VALUES (?,?,?,?,?)",
        (_new_id(), sp["id"], valid_from, valid_to, season_note),
    )
    conn.commit()


def get_supply_for_entity(conn, entity_id):
    sp = _row(conn, "SELECT * FROM supply_point WHERE entity_id=?", (entity_id,))
    if not sp:
        return None
    sp = dict(sp)
    sp["services"] = json.loads(sp["services"])
    sp["windows"] = [dict(w) for w in _rows(
        conn, "SELECT * FROM service_window WHERE supply_id=? ORDER BY valid_from", (sp["id"],))]
    return sp


def supply_state_at(conn, entity_id, on_date=None):
    """Return availability assessment for a *contemporary service entity*."""
    on = date.fromisoformat(on_date) if on_date else today()
    sp = get_supply_for_entity(conn, entity_id)
    if sp is None:
        return {"available": False, "reason": "no_service_record",
                "detail": "该实体没有当代补给服务记录"}
    if sp["status"] == "closed":
        return {"available": False, "reason": "closed", "detail": sp["note"] or "补给点已关闭"}
    if sp["status"] == "temporarily_closed":
        return {"available": False, "reason": "temporarily_closed",
                "detail": sp["note"] or "临时关闭"}
    covering = [w for w in sp["windows"]
                if date.fromisoformat(w["valid_from"]) <= on <= date.fromisoformat(w["valid_to"])]
    if not covering:
        return {"available": False, "reason": "no_valid_window",
                "detail": f"{on.isoformat()} 不在任何已发布服务窗口内",
                "windows": sp["windows"]}
    return {"available": True, "reason": "open",
            "windows": [dict(w) for w in covering], "services": sp["services"]}


# =============================================================== itineraries
def create_itinerary(conn, name, valid_from, valid_to, note="", reviewed=False):
    if date.fromisoformat(valid_to) < date.fromisoformat(valid_from):
        raise DomainError("bad_window", "行程有效窗口非法", 422)
    iid = _new_id()
    conn.execute(
        "INSERT INTO itinerary (id,name,reviewed,valid_from,valid_to,note,created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (iid, name, 1 if reviewed else 0, valid_from, valid_to, note, today_iso()),
    )
    conn.commit()
    return get_itinerary(conn, iid)


def get_itinerary(conn, iid):
    row = _row(conn, "SELECT * FROM itinerary WHERE id=?", (iid,))
    if not row:
        raise DomainError("not_found", "行程不存在", 404)
    d = dict(row)
    d["segments"] = [dict(s) for s in _rows(
        conn, "SELECT * FROM segment WHERE itinerary_id=? ORDER BY seq", (iid,))]
    return d


def list_itineraries(conn):
    return [get_itinerary(conn, r["id"]) for r in _rows(conn, "SELECT id FROM itinerary")]


def add_segment(conn, itinerary_id, seq, label, entity_id=None, kind="waypoint", note=""):
    get_itinerary(conn, itinerary_id)
    if entity_id:
        get_entity(conn, entity_id)
    conn.execute(
        "INSERT INTO segment (id,itinerary_id,seq,label,entity_id,kind,note)"
        " VALUES (?,?,?,?,?,?,?)",
        (_new_id(), itinerary_id, seq, label, entity_id, kind, note),
    )
    conn.commit()
    return get_itinerary(conn, itinerary_id)


def set_itinerary_reviewed(conn, iid, reviewed):
    get_itinerary(conn, iid)
    conn.execute("UPDATE itinerary SET reviewed=? WHERE id=?", (1 if reviewed else 0, iid))
    conn.commit()
    return get_itinerary(conn, iid)


# ------------------------------------------------------------- travel check
def _approved_link(conn, from_entity, rtype, to_entity):
    return _row(
        conn,
        "SELECT r.* FROM relation r JOIN statement s ON s.id=r.statement_id "
        "WHERE r.from_entity=? AND r.to_entity=? AND r.rtype=? "
        "AND r.approved=1 AND r.active=1 AND s.confidence='approved'",
        (from_entity, to_entity, rtype),
    )


def travel_check(conn, itinerary_id, on_date=None):
    """Check a *user-selected reviewed itinerary* on a date inside its window.

    The function only summarises published information and gaps; it never
    algorithmically guarantees passage.
    """
    on = date.fromisoformat(on_date) if on_date else today()
    try:
        itin = get_itinerary(conn, itinerary_id)
    except DomainError:
        raise
    if not itin["reviewed"]:
        raise DomainError("itinerary_not_reviewed",
                          "只能使用用户选定的已审行程；该行程尚未通过审核", 409)
    if not (date.fromisoformat(itin["valid_from"]) <= on <= date.fromisoformat(itin["valid_to"])):
        raise DomainError("outside_window",
                          f"日期 {on.isoformat()} 不在行程有效窗口 "
                          f"{itin['valid_from']} ~ {itin['valid_to']} 内", 409)

    segments_out = []
    gaps = []
    for seg in itin["segments"]:
        entry = {"seq": seg["seq"], "label": seg["label"], "kind": seg["kind"],
                 "note": seg["note"], "state": "info", "sources": [], "as_of": None}
        if not seg["entity_id"]:
            entry["state"] = "gap"
            entry["reason"] = "no_entity"
            entry["detail"] = "该停靠点未关联实体，资料不足"
            gaps.append(entry)
            segments_out.append(entry)
            continue
        ent = get_entity(conn, seg["entity_id"])
        pub = latest_publication(conn, ent["id"])
        if pub is None:
            entry["state"] = "gap"
            entry["reason"] = "not_published"
            entry["detail"] = "该站点尚无服务端已发布资料，不能据算法推断可通行"
            gaps.append(entry)
            segments_out.append(entry)
            continue
        doc = json.loads(pub["doc"])
        entry["as_of"] = pub["published_at"]

        state = None
        supply = get_supply_for_entity(conn, ent["id"])
        if supply is not None:
            state = supply_state_at(conn, ent["id"], on.isoformat())
            entry["sources"] = doc.get("supply_sources", [])
        elif ent["etype"] == "well":
            # A historical well is NOT a service entity.  Only an explicitly
            # approved 'serves' link to an open modern supply point can make
            # water available, and a shared name is never enough.
            links = _rows(
                conn,
                "SELECT r.* FROM relation r JOIN statement s ON s.id=r.statement_id "
                "WHERE r.to_entity=? AND r.rtype='serves' AND r.approved=1 AND r.active=1 "
                "AND s.confidence='approved'",
                (ent["id"],))
            options = []
            for l in links:
                l = dict(l)
                sv = supply_state_at(conn, l["from_entity"], on.isoformat())
                sv["supply_entity_id"] = l["from_entity"]
                options.append(sv)
            good = [o for o in options if o["available"]]
            if good:
                state = {"available": True, "reason": "via_approved_supply", "via": good}
                st = get_statement(conn, links[0]["statement_id"])
                src = get_source(conn, st["source_id"])
                entry["sources"] = [{"source_id": src["id"], "title": src["title"],
                                     "citation": src["citation"]}]
            else:
                state = {"available": False,
                         "reason": "well_is_not_service_entity",
                         "detail": "历史井址与当代补给点相互独立；未找到已批准的开放补给关联，"
                                   "同名不能作为仍可使用的依据"}
                if options:
                    state["checked"] = options
        else:
            entry["state"] = "info"
            entry["detail"] = "非补给节点"
            segments_out.append(entry)
            continue

        entry["state"] = "available" if state["available"] else "gap"
        entry["assessment"] = state
        if not state["available"]:
            gaps.append(entry)
        segments_out.append(entry)

    return {
        "itinerary": {"id": itin["id"], "name": itin["name"],
                      "window": [itin["valid_from"], itin["valid_to"]]},
        "checked_on": on.isoformat(),
        "segments": segments_out,
        "gaps": [{"seq": g["seq"], "label": g["label"],
                  "reason": g.get("reason") or g.get("assessment", {}).get("reason"),
                  "detail": g.get("detail") or g.get("assessment", {}).get("detail", "")}
                 for g in gaps],
        "guaranteed_passage": False,
        "disclaimer": "系统仅汇总服务端已发布资料与已批准关系，列出缺口，"
                      "不以算法自动保证可通行；出行前须自行核实。",
    }


# =============================================================== rendering
def _statement_payload(conn, st):
    src = get_source(conn, st["source_id"])
    return {
        "statement_id": st["id"], "predicate": st["predicate"],
        "value": st["value"], "value_ref": st["value_ref"],
        "category": st["category"], "confidence": st["confidence"],
        "note": st["note"],
        "source": {"id": src["id"], "kind": src["kind"], "title": src["title"],
                   "citation": src["citation"], "authored_at": src["authored_at"]},
    }


def render_entity_doc(conn, entity_id, low_bandwidth=False):
    """Render the *current approved* view of an entity for publication."""
    ent = get_entity(conn, entity_id)
    sts = [dict(r) for r in _rows(
        conn, "SELECT * FROM statement WHERE entity_id=? AND confidence='approved' ORDER BY created_at",
        (entity_id,))]
    statements = [_statement_payload(conn, s) for s in sts]

    rels = []
    for r in _rows(conn,
                   "SELECT r.* FROM relation r JOIN statement s ON s.id=r.statement_id "
                   "WHERE (r.from_entity=? OR r.to_entity=?) AND r.approved=1 AND r.active=1 "
                   "AND s.confidence='approved'", (entity_id, entity_id)):
        r = dict(r)
        other = r["to_entity"] if r["from_entity"] == entity_id else r["from_entity"]
        oe = get_entity(conn, other)
        rels.append({"rtype": r["rtype"], "other": {"id": other, "name": oe["name"],
                                                     "etype": oe["etype"], "era": oe["era"]},
                     "direction": "out" if r["from_entity"] == entity_id else "in",
                     "statement_id": r["statement_id"]})

    loc = _row(conn, "SELECT * FROM location WHERE entity_id=? AND status='active'", (entity_id,))
    location = None
    if loc:
        loc = dict(loc)
        location = {"precision": loc["precision"]}
        if loc["precision"] == "exact" and not low_bandwidth:
            location.update(lat=loc["lat"], lng=loc["lng"])
        elif loc["precision"] == "approximate" and not low_bandwidth:
            location.update(lat=loc["lat"], lng=loc["lng"], approximate=True)
        else:
            location["note"] = ("精确位置未证实" if loc["precision"] != "exact"
                                else "低带宽模式省略坐标")

    refresh_photo_status(conn)
    photos = []
    if not low_bandwidth:
        for p in _rows(conn, "SELECT * FROM photo WHERE entity_id=?", (entity_id,)):
            p = dict(p)
            if photo_usable(p):
                photos.append({"id": p["id"], "caption": p["caption"],
                               "license": p["license"], "license_until": p["license_until"]})

    supply = None
    supply_sources = []
    sp = get_supply_for_entity(conn, entity_id)
    if sp:
        supply = {"status": sp["status"], "services": sp["services"],
                  "windows": sp["windows"], "note": sp["note"], "updated_at": sp["updated_at"]}
        for w in sp["windows"]:
            supply_sources.append({"window": [w["valid_from"], w["valid_to"]],
                                   "note": w["season_note"]})

    return {
        "entity": ent,
        "statements": statements,
        "relations": rels,
        "location": location,
        "photos": photos,
        "supply": supply,
        "supply_sources": supply_sources,
        "low_bandwidth": bool(low_bandwidth),
    }


def render_print_card(conn, entity_id):
    doc = render_entity_doc(conn, entity_id)
    ent = doc["entity"]
    lines = [f"【{ent['name']}】({ {'well':'水井','caravan':'商队','dwelling':'民居','craft':'工艺'}[ent['etype']] })"]
    if ent["era"]:
        lines.append(f"年代：{ent['era']}")
    for s in doc["statements"]:
        lines.append(f"· [{s['category']}] {s['predicate']}: {s['value']}"
                     f"（来源：{s['source']['title']} {s['source'].get('authored_at') or ''}）")
    for r in doc["relations"]:
        lines.append(f"· 关系 {r['rtype']} {r['direction']} -> {r['other']['name']}")
    if doc["supply"]:
        su = doc["supply"]
        lines.append(f"· 当代补给：{su['status']}；窗口 "
                     + "; ".join(f"{w['valid_from']}~{w['valid_to']}" for w in su["windows"]))
    if doc["location"]:
        lines.append(f"· 定位：{doc['location']['precision']}")
    lines.append(f"打印依据：服务端发布资料；低带宽下仍保留时间与来源说明。")
    return {"title": ent["name"], "text": "\n".join(lines),
            "sources": [{"title": s["source"]["title"], "citation": s["source"]["citation"]}
                        for s in doc["statements"]]}


# =============================================================== publishing
def enqueue_publish(conn, entity_ids, reason):
    ids = []
    for eid in dict.fromkeys(entity_ids):
        get_entity(conn, eid)
        pend = _row(conn, "SELECT id FROM publish_task WHERE entity_id=? AND status='pending'", (eid,))
        if pend:
            ids.append(pend["id"])
            continue
        tid = _new_id()
        conn.execute(
            "INSERT INTO publish_task (id,entity_id,reason,status,created_at) VALUES (?,?,?,'pending',?)",
            (tid, eid, reason, today_iso()),
        )
        ids.append(tid)
    conn.commit()
    return ids


def cancel_task(conn, task_id):
    row = _row(conn, "SELECT * FROM publish_task WHERE id=?", (task_id,))
    if not row:
        raise DomainError("not_found", "发布任务不存在", 404)
    if dict(row)["status"] == "pending":
        conn.execute("UPDATE publish_task SET status='cancelled' WHERE id=?", (task_id,))
        conn.commit()
    return dict(_row(conn, "SELECT * FROM publish_task WHERE id=?", (task_id,)))


def run_pending_tasks(conn):
    """Rebuild affected articles.  Rendering reads the *current* database,
    so a tip withdrawn after enqueue is absent; cancelled tasks never run."""
    done = []
    tasks = _rows(conn, "SELECT * FROM publish_task WHERE status='pending' ORDER BY created_at")
    for t in tasks:
        t = dict(t)
        doc = render_entity_doc(conn, t["entity_id"])
        cards = {"default": render_print_card(conn, t["entity_id"])}
        rev = _row(conn, "SELECT COALESCE(MAX(revision),0) n FROM publication WHERE entity_id=?",
                   (t["entity_id"],))["n"] + 1
        conn.execute(
            "INSERT INTO publication (id,entity_id,revision,doc,cards,published_at,task_id)"
            " VALUES (?,?,?,?,?,?,?)",
            (_new_id(), t["entity_id"], rev, jdumps(doc), jdumps(cards),
             today_iso(), t["id"]),
        )
        conn.execute("UPDATE publish_task SET status='done',finished_at=? WHERE id=?",
                     (today_iso(), t["id"]))
        conn.commit()
        done.append({"task_id": t["id"], "entity_id": t["entity_id"], "revision": rev})
    return done


def latest_publication(conn, entity_id):
    row = _row(conn,
               "SELECT * FROM publication WHERE entity_id=? ORDER BY revision DESC LIMIT 1",
               (entity_id,))
    return dict(row) if row else None


def list_tasks(conn, status=None):
    sql = "SELECT * FROM publish_task"
    args = ()
    if status:
        sql += " WHERE status=?"
        args = (status,)
    return [dict(r) for r in _rows(conn, sql + " ORDER BY created_at", args)]


# =============================================================== offline packs
def build_package(conn, ttl_days=30, low_bandwidth=False):
    refresh_photo_status(conn)
    docs = {}
    for ent in list_entities(conn):
        pub = latest_publication(conn, ent["id"])
        if not pub:
            continue
        doc = render_entity_doc(conn, ent["id"], low_bandwidth=low_bandwidth)
        docs[ent["id"]] = doc
    payload = {"generated_at": today_iso(),
               "low_bandwidth": bool(low_bandwidth),
               "documents": docs}
    expires = today() + timedelta(days=ttl_days)
    pid = _new_id()
    conn.execute(
        "INSERT INTO offline_package (id,built_at,expires_at,payload,low_bandwidth)"
        " VALUES (?,?,?,?,?)",
        (pid, today_iso(), expires.isoformat(), jdumps(payload), 1 if low_bandwidth else 0),
    )
    conn.commit()
    return {"id": pid, "built_at": today_iso(), "expires_at": expires.isoformat(),
            "low_bandwidth": bool(low_bandwidth), "document_count": len(docs)}


def get_package(conn, pid, on_date=None):
    row = _row(conn, "SELECT * FROM offline_package WHERE id=?", (pid,))
    if not row:
        raise DomainError("not_found", "离线包不存在", 404)
    row = dict(row)
    on = date.fromisoformat(on_date) if on_date else today()
    if date.fromisoformat(row["expires_at"]) < on:
        raise DomainError("package_expired",
                          f"离线包已于 {row['expires_at']} 过期，请重新下载", 410)
    return {"id": row["id"], "built_at": row["built_at"],
            "expires_at": row["expires_at"], "low_bandwidth": bool(row["low_bandwidth"]),
            "payload": json.loads(row["payload"])}

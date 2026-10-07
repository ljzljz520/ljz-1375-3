"""领域服务层：所有业务规则集中于此（不直接产生 HTML）。

关键规则：
- 陈述可修订（版本链，旧版 superseded）、可撤回（withdrawn），不物理删除。
- 同名不等于同一实体：历史实体与当前服务实体之间只通过独立 relation 关联。
- 关系 provenance=inferred 的默认 candidate，必须人工 approved 才公开。
- 发布即快照：文章/打印卡内容在发布时固化，网页只展示 published_* 表。
- 重建级联受批准关系；任何旧任务重建都读取当前库，已撤提示不会复活。
"""
import json
import math
import hashlib
from datetime import date

# ---------- 基础工具 ----------

def today_or(s):
    return s or str(date.today())

def parse_coord(value):
    try:
        lat, lng = value.split(",")
        return float(lat), float(lng)
    except Exception:
        return None

def haversine_m(p, q):
    R = 6371000.0
    lat1, lng1, lat2, lng2 = map(math.radians, [p[0], p[1], q[0], q[1]])
    dlat, dlng = lat2 - lat1, lng2 - lng1
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlng/2)**2
    return 2*R*math.asin(math.sqrt(a))

def rowdict(r):
    return dict(r) if r is not None else None

def jloads(s, default=None):
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default

CONFLICT_DISTANCE_M = 300.0
NAME_SUFFIXES = ["服务区", "补给点", "服务点", "驿站民居", "驿站", "民居", "井"]

def name_core(name):
    core = name.strip()
    changed = True
    while changed:
        changed = False
        for suf in NAME_SUFFIXES:
            if core.endswith(suf) and len(core) > len(suf):
                core = core[:-len(suf)]
                changed = True
    return core

# ---------- 目标读取 ----------

def get_target(conn, table, tid):
    if table == "entity":
        return rowdict(conn.execute("SELECT * FROM entities WHERE id=?", (tid,)).fetchone())
    if table == "service":
        return rowdict(conn.execute("SELECT * FROM service_points WHERE id=?", (tid,)).fetchone())
    return None

def target_name(conn, table, tid):
    t = get_target(conn, table, tid)
    return t["name"] if t else f"{table}:{tid}(缺失)"

def list_targets(conn):
    ent = [rowdict(r) for r in conn.execute(
        "SELECT 'entity' AS t, id, kind, name FROM entities ORDER BY kind, id")]
    svc = [rowdict(r) for r in conn.execute(
        "SELECT 'service' AS t, id, kind, name FROM service_points ORDER BY id")]
    return ent, svc

def source_ref(conn, sid):
    r = conn.execute("SELECT ref,title,license_status,license_expires FROM sources WHERE id=?",
                     (sid,)).fetchone()
    return rowdict(r)

# ---------- 陈述：新增 / 修订 / 撤回 ----------

def _bump_dirty(conn, table, tid):
    if table == "entity":
        conn.execute("UPDATE entities SET dirty=1 WHERE id=?", (tid,))
    else:
        conn.execute("UPDATE service_points SET dirty=1 WHERE id=?", (tid,))

def enqueue(conn, table, tid, reason, now):
    conn.execute(
        "INSERT INTO publish_tasks(target_table,target_id,reason,status,created)"
        " VALUES (?,?,?,'pending',?)", (table, tid, reason, now))

def save_statement(conn, target_table, target_id, kind, value_type, claim, value,
                   source_id, valid_from=None, valid_to=None, reviewer=None, note=None,
                   now=None):
    """编辑接口保存：历史证据 / 口述推测 / 当代提示。每次保存为一条带出处的新陈述。"""
    now = today_or(now)
    cur = conn.execute(
        "INSERT INTO statements(target_table,target_id,kind,value_type,claim,value,source_id,"
        "version,status,valid_from,valid_to,reviewer,created,note)"
        " VALUES (?,?,?,?,?,?,?,1,'active',?,?,?,?,?)",
        (target_table, target_id, kind, value_type, claim, value, source_id,
         valid_from, valid_to, reviewer, now, note))
    _bump_dirty(conn, target_table, target_id)
    enqueue(conn, target_table, target_id, "新增陈述", now)
    conn.commit()
    return cur.lastrowid

def revise_statement(conn, sid, claim, value=None, source_id=None,
                     valid_from=None, valid_to=None, note=None, reviewer=None, now=None):
    """井址考证修订：旧陈述置 superseded，产生同链新版本（出处可更新）。"""
    now = today_or(now)
    old = conn.execute("SELECT * FROM statements WHERE id=?", (sid,)).fetchone()
    if not old:
        raise ValueError("陈述不存在")
    conn.execute("UPDATE statements SET status='superseded' WHERE id=?", (sid,))
    cur = conn.execute(
        "INSERT INTO statements(target_table,target_id,kind,value_type,claim,value,source_id,"
        "version,supersedes,status,valid_from,valid_to,reviewer,created,note)"
        " VALUES (?,?,?,?,?,?,?,?,?,'active',?,?,?,?,?)",
        (old["target_table"], old["target_id"], old["kind"], old["value_type"], claim,
         value if value is not None else old["value"],
         source_id or old["source_id"], old["version"] + 1, sid,
         valid_from if valid_from is not None else old["valid_from"],
         valid_to if valid_to is not None else old["valid_to"],
         reviewer, now, note))
    _bump_dirty(conn, old["target_table"], old["target_id"])
    enqueue(conn, old["target_table"], old["target_id"], f"陈述 #{sid} 考证修订", now)
    conn.commit()
    return cur.lastrowid

def withdraw_statement(conn, sid, now=None):
    """撤回（例如提示被撤下）。旧任务重建时该陈述不会重新出现。"""
    now = today_or(now)
    st = conn.execute("SELECT * FROM statements WHERE id=?", (sid,)).fetchone()
    if not st:
        raise ValueError("陈述不存在")
    conn.execute("UPDATE statements SET status='withdrawn' WHERE id=?", (sid,))
    _bump_dirty(conn, st["target_table"], st["target_id"])
    enqueue(conn, st["target_table"], st["target_id"], f"陈述 #{sid} 撤回", now)
    conn.commit()

# ---------- 时间窗与许可 ----------

def window_state(valid_from, valid_to, day):
    if valid_from and day < valid_from:
        return "upcoming"
    if valid_to and day > valid_to:
        return "expired"
    return "active"

def photo_allowed(src, day):
    if not src or src["license_status"] != "valid":
        return False
    exp = src["license_expires"]
    return not exp or exp >= day

# ---------- 冲突检测 ----------

def _conflict_key(st):
    if st["value_type"] == "coordinate":
        return "coord"
    v = jloads(st["value"], {})
    if isinstance(v, dict) and "coord" in v:
        return "coord"
    if st["value_type"] == "fact" and isinstance(v, dict) and len(v) == 1:
        return list(v.keys())[0]
    return st["value_type"]

def scan_conflicts(conn, table, tid):
    """同目标、同属性键的活跃陈述相互矛盾（坐标相差 >300m 或标量不同）即冲突。
    当代提示(tip)是时间性的，不参与事实冲突。"""
    rows = [rowdict(r) for r in conn.execute(
        "SELECT * FROM statements WHERE target_table=? AND target_id=? AND status='active'"
        " AND value_type IN ('coordinate','fact') ORDER BY id", (table, tid))]
    groups = {}
    for st in rows:
        groups.setdefault(_conflict_key(st), []).append(st)
    conflicts = []
    for key, sts in groups.items():
        if key == "coord":
            coords = [(st, parse_coord(st["value"] or jloads(st["value"], {}).get("coord", "")))
                      for st in sts]
            coords = [(st, c) for st, c in coords if c]
            for i in range(len(coords)):
                for j in range(i + 1, len(coords)):
                    if haversine_m(coords[i][1], coords[j][1]) > CONFLICT_DISTANCE_M:
                        conflicts.append([coords[i][0], coords[j][0]])
        else:
            vals = {}
            for st in sts:
                v = jloads(st["value"], {})
                scalar = v.get(key) if isinstance(v, dict) else st["value"]
                vals.setdefault(str(scalar), []).append(st)
            if len(vals) > 1:
                conflicts.append(sts)
    return conflicts

# ---------- 关系：推断 / 人工确认 / 驳回 ----------

def _rel_exists(conn, a_table, a_id, b_table, b_id, rtype):
    return conn.execute(
        "SELECT 1 FROM relations WHERE a_table=? AND a_id=? AND b_table=? AND b_id=? AND rtype=?",
        (a_table, a_id, b_table, b_id, rtype)).fetchone() is not None

def run_inference(conn, now=None):
    """带证据的关系推断：
    (1) 历史实体与当前服务实体名称核心相同 → same_name 候选（仅同名，绝不可当作同一/可用）；
    (2) 陈述事实中的 uses / made_by 引用 → 对应候选关系。
    所有推断均为 candidate，等待人工确认。"""
    now = today_or(now)
    created = []
    wells = [rowdict(r) for r in conn.execute("SELECT * FROM entities WHERE kind='well'")]
    services = [rowdict(r) for r in conn.execute("SELECT * FROM service_points")]
    for w in wells:
        for s in services:
            if name_core(w["name"]) and name_core(w["name"]) == name_core(s["name"]):
                if not _rel_exists(conn, "entity", w["id"], "service", s["id"], "same_name"):
                    cur = conn.execute(
                        "INSERT INTO relations(a_table,a_id,b_table,b_id,rtype,provenance,status,"
                        "note) VALUES ('entity',?,'service',?,'same_name','inferred','candidate',?)",
                        (w["id"], s["id"], "名称相同不代表同一实体，亦不代表古井/服务仍可用；需人工确认"))
                    created.append(cur.lastrowid)
    facts = conn.execute(
        "SELECT * FROM statements WHERE status='active' AND value_type='fact'").fetchall()
    for st in facts:
        v = jloads(st["value"], {})
        if not isinstance(v, dict):
            continue
        for rtype, ref in (("uses", v.get("uses")), ("made_by", v.get("made_by"))):
            if not ref:
                continue
            b_table = "entity"  # 种子中的引用均指向历史实体
            if not _rel_exists(conn, st["target_table"], st["target_id"], b_table, ref, rtype):
                cur = conn.execute(
                    "INSERT INTO relations(a_table,a_id,b_table,b_id,rtype,provenance,status,"
                    "evidence_statement_ids,note)"
                    " VALUES (?,?,?,?,?,'inferred','candidate',?,?)",
                    (st["target_table"], st["target_id"], b_table, ref, rtype,
                     json.dumps([st["id"]]), "依据带出处的事实陈述推断，待人工确认"))
                created.append(cur.lastrowid)
    conn.commit()
    return created

def add_manual_relation(conn, a_table, a_id, b_table, b_id, rtype, reviewer, note=None, now=None):
    """完全人工确认：直接 approved（与带证据推断的候选分开记录 provenance）。"""
    now = today_or(now)
    if _rel_exists(conn, a_table, a_id, b_table, b_id, rtype):
        raise ValueError("关系已存在")
    cur = conn.execute(
        "INSERT INTO relations(a_table,a_id,b_table,b_id,rtype,provenance,status,reviewer,reviewed,note)"
        " VALUES (?,?,?,?,?,'manual','approved',?,?,?)",
        (a_table, a_id, b_table, b_id, rtype, reviewer, now, note))
    for t in ((a_table, a_id), (b_table, b_id)):
        _bump_dirty(conn, *t)
        enqueue(conn, t[0], t[1], "人工确认新关系，需级联重建", now)
    conn.commit()
    return cur.lastrowid

def decide_relation(conn, rid, decision, reviewer, now=None):
    """人工对推断候选作决定：approved / rejected。"""
    now = today_or(now)
    rel = conn.execute("SELECT * FROM relations WHERE id=?", (rid,)).fetchone()
    if not rel:
        raise ValueError("关系不存在")
    if decision not in ("approved", "rejected"):
        raise ValueError("决定无效")
    conn.execute("UPDATE relations SET status=?, reviewer=?, reviewed=? WHERE id=?",
                 (decision, reviewer, now, rid))
    for t in ((rel["a_table"], rel["a_id"]), (rel["b_table"], rel["b_id"])):
        _bump_dirty(conn, *t)
        enqueue(conn, t[0], t[1], f"关系 #{rid} {decision}", now)
    conn.commit()

def relations_of(conn, table, tid, status=None):
    q = ("SELECT * FROM relations WHERE ((a_table=? AND a_id=?) OR (b_table=? AND b_id=?))")
    args = [table, tid, table, tid]
    if status:
        q += " AND status=?"
        args.append(status)
    out = []
    for r in conn.execute(q + " ORDER BY id", args):
        d = rowdict(r)
        if d["a_table"] == table and d["a_id"] == tid:
            d["other"], d["dir"] = (d["b_table"], d["b_id"]), "out"
        else:
            d["other"], d["dir"] = (d["a_table"], d["a_id"]), "in"
        out.append(d)
    return out

# ---------- 文章构建（发布快照的数据源） ----------

REL_LABEL = {"same_name": "同名（独立实体）", "uses": "行经补水", "made_by": "采用工艺"}

def build_article(conn, table, tid, day=None, publishing=None):
    """从当前库组装文章数据。只收 active 陈述、只连 approved 关系；
    照片按许可状态门控；提示按时间窗标注；坐标全部按不确定程度分级。
    publishing：本次发布闭包内的键集合，其中的邻居视为“同批已发布”。"""
    day = today_or(day)
    publishing = publishing or set()
    target = get_target(conn, table, tid)
    if not target:
        return None
    art = {"doc_key": f"{table}:{tid}", "target_table": table, "target_id": tid,
           "generated_at": day, "kind": target["kind"], "name": target["name"],
           "summary": target.get("summary") or target.get("notes") or "",
           "coords": [], "facts": [], "tips": [], "photos": [], "neighbors": [],
           "conflicts": [], "service": None}

    sts = conn.execute(
        "SELECT * FROM statements WHERE target_table=? AND target_id=? AND status='active'"
        " ORDER BY id", (table, tid)).fetchall()
    coord_ids_in_conflict = set()
    conflicts = scan_conflicts(conn, table, tid)
    for grp in conflicts:
        g = []
        for st in grp:
            coord_ids_in_conflict.add(st["id"])
            src = source_ref(conn, st["source_id"])
            g.append({"sid": st["id"], "claim": st["claim"], "source_ref": src and src["ref"],
                      "kind": st["kind"]})
        art["conflicts"].append(g)

    for st in sts:
        st = rowdict(st)
        src = source_ref(conn, st["source_id"])
        base = {"sid": st["id"], "claim": st["claim"], "kind": st["kind"],
                "source_ref": src and src["ref"], "source_title": src and src["title"]}
        if st["value_type"] == "coordinate" or (
                st["value_type"] == "fact" and isinstance(jloads(st["value"]), dict)
                and "coord" in jloads(st["value"])):
            c = parse_coord(st["value"]) or parse_coord(jloads(st["value"], {}).get("coord", ""))
            if c:
                # 历史坐标一律"约"；口述坐标是"待考"；绝不在此生成精确图钉。
                certainty = "oral-uncertain" if st["kind"] == "oral" else "approx"
                if table == "service":
                    certainty = "precise" if target["coord_certain"] else "uncertain"
                art["coords"].append({"lat": c[0], "lng": c[1], "certainty": certainty,
                                      "conflict": st["id"] in coord_ids_in_conflict, **base})
        elif st["value_type"] == "photo":
            allowed = photo_allowed(src, day)
            art["photos"].append({"allowed": allowed,
                                  "expires": src and src["license_expires"], **base})
        elif st["kind"] == "tip" or st["value_type"] == "tip":
            v = jloads(st["value"], {})
            art["tips"].append({"state": window_state(st["valid_from"], st["valid_to"], day),
                                "valid_from": st["valid_from"], "valid_to": st["valid_to"],
                                "service": v.get("service"), **base})
        elif st["value_type"] == "fact":
            art["facts"].append(base)

    if table == "service":
        closed = target["closed_effective"]
        art["service"] = {
            "status_record": target["status"],
            "closed_effective": closed,
            "status_on_day": "closed" if closed and day >= closed else "open",
            "services": jloads(target["services"], []),
            "coord_certain": bool(target["coord_certain"]),
            "lat": target["lat"], "lng": target["lng"]}

    for rel in relations_of(conn, table, tid, status="approved"):
        ot, oid = rel["other"]
        nkey = f"{ot}:{oid}"
        pub = (nkey in publishing) or conn.execute(
            "SELECT 1 FROM published_docs WHERE doc_key=?", (nkey,)).fetchone() is not None
        art["neighbors"].append({
            "rtype": rel["rtype"], "rtype_label": REL_LABEL.get(rel["rtype"], rel["rtype"]),
            "provenance": rel["provenance"], "dir": rel["dir"],
            "other_table": ot, "other_id": oid,
            "other_name": target_name(conn, ot, oid), "other_published": pub})

    art["source_hash"] = article_hash(art)
    return art

_HASH_KEYS = ["doc_key", "kind", "name", "summary", "coords", "facts", "tips", "photos",
              "neighbors", "conflicts", "service"]

def article_hash(art):
    payload = {k: art.get(k) for k in _HASH_KEYS}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

def rebase_snapshot(art, day):
    """公开页只依据服务端【发布快照】重算时间敏感状态：
    - 服务点开关：按快照中的 closed_effective 与查询日；
    - 当代提示：按快照中的有效窗口；
    - 照片许可：按快照中的授权到期日。
    正文事实、坐标、关系、冲突全部保持发布时冻结，绝不回读编辑库。"""
    art = dict(art)
    svc = art.get("service")
    if svc:
        svc = dict(svc)
        closed = svc.get("closed_effective")
        svc["status_on_day"] = "closed" if closed and day >= closed else "open"
        art["service"] = svc
    art["tips"] = [{**t, "state": window_state(t.get("valid_from"), t.get("valid_to"), day)}
                   for t in art.get("tips", [])]
    photos = []
    for p in art.get("photos", []):
        p = dict(p)
        exp = p.get("expires")
        # 许可有效性完全按快照中记录的授权到期日随查询日重算；
        # 现实中许可状态变更（撤销等）会产生编辑变更并重新发布新快照。
        p["allowed"] = not exp or exp >= day
        photos.append(p)
    art["photos"] = photos
    art["viewed_at"] = day
    return art

# ---------- 发布队列与级联重建 ----------

def enqueue_dirty(conn, now=None):
    now = today_or(now)
    n = 0
    for (t, i) in conn.execute(
            "SELECT 'entity', id FROM entities WHERE dirty=1 UNION ALL "
            "SELECT 'service', id FROM service_points WHERE dirty=1"):
        enqueue(conn, t, i, "数据变更", now)
        n += 1
    conn.commit()
    return n

def _approved_neighbors(conn, table, tid):
    out = []
    for rel in relations_of(conn, table, tid, status="approved"):
        out.append(rel["other"])
    return out

def _closure(conn, seeds):
    seen, stack = set(), list(seeds)
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(_approved_neighbors(conn, cur[0], cur[1]))
    return seen

def pending_targets(conn):
    return [(r["target_table"], r["target_id"]) for r in conn.execute(
        "SELECT DISTINCT target_table,target_id FROM publish_tasks WHERE status='pending'")]

def run_publish_tasks(conn, day=None, renderers=None):
    """执行后台发布：重建所有受影响文章及打印卡。
    - 经批准关系做闭包级联；
    - 重建内容来自当前库快照：已撤提示/过期许可自然不会被旧任务带上线。"""
    day = today_or(day)
    seeds = pending_targets(conn)
    if not seeds:
        return []
    affected = _closure(conn, seeds)
    publishing = {f"{t}:{i}" for (t, i) in affected}
    rebuilt = []
    for (t, i) in sorted(affected):
        art = build_article(conn, t, i, day, publishing=publishing)
        if art is None:
            continue
        html, card_html = renderers(art) if renderers else (None, None)
        conn.execute(
            "INSERT INTO published_docs(doc_key,target_table,target_id,title,html,source_hash,published_at)"
            " VALUES (?,?,?,?,?,?,?) ON CONFLICT(doc_key) DO UPDATE SET title=excluded.title,"
            "html=excluded.html,source_hash=excluded.source_hash,published_at=excluded.published_at",
            (art["doc_key"], t, i, art["name"], html or "", art["source_hash"], day))
        conn.execute(
            "INSERT INTO published_cards(card_key,target_table,target_id,html,source_hash,published_at)"
            " VALUES (?,?,?,?,?,?) ON CONFLICT(card_key) DO UPDATE SET html=excluded.html,"
            "source_hash=excluded.source_hash,published_at=excluded.published_at",
            (f"card:{t}:{i}", t, i, card_html or "", art["source_hash"], day))
        if t == "entity":
            conn.execute("UPDATE entities SET dirty=0 WHERE id=?", (i,))
        else:
            conn.execute("UPDATE service_points SET dirty=0 WHERE id=?", (i,))
        rebuilt.append((t, i))
    conn.execute("UPDATE publish_tasks SET status='done', finished=? WHERE status='pending'",
                 (day,))
    conn.commit()
    return rebuilt

def publish_state(conn, table, tid, day=None):
    """当前数据 vs 服务端实际发布记录：最新 / 待重建 / 未发布。"""
    day = today_or(day)
    art = build_article(conn, table, tid, day)
    doc = conn.execute("SELECT * FROM published_docs WHERE doc_key=?",
                       (f"{table}:{tid}",)).fetchone()
    if not doc:
        return "unpublished", art, None
    if doc["source_hash"] != art["source_hash"]:
        return "stale", art, rowdict(doc)
    return "current", art, rowdict(doc)

# ---------- 地图 ----------

def public_map_points(conn):
    """公开地图点位：
    - 补给点：确定坐标给精确图钉；不确定坐标只给虚线圈，不补精确图钉；
    - 历史实体：只用已发布文章里的约/待考圈，且仅显示已发布实体。"""
    points = []
    for sp in conn.execute("SELECT * FROM service_points ORDER BY id"):
        sp = rowdict(sp)
        pub = conn.execute("SELECT 1 FROM published_docs WHERE doc_key=?",
                           (f"service:{sp['id']}",)).fetchone()
        if not pub:
            continue
        if sp["coord_certain"] and sp["lat"] is not None:
            points.append({"lat": sp["lat"], "lng": sp["lng"], "marker": "pin",
                           "name": sp["name"], "key": f"service:{sp['id']}"})
        else:
            points.append({"marker": "uncertain", "name": sp["name"],
                           "key": f"service:{sp['id']}"})
    for r in conn.execute(
            "SELECT doc_key,target_id,html FROM published_docs WHERE target_table='entity'"):
        art = build_article(conn, "entity", r["target_id"])
        for c in art["coords"]:
            points.append({"lat": c["lat"], "lng": c["lng"], "marker": c["certainty"],
                           "name": art["name"], "key": art["doc_key"]})
    return points

# ---------- 行程核验 ----------

def _water_tips_for_service(conn, sid, travel_date):
    """收集补水提示：服务点自身的 + 经【已批准】关系关联的历史实体上的。
    未批准的同名候选不得把古井提示带给当前补给点。"""
    tips = []
    targets = [("service", sid)]
    targets += [rel["other"] for rel in relations_of(conn, "service", sid, status="approved")]
    for (t, i) in targets:
        for st in conn.execute(
                "SELECT * FROM statements WHERE target_table=? AND target_id=? AND status='active'"
                " AND value_type='tip'", (t, i)):
            v = jloads(st["value"], {})
            if v.get("service") != "water":
                continue
            if window_state(st["valid_from"], st["valid_to"], travel_date) != "active":
                continue
            src = source_ref(conn, st["source_id"])
            tips.append({"claim": st["claim"], "source_ref": src and src["ref"],
                         "kind": st["kind"], "from": (t, i)})
    return tips

def check_itinerary(conn, iid, travel_date, today=None):
    """旅行资料检查：只用用户选定的【已审行程】与【有效窗口】；
    补给信息不足如实报缺口；系统不自动保证可通行。"""
    today = today_or(today)
    it = conn.execute("SELECT * FROM itineraries WHERE id=?", (iid,)).fetchone()
    if not it:
        return {"ok": False, "error": "行程不存在"}
    if it["status"] != "reviewed":
        return {"ok": False, "error": "该行程尚未审定，不能用于旅行资料检查"}
    if not (it["valid_from"] <= travel_date <= it["valid_to"]):
        return {"ok": False, "error": f"出行日 {travel_date} 不在行程有效窗口"
                                     f"（{it['valid_from']} 至 {it['valid_to']}）内"}
    stops = jloads(it["stops"], [])
    results, blocks, gaps = [], [], []
    for idx, stop in enumerate(stops, 1):
        t, sid = stop["table"], stop["id"]
        sp = get_target(conn, t, sid)
        if not sp:
            blocks.append({"stop": idx, "problem": "停靠点资料缺失"})
            continue
        closed = sp["closed_effective"]
        is_closed = closed and travel_date >= closed
        tips = _water_tips_for_service(conn, sid, travel_date)
        entry = {"stop": idx, "id": sid, "name": sp["name"],
                 "status_on_date": "closed" if is_closed else "open", "tips": tips}
        if is_closed:
            entry["verdict"] = "blocked"
            entry["detail"] = f"补给点已关闭（{closed} 起生效），不能作为补水点"
            blocks.append(entry)
        elif not sp["coord_certain"] or sp["lat"] is None:
            entry["verdict"] = "gap"
            entry["detail"] = "坐标未确定，且无已审定位；不能在地图上补成精确图钉，也不能保证可到达"
            gaps.append(entry)
        elif tips and all(x["kind"] == "oral" for x in tips):
            entry["verdict"] = "gap"
            entry["detail"] = "只有口述提示，缺少已审证据；信息缺口：补水可靠性未确认，不能据此保证通行"
            gaps.append(entry)
        elif "water" not in jloads(sp["services"], []):
            entry["verdict"] = "gap"
            entry["detail"] = "补给信息不足：发布记录未确认该点提供补水"
            gaps.append(entry)
        else:
            if not tips:
                entry["verdict"] = "ok"
                entry["detail"] = "据发布记录该窗口内可补水；现场情况仍需自行核实，系统不自动保证可通行"
            else:
                entry["verdict"] = "ok"
                entry["detail"] = "据发布记录与有效窗口内提示可补水；提示非通行保证，需自行核实"
        results.append(entry)
    return {"ok": not blocks and not gaps, "itinerary": it["name"],
            "travel_date": travel_date, "checked_at": today,
            "window": [it["valid_from"], it["valid_to"]],
            "stops": results, "blocks": blocks, "gaps": gaps,
            "disclaimer": "检查基于服务端发布记录与已审行程，算法不自动保证道路可通行"}

# ---------- 离线包 ----------

def build_bundle(conn, keys, day=None, valid_days=30):
    day = today_or(day)
    docs = []
    for key in keys:
        r = conn.execute("SELECT * FROM published_docs WHERE doc_key=?", (key,)).fetchone()
        if not r:
            raise ValueError(f" {key} 尚未发布，不能进入离线包")
        docs.append({"key": key, "title": r["title"], "html": r["html"],
                     "published_at": r["published_at"]})
    y, m, d = map(int, day.split("-"))
    from datetime import timedelta
    valid_to = str(date(y, m, d) + timedelta(days=valid_days))
    payload = json.dumps({"built_at": day, "valid_to": valid_to, "docs": docs},
                         ensure_ascii=False)
    cur = conn.execute(
        "INSERT INTO bundles(doc_keys,built_at,valid_to,payload) VALUES (?,?,?,?)",
        (json.dumps(keys), day, valid_to, payload))
    conn.commit()
    return cur.lastrowid, valid_to

def open_bundle(conn, bid, day=None):
    day = today_or(day)
    r = conn.execute("SELECT * FROM bundles WHERE id=?", (bid,)).fetchone()
    if not r:
        return None
    data = json.loads(r["payload"])
    data["expired"] = day > r["valid_to"]
    data["opened_at"] = day
    return data

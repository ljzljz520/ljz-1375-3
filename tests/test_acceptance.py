"""Acceptance tests for the five required scenarios plus core invariants.

Scenarios:
1. 补给点关闭        – closure creates a travel-check gap from published data
2. 井址考证修订      – revision creates new version; old claim withdraws on
                       approval; exact pins require re-confirmation
3. 来源冲突          – conflicting statements open a conflict, never auto-pick
4. 照片许可失效      – lapsed photos vanish on republish
5. 离线包过期        – expired packages are refused; low-bandwidth package
                       keeps times/sources

Cross-cutting: historical/current entities with identical names are
independent; only approved relations are shown; unverified coordinates are
never pinned; cancelled publish tasks cannot resurrect withdrawn tips;
travel checks need a reviewed itinerary in window.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import core
from app.db import connect, init_db
from app.seed import seed, ANCHOR


class ArchiveCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "t.db"
        self.conn = connect(self.path)
        init_db(self.conn)
        core.TODAY_OVERRIDE = ANCHOR
        self.ids = seed(self.conn)
        self.I = self.ids

    def tearDown(self):
        core.TODAY_OVERRIDE = None
        self.conn.close()
        self.tmp.cleanup()

    # -------------------------------------------------- shared-name rule
    def test_same_name_is_not_a_link(self):
        well, sp = self.I["entities"]["well_old"], self.I["entities"]["supply_new"]
        self.assertEqual(
            core.get_entity(self.conn, well)["name"],
            core.get_entity(self.conn, sp)["name"])
        rel = core.get_relation(self.conn, self.I["relations"]["name_proposal"])
        self.assertFalse(rel["approved"])          # oral name-match stays proposed
        # approval fails while the supporting statement is only speculation
        with self.assertRaises(core.DomainError) as cx:
            core.approve_relation(self.conn, rel["id"])
        self.assertEqual(cx.exception.code, "statement_not_approved")
        graph = _graph_published(self.conn)
        pairs = {(e["from"], e["to"]) for e in graph["edges"]}
        self.assertIn((sp, well), pairs)           # manually confirmed serves
        self.assertNotIn((well, sp), pairs)        # name-only proposal excluded

    def test_unverified_coordinates_cannot_become_pin(self):
        # create an exact location attempt backed by the proposed oral claim
        with self.assertRaises(core.DomainError) as cx:
            core.add_location(self.conn, self.I["entities"]["well_old"],
                              self.I["statements"]["oral_pos"], "exact", 40.35, 95.12)
        self.assertEqual(cx.exception.code, "unverified_pin")
        pins = {n["entity_id"]: n for n in core.active_locations(self.conn)}
        old = pins[self.I["entities"]["well_old"]]
        self.assertEqual(old["precision"], "approximate")  # stays approximate

    # ------------------------------------------------ scenario 1: closure
    def test_scenario_1_supply_point_closure(self):
        well_old, sp = self.I["entities"]["well_old"], self.I["entities"]["supply_new"]
        # before closure, the historical well is reachable only via the
        # independently approved 'serves' relation
        res = core.travel_check(self.conn, self.I["itineraries"]["reviewed"], ANCHOR)
        seg2 = next(s for s in res["segments"] if s["seq"] == 2)
        self.assertEqual(seg2["state"], "available")
        self.assertEqual(seg2["assessment"]["reason"], "via_approved_supply")
        self.assertFalse(res["guaranteed_passage"])

        # editor closes the contemporary point, adds no covering window,
        # enqueues rebuild for BOTH affected articles and runs the queue
        core.upsert_supply_point(self.conn, sp, "closed", services=[],
                                 note="水源枯竭，2026-10 起永久关闭")
        core.enqueue_publish(self.conn, [sp, well_old], "验收：补给点关闭")
        done = core.run_pending_tasks(self.conn)
        self.assertEqual({d["entity_id"] for d in done}, {sp, well_old})

        res2 = core.travel_check(self.conn, self.I["itineraries"]["reviewed"], ANCHOR)
        states = {s["seq"]: s for s in res2["segments"]}
        self.assertEqual(states[1]["state"], "gap")
        self.assertEqual(states[1]["assessment"]["reason"], "closed")
        self.assertEqual(states[2]["state"], "gap")
        # same name never rescues the historical well
        self.assertEqual(states[2]["assessment"]["reason"], "well_is_not_service_entity")
        self.assertEqual(states[3]["state"], "gap")
        self.assertTrue(any(g["seq"] in (1, 2, 3) for g in res2["gaps"]))
        self.assertFalse(res2["guaranteed_passage"])

        # article published doc reflects closure
        doc = __import__("json").loads(
            core.latest_publication(self.conn, sp)["doc"])
        self.assertEqual(doc["supply"]["status"], "closed")

    def test_travel_check_requires_reviewed_itinerary_and_window(self):
        with self.assertRaises(core.DomainError) as cx:
            core.travel_check(self.conn, self.I["itineraries"]["draft"], ANCHOR)
        self.assertEqual(cx.exception.code, "itinerary_not_reviewed")
        with self.assertRaises(core.DomainError) as cx:
            core.travel_check(self.conn, self.I["itineraries"]["reviewed"], "2026-09-30")
        self.assertEqual(cx.exception.code, "outside_window")

    # --------------------------------------- scenario 2: well-site revision
    def test_scenario_2_well_site_revision(self):
        old_claim = self.I["statements"]["dug"]
        # editor records new textual research as a revision
        rev = core.revise_statement(
            self.conn, old_claim, value="清乾隆四十九年",
            source_id=self.I["sources"]["qing_ce"], note="新见驿站碑铭拓片")
        self.assertEqual(rev["confidence"], "proposed")
        self.assertEqual(rev["supersedes"], old_claim)
        versions = [v["version"] for v in _versions(self.conn, rev["id"])]
        self.assertEqual(versions, [1])
        old_st = core.get_statement(self.conn, old_claim)
        self.assertEqual(old_st["confidence"], "approved")  # untouched pre-approval

        # while unapproved, publication still shows the old claim
        core.enqueue_publish(self.conn, [self.I["entities"]["well_old"]], "修订排队（未批）")
        core.run_pending_tasks(self.conn)
        doc = _pub_doc(self.conn, self.I["entities"]["well_old"])
        vals = [s["value"] for s in doc["statements"] if s["predicate"] == "dug_in"]
        self.assertIn("清道光七年", vals)
        self.assertNotIn("清乾隆四十九年", vals)

        # approval swaps versions atomically
        core.set_statement_confidence(self.conn, rev["id"], "approved")
        self.assertEqual(core.get_statement(self.conn, old_claim)["confidence"],
                         "withdrawn")
        core.enqueue_publish(self.conn, [self.I["entities"]["well_old"]], "井址考证修订发布")
        core.run_pending_tasks(self.conn)
        doc = _pub_doc(self.conn, self.I["entities"]["well_old"])
        vals = [s["value"] for s in doc["statements"] if s["predicate"] == "dug_in"]
        self.assertEqual(vals, ["清乾隆四十九年"])
        # complete version trail is retained in statement_version
        hist = _versions(self.conn, old_claim)
        self.assertTrue(any(v["confidence"] == "withdrawn" for v in hist))

    def test_edit_returns_claim_to_proposed_and_drops_approval(self):
        rel = self.I["relations"]["serves"]
        self.assertTrue(core.get_relation(self.conn, rel)["approved"])
        st = self.I["statements"]["supply_serves"]
        core.edit_statement(self.conn, st, value="补给点为旅人提供服务（更新措辞）")
        self.assertEqual(core.get_statement(self.conn, st)["confidence"], "proposed")
        self.assertFalse(core.get_relation(self.conn, rel)["approved"])

    # ----------------------------------------- scenario 3: source conflict
    def test_scenario_3_source_conflict_is_explicit(self):
        conflicts = core.list_conflicts(self.conn, "open")
        self.assertTrue(any(
            {self.I["statements"]["dug"], self.I["statements"]["dug_conflict"]}
            == {c["statement_a"], c["statement_b"]} for c in conflicts))
        cid = conflicts[0]["id"]
        # before ruling, BOTH claims remain in the working store and the
        # approved one keeps publishing; nothing is auto-resolved
        self.assertEqual(
            core.get_statement(self.conn, self.I["statements"]["dug"])["confidence"],
            "approved")
        resolved = core.resolve_conflict(self.conn, cid, "resolved_a",
                                         note="方志版本更早更可信")
        self.assertEqual(resolved["status"], "resolved_a")
        loser = self.I["statements"]["dug_conflict"]
        self.assertEqual(core.get_statement(self.conn, loser)["confidence"], "withdrawn")
        self.assertEqual(core.list_conflicts(self.conn, "open"), [])

    # -------------------------------------- scenario 4: photo permission
    def test_scenario_4_photo_license_lapse(self):
        sp = self.I["entities"]["supply_new"]
        # seed photo's permission ended 2026-09-01; initial publication
        # already excludes it
        doc = _pub_doc(self.conn, sp)
        self.assertEqual(doc["photos"], [])
        photo = core.get_photo(self.conn, self.I["photos"]["lapsed"])
        self.assertEqual(photo["status"], "permission_lapsed")

        # add a currently valid photo, publish → appears
        p = core.add_photo(self.conn, sp, "新补给点照片", "permission", "2026-12-31")
        self.assertTrue(core.photo_usable(core.get_photo(self.conn, p["id"])))
        core.enqueue_publish(self.conn, [sp], "验收：补充新照片")
        core.run_pending_tasks(self.conn)
        self.assertEqual(len(_pub_doc(self.conn, sp)["photos"]), 1)

        # advance time past its licence date, rebuild → removed again
        core.TODAY_OVERRIDE = "2027-01-01"
        self.assertFalse(core.photo_usable(core.get_photo(self.conn, p["id"])))
        n = core.refresh_photo_status(self.conn)
        self.assertEqual(n, 1)
        core.enqueue_publish(self.conn, [sp], "验收：许可失效")
        core.run_pending_tasks(self.conn)
        self.assertEqual(_pub_doc(self.conn, sp)["photos"], [])
        core.TODAY_OVERRIDE = ANCHOR

    # ---------------------------------------- scenario 5: offline package
    def test_scenario_5_offline_package_expiry(self):
        pkg = core.build_package(self.conn, ttl_days=30)
        got = core.get_package(self.conn, pkg["id"], "2026-11-05")
        self.assertIn("documents", got["payload"])
        with self.assertRaises(core.DomainError) as cx:
            core.get_package(self.conn, pkg["id"], "2026-11-08")
        self.assertEqual(cx.exception.code, "package_expired")

    def test_low_bandwidth_package_keeps_time_and_sources(self):
        pkg = core.build_package(self.conn, ttl_days=30, low_bandwidth=True)
        got = core.get_package(self.conn, pkg["id"])
        self.assertTrue(got["low_bandwidth"])
        well_doc = got["payload"]["documents"][self.I["entities"]["well_old"]]
        self.assertEqual(well_doc["photos"], [])                       # stripped
        self.assertNotIn("lat", well_doc["location"])                 # stripped
        self.assertTrue(well_doc["statements"])                       # kept
        for s in well_doc["statements"]:
            self.assertTrue(s["source"]["title"])                     # attribution kept
        self.assertEqual(got["payload"]["generated_at"], ANCHOR)      # time kept
        supply_doc = got["payload"]["documents"][self.I["entities"]["supply_new"]]
        self.assertTrue(supply_doc["supply"]["windows"])              # dates kept

    # ------------------------------------ publishing: no resurrection
    def test_old_task_cannot_resurrect_withdrawn_tip(self):
        sp = self.I["entities"]["supply_new"]
        # a contemporary tip is approved and published
        tip = core.create_statement(
            self.conn, sp, self.I["sources"]["survey"], "tip",
            value="院内新到应急水箱，24 小时开放", category="tip")
        core.set_statement_confidence(self.conn, tip["id"], "approved")
        core.enqueue_publish(self.conn, [sp], "提示上线")
        core.run_pending_tasks(self.conn)
        self.assertTrue(any("院内新到应急水箱" in s["value"]
                            for s in _pub_doc(self.conn, sp)["statements"]))

        # a later task is queued, then the tip withdrawn BEFORE it runs
        core.enqueue_publish(self.conn, [sp], "其他更新（含撤提示）")
        core.set_statement_confidence(self.conn, tip["id"], "withdrawn")
        done = core.run_pending_tasks(self.conn)
        self.assertTrue(done)
        self.assertFalse(any("院内新到应急水箱" in s["value"]
                             for s in _pub_doc(self.conn, sp)["statements"]))

        # cancelled tasks never execute and therefore never republish stale text
        before = core.latest_publication(self.conn, sp)["revision"]
        ids = core.enqueue_publish(self.conn, [sp], "过期任务")
        core.cancel_task(self.conn, ids[0])
        self.assertEqual(core.run_pending_tasks(self.conn), [])
        self.assertEqual(core.latest_publication(self.conn, sp)["revision"], before)

    def test_website_only_reads_publication_table(self):
        # a brand new entity has working data but is invisible publicly
        ent = core.create_entity(self.conn, "dwelling", "未发布小院", era="民国")
        st = core.create_statement(self.conn, ent["id"],
                                   self.I["sources"]["gazetteer"], "note", value="测试")
        core.set_statement_confidence(self.conn, st["id"], "approved")
        self.assertIsNone(core.latest_publication(self.conn, ent["id"]))
        graph_ids = {n["id"] for n in _graph_published(self.conn)["nodes"]}
        self.assertNotIn(ent["id"], graph_ids)

    def test_print_card_rebuilt_with_revision(self):
        well = self.I["entities"]["well_old"]
        pub = core.latest_publication(self.conn, well)
        card = __import__("json").loads(pub["cards"])["default"]
        self.assertIn("红沙井", card["text"])
        self.assertTrue(card["sources"])
        rev_before = pub["revision"]
        core.enqueue_publish(self.conn, [well], "重发卡")
        core.run_pending_tasks(self.conn)
        self.assertEqual(core.latest_publication(self.conn, well)["revision"],
                         rev_before + 1)


# ------------------------------------------------------------------ utils
def _versions(conn, sid):
    from app.db import connect as _c  # noqa
    rows = conn.execute(
        "SELECT * FROM statement_version WHERE statement_id=? ORDER BY version",
        (sid,)).fetchall()
    return [dict(r) for r in rows]


def _pub_doc(conn, eid):
    import json
    return json.loads(core.latest_publication(conn, eid)["doc"])


def _graph_published(conn):
    """Mirror of server.public_graph without HTTP."""
    nodes, edges = {}, []
    published = {e["id"] for e in core.list_entities(conn)
                 if core.latest_publication(conn, e["id"])}
    for r in core.list_relations(conn, approved_only=True):
        if r["from_entity"] in published and r["to_entity"] in published:
            edges.append({"from": r["from_entity"], "to": r["to_entity"]})
    for eid in published:
        nodes[eid] = {"id": eid}
    return {"nodes": list(nodes.values()), "edges": edges}


if __name__ == "__main__":
    unittest.main(verbosity=2)

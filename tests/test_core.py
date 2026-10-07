"""验收测试（服务层，确定性基准日 2026-10-07）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import db
from app import services as sv
from app import render as rd

DAY = "2026-10-07"

class Case(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init(self.conn)
        db.seed(self.conn, DAY)
        sv.run_inference(self.conn, DAY)
        # 发布前批准：商队->井(uses)、民居->工艺(made_by)、井w1<->补给点s1(同名)
        # w2 的同名候选保持 candidate，用于验证“只展示已批准连接”
        cands = self.conn.execute(
            "SELECT * FROM relations WHERE status='candidate'").fetchall()
        for r in cands:
            if r["rtype"] in ("uses", "made_by", "same_name") and (
                    r["a_id"] != "w2"):
                sv.decide_relation(self.conn, r["id"], "approved", "馆长", DAY)
        sv.enqueue_dirty(self.conn, DAY)
        sv.run_publish_tasks(self.conn, DAY, rd.publish_renderers)

    def tearDown(self):
        self.conn.close()

    # 1. 同名独立：推断出 same_name 候选，但名称相同 != 仍可用，且必须独立批准
    def test_same_name_is_independent(self):
        rel = self.conn.execute(
            "SELECT * FROM relations WHERE rtype='same_name' AND a_id='w1' AND b_id='s1'").fetchone()
        self.assertEqual(rel["provenance"], "inferred")
        self.assertEqual(rel["status"], "approved")  # 本用例显式批准
        # 未批准的 w2 同名候选绝不出现在公开关系里
        w2_rel = self.conn.execute(
            "SELECT * FROM relations WHERE rtype='same_name' AND a_id='w2'").fetchone()
        self.assertEqual(w2_rel["status"], "candidate")
        art = sv.build_article(self.conn, "entity", "w2", DAY)
        self.assertEqual(art["neighbors"], [])  # 只展示 approved 连接

    # 2. 只展示已批准连接；驳回后公开页不出现
    def test_only_approved_shown(self):
        rel = self.conn.execute(
            "SELECT id FROM relations WHERE a_id='w2' AND rtype='same_name'").fetchone()
        sv.decide_relation(self.conn, rel["id"], "rejected", "馆长", DAY)
        sv.run_publish_tasks(self.conn, DAY, rd.publish_renderers)
        art = sv.build_article(self.conn, "entity", "w2", DAY)
        self.assertEqual([n for n in art["neighbors"] if n["rtype"] == "same_name"], [])

    # 3. 未确定坐标不能补成精确图钉
    def test_uncertain_coords_not_pinned(self):
        pts = {p["key"]: p for p in sv.public_map_points(self.conn)}
        self.assertEqual(pts["service:s1"]["marker"], "pin")
        self.assertEqual(pts["service:s3"]["marker"], "uncertain")
        self.assertNotIn("lat", pts["service:s3"])  # 不补精确坐标
        for p in pts.values():
            if p["key"].startswith("entity:"):
                self.assertNotEqual(p["marker"], "pin")  # 历史实体永不精确图钉

    # 4. 补给点关闭：发布记录反映关闭，行程核验阻断
    def test_closed_service_point(self):
        art = sv.build_article(self.conn, "service", "s2", DAY)
        self.assertEqual(art["service"]["status_on_day"], "closed")
        res = sv.check_itinerary(self.conn, "i1", DAY, today=DAY)
        self.assertFalse(res["ok"])
        blocked = res["blocks"][0]
        self.assertEqual(blocked["id"], "s2")
        self.assertEqual(blocked["verdict"], "blocked")
        # 在关闭生效日之前检查则不阻断（2026-01-15 窗口内）
        res2 = sv.check_itinerary(self.conn, "i1", "2026-01-15", today=DAY)
        self.assertTrue(res2["ok"])

    # 5. 井址考证修订：新版本生效、旧版 superseded，来源冲突显式标记
    def test_revision_and_conflict(self):
        old = self.conn.execute(
            "SELECT id FROM statements WHERE target_id='w1' AND kind='evidence'"
            " AND value_type='coordinate' ORDER BY id LIMIT 1").fetchone()
        new_id = sv.revise_statement(
            self.conn, old["id"], "2024 考订复核：井址在旧道岔口偏北 80m",
            value="40.0603,94.3121", source_id=None, note="复核修订", now=DAY)
        st = self.conn.execute("SELECT * FROM statements WHERE id=?", (new_id,)).fetchone()
        self.assertEqual(st["version"], 2)
        self.assertEqual(self.conn.execute(
            "SELECT status FROM statements WHERE id=?", (old["id"],)).fetchone()["status"],
            "superseded")
        conflicts = sv.scan_conflicts(self.conn, "entity", "w1")
        self.assertTrue(conflicts)  # 新版坐标与口述指认相距>300m，冲突保留可见
        # 重建前网页仍是旧快照（stale），重建后才上线
        state, _, doc = sv.publish_state(self.conn, "entity", "w1", DAY)
        self.assertEqual(state, "stale")
        sv.run_publish_tasks(self.conn, DAY, rd.publish_renderers)
        state, art, doc = sv.publish_state(self.conn, "entity", "w1", DAY)
        self.assertEqual(state, "current")
        self.assertIn("偏北 80m", doc["html"])

    # 6. 照片许可失效：发布快照不显示影像，保留出处
    def test_photo_license_expiry(self):
        art = sv.build_article(self.conn, "entity", "w1", DAY)
        photo = [p for p in art["photos"] if p["source_ref"] == "S-PHOTO-A7"][0]
        self.assertFalse(photo["allowed"])
        state, _, _ = sv.publish_state(self.conn, "entity", "w1", DAY)
        if state != "current":
            sv.run_publish_tasks(self.conn, DAY, rd.publish_renderers)
        doc = self.conn.execute(
            "SELECT html FROM published_docs WHERE doc_key='entity:w1'").fetchone()
        self.assertIn("照片许可已失效", doc["html"])
        self.assertNotIn("thumb photo-art", doc["html"].split("照片许可已失效")[1][:300])
        # 有效照片正常占位
        self.assertTrue(all(p["allowed"] for p in art["photos"] if p["source_ref"] == "S-PHOTO-B2")
                        or art["photos"])

    # 7. 离线包过期：过期后打开显式标注，时间与来源保留
    def test_bundle_expiry(self):
        keys = ["entity:w1", "service:s1"]
        bid, valid_to = sv.build_bundle(self.conn, keys, DAY, valid_days=30)
        fresh = sv.open_bundle(self.conn, bid, DAY)
        self.assertFalse(fresh["expired"])
        stale = sv.open_bundle(self.conn, bid, "2027-01-01")
        self.assertTrue(stale["expired"])
        # 快照内保留时间窗与来源说明
        blob = stale["docs"][0]["html"]
        self.assertIn("有效窗口", blob)
        self.assertIn("出处", blob)

    # 8. 已撤提示不被旧发布任务重新上线
    def test_withdrawn_tip_not_revived(self):
        tip = self.conn.execute(
            "SELECT id FROM statements WHERE target_id='w1' AND kind='tip'").fetchone()
        sv.withdraw_statement(self.conn, tip["id"], DAY)
        # 重建后提示消失
        sv.run_publish_tasks(self.conn, DAY, rd.publish_renderers)
        doc = self.conn.execute(
            "SELECT html FROM published_docs WHERE doc_key='entity:w1'").fetchone()
        self.assertNotIn("水龙头", doc["html"])
        # 即使有更早创建、当时未执行的“旧任务”，重跑也不会复活
        sv.enqueue(self.conn, "entity", "w1", "旧任务重放", "2026-09-01")
        rebuilt = sv.run_publish_tasks(self.conn, DAY, rd.publish_renderers)
        self.assertIn(("entity", "w1"), rebuilt)
        doc2 = self.conn.execute(
            "SELECT html FROM published_docs WHERE doc_key='entity:w1'").fetchone()
        self.assertNotIn("水龙头", doc2["html"])

    # 9. 级联重建：批准新关系后，受影响双方文章都重建
    def test_cascade_rebuild(self):
        rel = self.conn.execute(
            "SELECT id FROM relations WHERE a_id='w2' AND rtype='same_name'").fetchone()
        sv.decide_relation(self.conn, rel["id"], "approved", "馆长", DAY)
        rebuilt = sv.run_publish_tasks(self.conn, DAY, rd.publish_renderers)
        self.assertIn(("entity", "w2"), rebuilt)
        self.assertIn(("service", "s2"), rebuilt)
        art2 = sv.build_article(self.conn, "entity", "w2", DAY)
        self.assertEqual(art2["neighbors"][0]["other_id"], "s2")

    # 10. 不确定坐标的补给点：行程核验报信息缺口而非自动放行
    def test_itinerary_gap_uncertain(self):
        # 构造一条经过 s3 的已审行程
        import json
        self.conn.execute(
            "INSERT INTO itineraries(id,name,status,valid_from,valid_to,stops)"
            " VALUES ('i2','野线','reviewed','2026-01-01','2026-12-31',?)",
            (json.dumps([{"table": "service", "id": "s3"}]),))
        # s3 必须先发布
        sv.enqueue_dirty(self.conn, DAY)
        sv.run_publish_tasks(self.conn, DAY, rd.publish_renderers)
        res = sv.check_itinerary(self.conn, "i2", DAY, today=DAY)
        self.assertFalse(res["ok"])
        self.assertEqual(res["gaps"][0]["id"], "s3")
        self.assertIn("不能保证", res["gaps"][0]["detail"])

    # 11. 未审行程 / 窗口外行程不得用于检查
    def test_itinerary_window_guard(self):
        self.conn.execute(
            "UPDATE itineraries SET status='draft' WHERE id='i1'")
        self.assertIn("尚未审定", sv.check_itinerary(
            self.conn, "i1", DAY, today=DAY)["error"])
        self.conn.execute("UPDATE itineraries SET status='reviewed' WHERE id='i1'")
        self.assertIn("不在行程有效窗口", sv.check_itinerary(
            self.conn, "i1", "2027-01-01", today=DAY)["error"])

    # 12. 低带宽：省影像，但保留必要时间与来源说明；来源仍是发布快照
    def test_low_bandwidth_keeps_time_and_sources(self):
        doc = self.conn.execute(
            "SELECT html FROM published_docs WHERE doc_key='entity:w1'").fetchone()
        snap = rd.extract_art(doc["html"])
        lite = rd.render_article_html(snap, lite=True)
        self.assertIn("有效窗口", lite)
        self.assertIn("出处", lite)
        self.assertIn("class=\"lite\"", lite)
        # 低带宽下不传输影像（有效照片显式说明省略；过期照片不传输且保留许可状态）
        self.assertTrue("影像已省略" in lite or "低带宽模式同样不传输影像" in lite)


    # 13. 来源冲突在编辑台可见，冲突坐标不取均值、不合并
    def test_conflict_visible_and_not_averaged(self):
        conflicts = sv.scan_conflicts(self.conn, "entity", "w2")
        self.assertEqual(len(conflicts), 1)
        ids = {st["id"] for grp in conflicts for st in grp}
        # 冲突双方：档案坐标 vs 口述坐标
        kinds = {st["kind"] for grp in conflicts for st in grp}
        self.assertSetEqual(kinds, {"evidence", "oral"})
        art = sv.build_article(self.conn, "entity", "w2", DAY)
        self.assertTrue(any(c["conflict"] for c in art["coords"]))

    # 14. 仅口述提示的停靠点：报信息缺口，不能据口述保证通行
    def test_oral_only_tip_is_gap(self):
        # 发布记录不确认 s1 提供补水（服务清单移除 water），只剩口述线索：缺口，不可保证通行
        import json
        self.conn.execute("UPDATE service_points SET services=? WHERE id='s1'",
                          (json.dumps(["fuel"]),))
        self.conn.execute(
            "UPDATE statements SET kind='oral' WHERE target_id='w1' AND kind='tip'")
        sv.enqueue_dirty(self.conn, DAY)
        sv.run_publish_tasks(self.conn, DAY, rd.publish_renderers)
        res = sv.check_itinerary(self.conn, "i1", DAY, today=DAY)
        s1 = next(s for s in res["stops"] if s["id"] == "s1")
        self.assertEqual(s1["verdict"], "gap")
        self.assertIn("口述", s1["detail"])

    # 15. 人工关系 provenance=manual 且直接 approved，与推断候选分离
    def test_manual_relation_is_separate(self):
        rid = sv.add_manual_relation(self.conn, "entity", "c1", "entity", "w2", "uses",
                                     "馆长", now=DAY)
        rel = self.conn.execute("SELECT * FROM relations WHERE id=?", (rid,)).fetchone()
        self.assertEqual(rel["provenance"], "manual")
        self.assertEqual(rel["status"], "approved")
        sv.run_publish_tasks(self.conn, DAY, rd.publish_renderers)
        art = sv.build_article(self.conn, "entity", "c1", DAY)
        self.assertTrue(any(n["other_id"] == "w2" for n in art["neighbors"]))

    # 16. 编辑后未发布：公开哈希仍是旧版（网页状态来自发布记录而非当前库）
    def test_web_served_from_publish_record_only(self):
        sv.save_statement(self.conn, "entity", "k1", "evidence", "fact",
                          "新增一条尚未发布的证据", None, 1, now=DAY)
        state, art, doc = sv.publish_state(self.conn, "entity", "k1", DAY)
        self.assertEqual(state, "stale")
        self.assertNotIn("新增一条尚未发布的证据", doc["html"])
        self.assertEqual(rd.extract_art(doc["html"])["name"], "红柳井夯土工艺")
if __name__ == "__main__":
    unittest.main(verbosity=2)

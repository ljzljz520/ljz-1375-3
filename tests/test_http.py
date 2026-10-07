"""HTTP 端到端集成测试：真实 HTTPServer + urllib。"""
import json
import os
import sys
import threading
import unittest
import urllib.parse
import urllib.request
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from http.server import HTTPServer
from app import db
from app.web import App, Handler

DAY = "2026-10-07"

class HttpCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sqlite3
        cls.conn = sqlite3.connect(":memory:", check_same_thread=False)
        cls.conn.row_factory = sqlite3.Row
        db.init(cls.conn)
        db.seed(cls.conn, DAY)

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None  # 303 直接作为响应返回，不自动跟随
        cls.opener = urllib.request.build_opener(NoRedirect)
        Handler.app = App(cls.conn, DAY)
        cls.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def get(self, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8")

    def post(self, path, data=None):
        body = urllib.parse.urlencode(data or {}, doseq=True).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=body,
                                     method="POST")
        try:
            with self.opener.open(req) as r:
                return r.status, r.headers.get("Location", "")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8")

    def test_full_publish_workflow_and_guards(self):
        # 发布前公开页 404
        self.assertEqual(self.get("/doc/entity:w1")[0], 404)
        self.post("/e/infer")
        # 批准全部 4 条候选
        for rid in range(1, 5):
            self.post(f"/e/relation/{rid}/decide",
                      {"decision": "approved", "reviewer": "curator"})
        pst, _ = self.post("/e/publish")
        st, html_w1 = self.get("/doc/entity:w1")
        self.assertEqual(st, 200)
        self.assertIn("照片许可已失效", html_w1)              # 许可失效门控
        self.assertIn("同名（独立实体）", html_w1)             # 已批准关系可见
        self.assertNotIn('marker:pin', html_w1)              # 历史坐标非精确图钉（SVG 页另验）

        st, maphtml = self.get("/map")
        self.assertIn("不补精确图钉", maphtml)                # s3 坐标不确定

        st, s2 = self.get("/doc/service:s2")
        self.assertIn("当前不可接受补给", s2)                 # 关闭状态来自发布记录

        st, lite = self.get("/doc/entity:w1?lowbw=1")
        self.assertIn("有效窗口", lite)
        self.assertIn("出处", lite)

        st, card = self.get("/card/entity:w1")
        self.assertIn("打印卡", card)
        self.assertIn("哈希", card)

        # 行程：当日 s2 关闭 → 未通过
        st, page = self.post_page("/itinerary/check",
                                  {"iid": "i1", "travel_date": DAY})
        self.assertIn("未通过", page)
        self.assertIn("不自动保证道路可通行", page)

        # 离线包构建 + 过期打开
        st, loc = self.post("/bundles/build",
                            {"keys": ["entity:w1", "service:s1"], "days": "30"})
        self.assertEqual(st, 303)
        st, stale = self.get("/bundles/1?day=2027-01-01")
        self.assertIn("离线包已过期", stale)
        self.assertIn("出处", stale)

    def post_page(self, path, data):
        body = urllib.parse.urlencode(data, doseq=True).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=body,
                                     method="POST")
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode()

if __name__ == "__main__":
    unittest.main(verbosity=2)

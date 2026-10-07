"""End-to-end checks through the real HTTP stack (stdlib server on an
ephemeral port)."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from app import core
from app.db import connect, init_db
from app.seed import seed, ANCHOR
from app.server import make_handler


def _req(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


class HttpCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = str(Path(cls.tmp.name) / "h.db")
        conn = connect(cls.db); init_db(conn); core.TODAY_OVERRIDE = ANCHOR
        cls.ids = seed(conn); conn.close()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.db))
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.tmp.cleanup(); core.TODAY_OVERRIDE = None

    def test_browse_article_graph_static(self):
        st, data = _req("GET", self.base + "/api/browse")
        self.assertEqual(st, 200)
        self.assertTrue(data["well"])
        well = self.ids["entities"]["well_old"]
        st, art = _req("GET", self.base + f"/api/article?entity_id={well}")
        self.assertEqual(st, 200)
        self.assertEqual(art["published_at"], ANCHOR)
        # lapsed photo never made it into the published article
        self.assertEqual(len([p for p in art["doc"]["photos"]
                              if p["id"] == self.ids["photos"]["lapsed"]]), 0)

        st, lb = _req("GET", self.base + f"/api/article?entity_id={well}&lowbw=1")
        self.assertEqual(st, 200)
        self.assertTrue(lb["doc"]["low_bandwidth"])
        self.assertEqual(lb["doc"]["photos"], [])
        self.assertTrue(lb["doc"]["statements"][0]["source"]["title"])

        st, graph = _req("GET", self.base + "/api/graph")
        self.assertEqual(st, 200)
        self.assertTrue(all(e["rtype"] for e in graph["edges"]))
        self.assertTrue(any(n["location_precision"] == "unlocated" for n in graph["nodes"]))

        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn("沙漠驿站资料馆", r.read().decode())
        with urllib.request.urlopen(self.base + "/app.js") as r:
            self.assertEqual(r.status, 200)

    def test_travel_check_rejection_and_gaps(self):
        st, body = _req("POST", self.base + "/api/travel-check",
                        {"itinerary_id": self.ids["itineraries"]["draft"],
                         "on_date": ANCHOR})
        self.assertEqual(st, 409)
        self.assertEqual(body["error"], "itinerary_not_reviewed")

        st, body = _req("POST", self.base + "/api/travel-check",
                        {"itinerary_id": self.ids["itineraries"]["reviewed"],
                         "on_date": ANCHOR})
        self.assertEqual(st, 200)
        self.assertFalse(body["guaranteed_passage"])
        self.assertTrue(any(g["reason"] == "closed" for g in body["gaps"]))

    def test_admin_editorial_flow_via_http(self):
        # add a speculative source + claim, verify it is not public, approve, publish
        st, src = _req("POST", self.base + "/api/admin/sources",
                       {"kind": "oral", "title": "新访谈", "citation": "2026-42"})
        self.assertEqual(st, 200)
        sp = self.ids["entities"]["supply_new"]
        st, claim = _req("POST", self.base + "/api/admin/statements", {
            "entity_id": sp, "source_id": src["id"], "predicate": "tip",
            "value": "周末加开补水车", "category": "tip"})
        self.assertEqual(st, 200)
        self.assertEqual(claim["confidence"], "proposed")
        st, _ = _req("GET", self.base + f"/api/article?entity_id={sp}")
        self.assertEqual(st, 200)  # entity published but claim absent
        st, _ = _req("POST", self.base +
                     f"/api/admin/statement/{claim['id']}/approve", {})
        self.assertEqual(st, 200)
        st, _ = _req("POST", self.base + "/api/admin/publish",
                     {"entity_ids": [sp], "reason": "http 流程"})
        st, ran = _req("POST", self.base + "/api/admin/run-publish", {})
        self.assertTrue(any(p["entity_id"] == sp for p in ran["published"]))
        st, art = _req("GET", self.base + f"/api/article?entity_id={sp}")
        self.assertTrue(any("周末加开补水车" in s["value"]
                            for s in art["doc"]["statements"]))

    def test_unverified_pin_rejected_over_http(self):
        st, body = _req("POST", self.base + "/api/admin/locations", {
            "entity_id": self.ids["entities"]["well_old"],
            "statement_id": self.ids["statements"]["oral_pos"],
            "precision": "exact", "lat": 40.35, "lng": 95.12})
        self.assertEqual(st, 409)
        self.assertEqual(body["error"], "unverified_pin")

    def test_package_expiry_over_http(self):
        st, pkg = _req("POST", self.base + "/api/admin/package", {"ttl_days": 10})
        self.assertEqual(st, 200)
        st, ok = _req("GET", self.base + f"/api/package/{pkg['id']}?date=2026-10-16")
        self.assertEqual(st, 200)
        st, bad = _req("GET", self.base + f"/api/package/{pkg['id']}?date=2026-11-01")
        self.assertEqual(st, 410)
        self.assertEqual(bad["error"], "package_expired")


if __name__ == "__main__":
    unittest.main()

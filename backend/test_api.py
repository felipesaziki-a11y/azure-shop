"""Run with:  python -m unittest -v   (uses a temporary database)"""
import os
import tempfile
import unittest

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["AZURE_DB"] = _tmp.name
os.environ["AZURE_GM_PASSWORD"] = "testpw"

import app as A  # noqa: E402


class ApiTests(unittest.TestCase):
    def setUp(self):
        if os.path.exists(_tmp.name):
            os.remove(_tmp.name)
        A.init_db()
        self.c = A.app.test_client()
        tok = self.c.post("/api/gm/login", json={"password": "testpw"}).get_json()["token"]
        self.h = {"Authorization": "Bearer " + tok}
        self.c.post("/api/gm/characters", json={"name": "Dante", "money": 1000}, headers=self.h)
        self.c.post("/api/gm/characters", json={"name": "Poor", "money": 10}, headers=self.h)
        chars = {c["name"]: c["id"] for c in self.c.get("/api/characters").get_json()}
        self.dante, self.poor = chars["Dante"], chars["Poor"]
        self.ids = {i["name"]: i["id"] for i in self.c.get("/api/items").get_json()}

    def buy(self, name, cid=None):
        return self.c.post("/api/buy", json={"character_id": cid or self.dante, "item_id": self.ids[name]})

    def char(self, cid):
        return next(c for c in self.c.get("/api/characters").get_json() if c["id"] == cid)

    def test_seed_and_cors(self):
        r = self.c.get("/api/items")
        self.assertEqual(len(r.get_json()), 26)
        self.assertIn("Access-Control-Allow-Origin", r.headers)
        self.assertEqual(self.c.options("/api/buy").status_code, 200)

    def test_gm_auth(self):
        self.assertEqual(self.c.post("/api/gm/mission/new").status_code, 401)
        self.assertEqual(self.c.post("/api/gm/mission/new", headers={"Authorization": "Bearer junk"}).status_code, 401)
        self.assertEqual(self.c.post("/api/gm/login", json={"password": "nope"}).status_code, 401)

    def test_buy_and_funds(self):
        self.assertEqual(self.buy("Fox Bolus").status_code, 200)
        self.assertEqual(self.char(self.dante)["money"], 955)
        r = self.buy("Bridle", self.poor)
        self.assertEqual(r.status_code, 409)
        self.assertIn("Not enough funds", r.get_json()["error"])

    def test_slots_backpack_and_mission_reset(self):
        for n in ("Fox Bolus", "Wolf Bolus", "Gorilla Bolus"):
            self.assertEqual(self.buy(n).status_code, 200)
        self.assertIn("Inventory full", self.buy("Tortoise Bolus").get_json()["error"])
        self.assertEqual(self.buy("Backpack").status_code, 200)
        self.assertEqual(self.char(self.dante)["capacity"], 5)
        self.assertEqual(self.buy("Tortoise Bolus").status_code, 200)
        self.assertIn("Limit reached", self.buy("Backpack").get_json()["error"])
        self.c.post("/api/gm/mission/new", headers=self.h)
        self.assertEqual(self.buy("Backpack").status_code, 200)

    def test_equipment_rules(self):
        self.buy("Bridle")
        self.assertIn("Already owned", self.buy("Bridle").get_json()["error"])
        self.assertEqual(self.char(self.dante)["held"], 0)

    def test_use_frees_slot(self):
        for n in ("Fox Bolus", "Wolf Bolus", "Gorilla Bolus"):
            self.buy(n)
        self.c.post("/api/use", json={"character_id": self.dante, "item_id": self.ids["Fox Bolus"]})
        self.assertEqual(self.buy("Tortoise Bolus").status_code, 200)

    def test_items_reason_per_character(self):
        items = self.c.get(f"/api/items?character_id={self.poor}").get_json()
        bridle = next(i for i in items if i["name"] == "Bridle")
        self.assertEqual(bridle["reason"], "Not enough funds")

    def test_gm_item_crud_and_stock(self):
        self.c.post("/api/gm/items", json={"name": "Test", "price": 5}, headers=self.h)
        new = next(i for i in self.c.get("/api/items").get_json() if i["name"] == "Test")
        self.c.post(f"/api/gm/items/{new['id']}/toggle", headers=self.h)
        self.assertEqual(self.c.post("/api/buy", json={"character_id": self.dante, "item_id": new["id"]}).status_code, 409)
        self.c.put(f"/api/gm/items/{new['id']}", json={"name": "Test2", "price": 7}, headers=self.h)
        self.assertEqual(self.c.delete(f"/api/gm/items/{new['id']}", headers=self.h).status_code, 200)

    def test_gm_money_grant_delete(self):
        self.c.post(f"/api/gm/characters/{self.dante}/money", json={"amount": -100, "mode": "add"}, headers=self.h)
        self.assertEqual(self.char(self.dante)["money"], 900)
        self.c.post(f"/api/gm/characters/{self.dante}/grant", json={"item_id": self.ids["Bridle"]}, headers=self.h)
        self.assertEqual(len(self.char(self.dante)["inventory"]), 1)
        self.c.delete(f"/api/gm/characters/{self.poor}", headers=self.h)
        self.assertEqual(len(self.c.get("/api/characters").get_json()), 1)


if __name__ == "__main__":
    unittest.main()

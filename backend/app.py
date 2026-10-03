"""The Azure Shop - JSON API (back-end).

Run locally:  python app.py          (http://localhost:5000)
Production:   gunicorn app:app

Environment variables:
  AZURE_GM_PASSWORD  GM password (default "changeme")
  AZURE_SECRET_KEY   signs GM tokens - set to a long random string
  ALLOWED_ORIGIN     your front-end origin, e.g. https://yourname.github.io  (default "*")
  AZURE_DB           path to the SQLite file
"""
import hmac
import json
import os
import sqlite3
import time
from functools import wraps

from flask import Flask, g, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("AZURE_DB", os.path.join(BASE_DIR, "azure_shop.db"))
GM_PASSWORD = os.environ.get("AZURE_GM_PASSWORD", "changeme")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
BASE_CAPACITY = 3
TOKEN_TTL = 60 * 60 * 12  # GM stays logged in for 12 hours

app = Flask(__name__)
serializer = URLSafeTimedSerializer(os.environ.get("AZURE_SECRET_KEY", "change-this-secret-key"), salt="gm")

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, price INTEGER NOT NULL DEFAULT 0,
    description TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT 'consumable',
    inventory_bonus INTEGER NOT NULL DEFAULT 0, per_mission_limit INTEGER, max_owned INTEGER,
    in_stock INTEGER NOT NULL DEFAULT 1, sort INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, money INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS inventory (
    character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    qty INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (character_id, item_id));
CREATE TABLE IF NOT EXISTS purchases (
    character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    mission INTEGER NOT NULL, qty INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (character_id, item_id, mission));
CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, mission INTEGER NOT NULL, message TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


# ---------------------------------------------------------------- helpers
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = connect()
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0:
        with open(os.path.join(BASE_DIR, "items_seed.json"), encoding="utf-8") as f:
            for i, it in enumerate(json.load(f)):
                conn.execute(
                    """INSERT INTO items (name, price, description, category, inventory_bonus,
                       per_mission_limit, max_owned, sort) VALUES (?,?,?,?,?,?,?,?)""",
                    (it["name"], it["price"], it["description"], it["category"],
                     it.get("inventory_bonus", 0), it.get("per_mission_limit"), it.get("max_owned"), i))
    conn.execute("INSERT OR IGNORE INTO state VALUES ('mission', '1')")
    conn.execute("INSERT OR IGNORE INTO state VALUES ('currency', 'Ahn')")
    conn.commit()
    conn.close()


def get_state(key):
    return get_db().execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()[0]


def set_state(key, value):
    get_db().execute("UPDATE state SET value=? WHERE key=?", (str(value), key))


def log_event(message):
    get_db().execute("INSERT INTO log (ts, mission, message) VALUES (?,?,?)",
                     (time.strftime("%Y-%m-%d %H:%M"), int(get_state("mission")), message))


def to_int(v, default=None):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def ok(message="", **extra):
    return jsonify(message=message, **extra)


def err(message, code=400):
    return jsonify(error=message), code


def body():
    return request.get_json(silent=True) or {}


@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    return resp


@app.errorhandler(404)
def not_found(_e):
    return err("Not found", 404)


def gm_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        try:
            serializer.loads(token, max_age=TOKEN_TTL)
        except (BadSignature, SignatureExpired):
            return err("GM login required", 401)
        return view(*args, **kwargs)
    return wrapper


# ------------------------------------------------------------- game rules
def capacity(db, cid):
    extra = db.execute(
        """SELECT COALESCE(SUM(i.inventory_bonus * inv.qty), 0) FROM inventory inv
           JOIN items i ON i.id = inv.item_id WHERE inv.character_id = ?""", (cid,)).fetchone()[0]
    return BASE_CAPACITY + extra


def consumables_held(db, cid):
    return db.execute(
        """SELECT COALESCE(SUM(inv.qty), 0) FROM inventory inv JOIN items i ON i.id = inv.item_id
           WHERE inv.character_id = ? AND i.category = 'consumable'""", (cid,)).fetchone()[0]


def owned_qty(db, cid, iid):
    r = db.execute("SELECT qty FROM inventory WHERE character_id=? AND item_id=?", (cid, iid)).fetchone()
    return r[0] if r else 0


def bought_this_mission(db, cid, iid):
    r = db.execute("SELECT qty FROM purchases WHERE character_id=? AND item_id=? AND mission=?",
                   (cid, iid, int(get_state("mission")))).fetchone()
    return r[0] if r else 0


def check_purchase(db, char, item):
    """None if allowed, otherwise a short reason."""
    if not item["in_stock"]:
        return "Out of stock"
    if char["money"] < item["price"]:
        return "Not enough funds"
    cid, iid = char["id"], item["id"]
    if item["category"] == "consumable" and consumables_held(db, cid) >= capacity(db, cid):
        return "Inventory full"
    if item["max_owned"] is not None and owned_qty(db, cid, iid) >= item["max_owned"]:
        return "Already owned"
    if item["per_mission_limit"] is not None and bought_this_mission(db, cid, iid) >= item["per_mission_limit"]:
        return "Limit reached this mission"
    return None


def add_to_inventory(db, cid, iid, qty=1):
    db.execute("""INSERT INTO inventory (character_id, item_id, qty) VALUES (?,?,?)
                  ON CONFLICT(character_id, item_id) DO UPDATE SET qty = qty + excluded.qty""", (cid, iid, qty))


def remove_from_inventory(db, cid, iid, qty=1):
    db.execute("UPDATE inventory SET qty = qty - ? WHERE character_id=? AND item_id=?", (qty, cid, iid))
    db.execute("DELETE FROM inventory WHERE qty <= 0")


def get_inventory(db, cid):
    return db.execute(
        """SELECT i.*, inv.qty FROM inventory inv JOIN items i ON i.id = inv.item_id
           WHERE inv.character_id = ? ORDER BY i.category, i.sort""", (cid,)).fetchall()


def character_json(db, c):
    return dict(id=c["id"], name=c["name"], money=c["money"], capacity=capacity(db, c["id"]),
                held=consumables_held(db, c["id"]), inventory=[dict(r) for r in get_inventory(db, c["id"])])


def find(table, id_):
    return get_db().execute(f"SELECT * FROM {table} WHERE id=?", (id_,)).fetchone()


# ------------------------------------------------------------ public API
@app.get("/api/state")
def api_state():
    return jsonify(currency=get_state("currency"), mission=int(get_state("mission")), base_capacity=BASE_CAPACITY)


@app.get("/api/items")
def api_items():
    db = get_db()
    char = find("characters", to_int(request.args.get("character_id")))
    out = []
    for it in db.execute("SELECT * FROM items ORDER BY sort, id"):
        d = dict(it)
        d["in_stock"] = bool(d["in_stock"])
        if char:
            d["reason"] = check_purchase(db, char, it)
            d["owned"] = owned_qty(db, char["id"], it["id"])
        out.append(d)
    return jsonify(out)


@app.get("/api/characters")
def api_characters():
    db = get_db()
    return jsonify([character_json(db, c) for c in db.execute("SELECT * FROM characters ORDER BY name")])


@app.post("/api/buy")
def api_buy():
    db, d = get_db(), body()
    char, item = find("characters", to_int(d.get("character_id"))), find("items", to_int(d.get("item_id")))
    if not char:
        return err("Choose a character first.")
    if not item:
        return err("That item doesn't exist.", 404)
    reason = check_purchase(db, char, item)
    if reason:
        return err(f"Can't buy {item['name']}: {reason}.", 409)
    db.execute("UPDATE characters SET money = money - ? WHERE id=?", (item["price"], char["id"]))
    add_to_inventory(db, char["id"], item["id"])
    db.execute("""INSERT INTO purchases (character_id, item_id, mission, qty) VALUES (?,?,?,1)
                  ON CONFLICT(character_id, item_id, mission) DO UPDATE SET qty = qty + 1""",
               (char["id"], item["id"], int(get_state("mission"))))
    log_event(f"{char['name']} bought {item['name']} for {item['price']}.")
    db.commit()
    return ok(f"{char['name']} bought {item['name']}.")


@app.post("/api/use")
def api_use():
    db, d = get_db(), body()
    cid, iid = to_int(d.get("character_id")), to_int(d.get("item_id"))
    char, item = find("characters", cid), find("items", iid)
    if not char or not item or item["category"] != "consumable" or owned_qty(db, cid, iid) < 1:
        return err("Nothing to use.", 409)
    remove_from_inventory(db, cid, iid)
    log_event(f"{char['name']} used {item['name']}.")
    db.commit()
    return ok(f"{char['name']} used {item['name']}.")


# ---------------------------------------------------------------- GM API
@app.post("/api/gm/login")
def gm_login():
    if hmac.compare_digest(str(body().get("password", "")), GM_PASSWORD):
        return jsonify(token=serializer.dumps("gm"))
    return err("Wrong password.", 401)


@app.get("/api/gm/log")
@gm_required
def gm_log():
    rows = get_db().execute("SELECT * FROM log ORDER BY id DESC LIMIT 40").fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/gm/characters")
@gm_required
def gm_add_character():
    db, d = get_db(), body()
    name, money = str(d.get("name", "")).strip(), to_int(d.get("money"), 0)
    if not name:
        return err("Name required.")
    try:
        db.execute("INSERT INTO characters (name, money) VALUES (?,?)", (name, money))
    except sqlite3.IntegrityError:
        return err("A character with that name already exists.", 409)
    log_event(f"Character {name} created with {money}.")
    db.commit()
    return ok(f"Added {name}.")


@app.delete("/api/gm/characters/<int:cid>")
@gm_required
def gm_delete_character(cid):
    char = find("characters", cid)
    if not char:
        return err("Not found", 404)
    db = get_db()
    db.execute("DELETE FROM characters WHERE id=?", (cid,))
    log_event(f"Character {char['name']} deleted.")
    db.commit()
    return ok(f"Deleted {char['name']}.")


@app.post("/api/gm/characters/<int:cid>/money")
@gm_required
def gm_money(cid):
    db, d = get_db(), body()
    char, amount = find("characters", cid), to_int(d.get("amount"))
    if not char:
        return err("Not found", 404)
    if amount is None:
        return err("Enter a number.")
    if d.get("mode") == "set":
        db.execute("UPDATE characters SET money=? WHERE id=?", (amount, cid))
        log_event(f"GM set {char['name']}'s money to {amount}.")
    else:
        db.execute("UPDATE characters SET money = money + ? WHERE id=?", (amount, cid))
        log_event(f"GM adjusted {char['name']}'s money by {amount:+d}.")
    db.commit()
    return ok("Money updated.")


@app.post("/api/gm/characters/<int:cid>/grant")
@gm_required
def gm_grant(cid):
    db, d = get_db(), body()
    char, item = find("characters", cid), find("items", to_int(d.get("item_id")))
    if not char or not item:
        return err("Not found", 404)
    qty = max(1, to_int(d.get("qty"), 1))
    add_to_inventory(db, cid, item["id"], qty)
    log_event(f"GM gave {qty}x {item['name']} to {char['name']}.")
    db.commit()
    return ok(f"Gave {qty}x {item['name']} to {char['name']} (GM grants ignore limits).")


@app.post("/api/gm/characters/<int:cid>/remove")
@gm_required
def gm_remove(cid):
    db = get_db()
    char, item = find("characters", cid), find("items", to_int(body().get("item_id")))
    if not char or not item:
        return err("Not found", 404)
    remove_from_inventory(db, cid, item["id"])
    log_event(f"GM removed {item['name']} from {char['name']}.")
    db.commit()
    return ok(f"Removed one {item['name']}.")


@app.post("/api/gm/mission/new")
@gm_required
def gm_new_mission():
    n = int(get_state("mission")) + 1
    set_state("mission", n)
    log_event(f"Mission {n} started. Per-mission purchase limits reset.")
    get_db().commit()
    return ok(f"Mission {n} started.")


@app.post("/api/gm/currency")
@gm_required
def gm_currency():
    name = str(body().get("currency", "")).strip()[:20]
    if not name:
        return err("Currency name required.")
    set_state("currency", name)
    get_db().commit()
    return ok("Currency renamed.")


def item_fields(d):
    return dict(
        name=str(d.get("name", "")).strip() or "Unnamed item",
        price=max(0, to_int(d.get("price"), 0)),
        description=str(d.get("description", "")).strip(),
        category="equipment" if d.get("category") == "equipment" else "consumable",
        inventory_bonus=to_int(d.get("inventory_bonus"), 0),
        per_mission_limit=to_int(d.get("per_mission_limit")),
        max_owned=to_int(d.get("max_owned")),
        in_stock=1 if d.get("in_stock", True) else 0,
    )


@app.post("/api/gm/items")
@gm_required
def gm_item_create():
    db, f = get_db(), item_fields(body())
    f["sort"] = db.execute("SELECT COALESCE(MAX(sort), 0) + 1 FROM items").fetchone()[0]
    db.execute("""INSERT INTO items (name, price, description, category, inventory_bonus,
                  per_mission_limit, max_owned, in_stock, sort)
                  VALUES (:name,:price,:description,:category,:inventory_bonus,
                  :per_mission_limit,:max_owned,:in_stock,:sort)""", f)
    log_event(f"Item {f['name']} added to the shop.")
    db.commit()
    return ok(f"Added {f['name']}.")


@app.put("/api/gm/items/<int:iid>")
@gm_required
def gm_item_update(iid):
    db = get_db()
    if not find("items", iid):
        return err("Not found", 404)
    f = item_fields(body())
    db.execute("""UPDATE items SET name=:name, price=:price, description=:description, category=:category,
                  inventory_bonus=:inventory_bonus, per_mission_limit=:per_mission_limit,
                  max_owned=:max_owned, in_stock=:in_stock WHERE id=:id""", {**f, "id": iid})
    log_event(f"Item {f['name']} edited.")
    db.commit()
    return ok(f"Saved {f['name']}.")


@app.post("/api/gm/items/<int:iid>/toggle")
@gm_required
def gm_item_toggle(iid):
    db = get_db()
    item = find("items", iid)
    if not item:
        return err("Not found", 404)
    db.execute("UPDATE items SET in_stock = 1 - in_stock WHERE id=?", (iid,))
    db.commit()
    return ok(f"{item['name']} is now {'sold out' if item['in_stock'] else 'in stock'}.")


@app.delete("/api/gm/items/<int:iid>")
@gm_required
def gm_item_delete(iid):
    db = get_db()
    item = find("items", iid)
    if not item:
        return err("Not found", 404)
    db.execute("DELETE FROM items WHERE id=?", (iid,))
    log_event(f"Item {item['name']} deleted from the shop.")
    db.commit()
    return ok(f"Deleted {item['name']}.")


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

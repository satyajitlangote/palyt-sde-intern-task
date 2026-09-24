"""Throwaway live smoke test: drives the running Flask server over HTTP.

Complements test_logic.py by verifying the wiring (routes, persistence,
status codes) and the same hand-computed numbers end to end. Run withthe server already up:  .venv/Scripts/python smoke_test.py

Expects PRISTINE state: every expected number is anchored to the shipped
stock.json. A second run without restoring mutates state and cascades
failures (proven the hard way).
"""

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:5000"
results = []


def call(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def check(label, cond, detail=""):
    results.append(cond)
    print(("PASS " if cond else "FAIL ") + label + (f"  [{detail}]" if detail else ""))


def find(items, id_):
    return next(i for i in items if i["id"] == id_)


# --- baseline snapshot ------------------------------------------------------
s, snap = call("GET", "/api/stock")
cashews = find(snap["stock"], "cashews")
tikka = find(snap["menu"], "paneer-tikka")
korma = find(snap["menu"], "chicken-korma")
check("GET /api/stock -> 200", s == 200)
check("20 ingredients, 8 dishes", len(snap["stock"]) == 20 and len(snap["menu"]) == 8,
      f"{len(snap['stock'])}/{len(snap['menu'])}")
check("cashews starts at 1.5 kg", cashews["qty"] == 1.5, str(cashews["qty"]))
check("tikka unavailable under as-given rule", tikka["available"] is False)
check("tikka blocker list is exactly Fresh Coriander", tikka["below_par"] == ["Fresh Coriander"])
check("tikka still has 2 makeable portions (critique surfaced)", tikka["min_portions"] == 2,
      str(tikka["min_portions"]))
# binding constraint for korma is cream: 2000/100 = 20
check("korma shows 20 makeable portions", korma["min_portions"] == 20, str(korma["min_portions"]))
check("every stock row carries used_by", all("used_by" in i for i in snap["stock"]))

# --- order: deducts across the kg/g scale, menu recomputes -------------------
s, snap = call("POST", "/api/orders", {"dish_id": "chicken-korma"})
cashews = find(snap["stock"], "cashews")
check("POST /api/orders (korma) -> 200", s == 200)
check("cashews 1.5 -> 1.44 kg (kg stock vs g recipe)", abs(cashews["qty"] - 1.44) < 1e-9,
      str(cashews["qty"]))
check("cream 2000 -> 1900 ml", find(snap["stock"], "cream")["qty"] == 1900)
check("chicken 4.5 -> 4.3 kg", find(snap["stock"], "chicken")["qty"] == 4.3)
check("garam masala 250 -> 245 g", find(snap["stock"], "garam-masala")["qty"] == 245)
korma = find(snap["menu"], "chicken-korma")
check("korma makeable portions 20 -> 19 in same response", korma["min_portions"] == 19,
      str(korma["min_portions"]))

# --- par must not block the kitchen: tikka is menu-blocked but orderable ----
s, snap = call("POST", "/api/orders", {"dish_id": "paneer-tikka"})
check("order par-blocked tikka -> 200 (API allows, menu hides)", s == 200)
coriander = find(call("GET", "/api/stock")[1]["stock"], "fresh-coriander")
check("coriander 80 -> 50 g after tikka", coriander["qty"] == 50, str(coriander["qty"]))
tikka = find(snap["menu"], "paneer-tikka")
check("tikka now shows 1 makeable portion", tikka["min_portions"] == 1, str(tikka["min_portions"]))

# --- restock via PUT flips the dish back on the menu ------------------------
# coriander is stocked in g, so the payload is in g too (API takes qty in
# the row's own unit - my first draft passed 0.3 as if kg and FAILED here,
# which is exactly the 1000x class of mistake the app itself refuses).
s, snap = call("PUT", "/api/stock/fresh-coriander", {"qty": 300, "par": 100})
tikka = find(snap["menu"], "paneer-tikka")
check("PUT restock coriander to 300 g -> 200", s == 200)
check("tikka flips AVAILABLE with no reload", tikka["available"] is True)

# --- boundary: qty exactly at par is available (strict below) ---------------
s, snap = call("PUT", "/api/stock/fresh-coriander", {"qty": 100, "par": 100})
tikka = find(snap["menu"], "paneer-tikka")
check("qty == par (100 g / 100 g) still available", tikka["available"] is True)
check("...and covers 3 portions (100/30)", tikka["min_portions"] == 3, str(tikka["min_portions"]))

# --- the rule's pathological corner, demonstrated live ----------------------
# par == qty == 10 g: NOT below par, so the as-given rule says AVAILABLE -
# yet 10 g cannot make one 30 g portion. The menu shows an enabled Order
# button and the order 409s on click. Faithful implementation of a flawed
# rule; strongest evidence for the write-up critique.
s, snap = call("PUT", "/api/stock/fresh-coriander", {"qty": 10, "par": 10})
tikka = find(snap["menu"], "paneer-tikka")
check("rule pathology: qty==par==10g is 'available'...", tikka["available"] is True)
check("...but makes 0 portions", tikka["min_portions"] == 0, str(tikka["min_portions"]))
s, body = call("POST", "/api/orders", {"dish_id": "paneer-tikka"})
check("...and the order 409s on click", s == 409, str(s))
s, snap = call("PUT", "/api/stock/fresh-coriander", {"qty": 80, "par": 100})  # restore

# --- all-or-nothing deduction ------------------------------------------------
s, _ = call("PUT", "/api/stock/chicken", {"qty": 0.1, "par": 2})  # korma needs 0.2
s, body = call("POST", "/api/orders", {"dish_id": "chicken-korma"})
cashews = find(call("GET", "/api/stock")[1]["stock"], "cashews")
check("short korma order -> 409", s == 409, str(s))
check("all-or-nothing: cashews untouched at 1.44 kg", abs(cashews["qty"] - 1.44) < 1e-9,
      str(cashews["qty"]))
call("PUT", "/api/stock/chicken", {"qty": 4.3, "par": 2})  # restore for later checks

# --- add / validation --------------------------------------------------------
s, _ = call("POST", "/api/stock", {"name": "Tofu", "qty": 1, "unit": "g", "par": 0})
check("add with par 0 -> 400", s == 400, str(s))
s, _ = call("POST", "/api/stock", {"name": "Tofu", "qty": "abc", "unit": "g", "par": 1})
check("add with non-numeric qty -> 400", s == 400, str(s))
s, _ = call("POST", "/api/stock", {"name": "Tofu", "qty": -1, "unit": "g", "par": 1})
check("add with negative qty -> 400", s == 400, str(s))
s, _ = call("POST", "/api/stock", {"name": "   ", "qty": 1, "unit": "g", "par": 1})
check("add with blank name -> 400", s == 400, str(s))
s, _ = call("POST", "/api/stock", {"name": "CASHEWS", "qty": 1, "unit": "g", "par": 1})
check("add with duplicate name (case-insensitive) -> 400", s == 400, str(s))
s, _ = call("POST", "/api/stock")
check("add with no JSON body -> 400", s == 400, str(s))
s, snap = call("POST", "/api/stock", {"name": "Smoked Paprika", "qty": 200, "unit": "g", "par": 50})
check("valid add -> 201 and appears in stock", s == 201 and
      any(i["name"] == "Smoked Paprika" for i in snap["stock"]), str(s))

# --- delete: guard blocks used ingredients, unused delete freely -------------
s, body = call("DELETE", "/api/stock/cashews")
check("delete cashews -> 409", s == 409, str(s))
check("409 names both using dishes",
      "Chicken Korma" in body.get("error", "") and "Paneer Butter Masala" in body.get("error", ""),
      body.get("error", "")[:80])
s, snap = call("DELETE", "/api/stock/bay-leaves")
check("delete unused bay-leaves -> 200 and gone", s == 200 and
      not any(i["id"] == "bay-leaves" for i in snap["stock"]), str(s))
s, snap = call("DELETE", "/api/stock/smoked-paprika")
check("delete fresh add -> 200 and gone", s == 200 and
      not any(i["id"] == "smoked-paprika" for i in snap["stock"]), str(s))

# --- unknown ids -------------------------------------------------------------
s, _ = call("POST", "/api/orders", {"dish_id": "unicorn"})
check("order unknown dish -> 404", s == 404, str(s))
s, _ = call("PUT", "/api/stock/unicorn", {"qty": 1, "par": 1})
check("edit unknown ingredient -> 404", s == 404, str(s))

print()
print(f"{sum(results)}/{len(results)} checks passed")
raise SystemExit(0 if all(results) else 1)

"""Flask app: serves the static UI and a small JSON API.

stock.json is the single source of truth for ingredients; every mutation
writes it back to disk, so a refresh or a second tab sees the same numbers.
recipes.json is read-only.

Validation split (argued in the PR write-up): the API enforces what is
physically possible (deduction fails only when stock is genuinely short),
while the UI enforces the brief's par-based menu rule. A staff-facing till
could order a "par-blocked" dish; the customer menu does not offer it.
"""

import json
import re
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

import logic

HERE = Path(__file__).parent
STOCK_FILE = HERE / "stock.json"
RECIPES_FILE = HERE / "recipes.json"
STATIC_FILES = {"index.html", "app.js", "style.css"}

UNITS = ("g", "kg", "ml", "l")

app = Flask(__name__, static_folder=None)


# --- persistence helpers ----------------------------------------------------


def load_stock():
    return json.loads(STOCK_FILE.read_text(encoding="utf-8"))


def save_stock(stock):
    STOCK_FILE.write_text(
        json.dumps(stock, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def load_recipes():
    return json.loads(RECIPES_FILE.read_text(encoding="utf-8"))


def snapshot():
    """Everything the UI needs after any mutation: stock rows + fresh menu."""
    stock = load_stock()
    recipes = load_recipes()
    by_id = {ing["id"]: ing for ing in stock}
    for ing in stock:
        ing["used_by"] = logic.used_by(ing["id"], recipes)
    return jsonify({"stock": stock, "menu": logic.menu(recipes, by_id)})


# --- validation --------------------------------------------------------------


def parse_number(value, field, errors, minimum, allow_equal):
    """Return value as float, or record an error explaining why it's invalid."""
    if value is None or (isinstance(value, str) and not value.strip()):
        errors.append(f"{field} is required")
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        errors.append(f"{field} must be a number")
        return None
    if number != number or number in (float("inf"), float("-inf")):
        errors.append(f"{field} must be a finite number")
        return None
    ok = number >= minimum if allow_equal else number > minimum
    if not ok:
        bound = f"{minimum} or more" if allow_equal else f"greater than {minimum}"
        errors.append(f"{field} must be {bound}")
        return None
    return number


def validate_ingredient(data, current_id=None):
    """Validate a stock payload. Returns (errors, cleaned fields).

    Rules and reasoning (stated in the PR write-up):
    - name: required, non-blank, unique case-insensitively (duplicate names
      make search and the used-by guard ambiguous).
    - unit: one of g/kg/ml/l. Anything else cannot be converted safely.
    - qty: >= 0 allowed - zero stock is a real kitchen state (ran out).
    - par: > 0 required. Par 0 would silently make a dish "available" at
      zero stock under the below-par rule, so it is treated as invalid.
    - Being below par at add/edit time is fine (that is the reorder state).
    """
    errors = []

    name = (data.get("name") or "").strip()
    if not name:
        errors.append("name is required")
    elif any(
        ing["name"].strip().lower() == name.lower()
        for ing in load_stock()
        if ing["id"] != current_id
    ):
        errors.append(f"an ingredient named {name!r} already exists")

    unit = data.get("unit")
    if unit not in UNITS:
        errors.append(f"unit must be one of: {', '.join(UNITS)}")

    qty = parse_number(data.get("qty"), "qty", errors, minimum=0, allow_equal=True)
    par = parse_number(data.get("par"), "par", errors, minimum=0, allow_equal=False)

    return errors, {"name": name, "unit": unit, "qty": qty, "par": par}


# --- static files ------------------------------------------------------------


@app.get("/")
def index():
    return send_from_directory(HERE, "index.html")


@app.get("/<path:filename>")
def static_files(filename):
    if filename not in STATIC_FILES:
        abort(404)
    return send_from_directory(HERE, filename)


# --- API ----------------------------------------------------------------------


@app.get("/api/stock")
def get_stock():
    return snapshot()


@app.post("/api/stock")
def add_ingredient():
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "expected a JSON body"}), 400

    errors, fields = validate_ingredient(data)
    if errors:
        return jsonify({"error": "; ".join(errors)}), 400

    slug = re.sub(r"[^a-z0-9]+", "-", fields["name"].lower()).strip("-") or "ingredient"
    stock = load_stock()
    taken = {ing["id"] for ing in stock}
    ingredient_id, suffix = slug, 2
    while ingredient_id in taken:
        ingredient_id = f"{slug}-{suffix}"
        suffix += 1

    stock.append({"id": ingredient_id, **fields})
    save_stock(stock)
    return snapshot(), 201


@app.put("/api/stock/<ingredient_id>")
def edit_ingredient(ingredient_id):
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "expected a JSON body"}), 400

    stock = load_stock()
    ingredient = next((ing for ing in stock if ing["id"] == ingredient_id), None)
    if ingredient is None:
        return jsonify({"error": f"unknown ingredient: {ingredient_id}"}), 404

    # Editing covers qty and par only - name/unit changes would ripple into
    # recipe data semantics and are out of scope (stated in the write-up).
    errors = []
    qty = parse_number(data.get("qty"), "qty", errors, minimum=0, allow_equal=True)
    par = parse_number(data.get("par"), "par", errors, minimum=0, allow_equal=False)
    if errors:
        return jsonify({"error": "; ".join(errors)}), 400

    ingredient["qty"] = qty
    ingredient["par"] = par
    save_stock(stock)
    return snapshot()


@app.delete("/api/stock/<ingredient_id>")
def delete_ingredient(ingredient_id):
    stock = load_stock()
    recipes = load_recipes()
    ingredient = next((ing for ing in stock if ing["id"] == ingredient_id), None)
    if ingredient is None:
        return jsonify({"error": f"unknown ingredient: {ingredient_id}"}), 404

    used_in = logic.used_by(ingredient_id, recipes)
    if used_in:
        # Block with the evidence: the caller can judge Cashews (2 dishes)
        # vs Bay Leaves (none) themselves. Argued in the PR write-up.
        return (
            jsonify(
                {
                    "error": (
                        f"{ingredient['name']} is used by {len(used_in)} "
                        f"dish(es): {', '.join(used_in)}. Remove it from those "
                        "recipes first."
                    )
                }
            ),
            409,
        )

    save_stock([ing for ing in stock if ing["id"] != ingredient_id])
    return snapshot()


@app.get("/api/menu")
def get_menu():
    recipes = load_recipes()
    by_id = {ing["id"]: ing for ing in load_stock()}
    return jsonify({"menu": logic.menu(recipes, by_id)})


@app.post("/api/orders")
def place_order():
    data = request.get_json(silent=True)
    dish_id = (data or {}).get("dish_id")
    if not dish_id:
        return jsonify({"error": "dish_id is required"}), 400

    recipes = load_recipes()
    recipe = next((r for r in recipes if r["id"] == dish_id), None)
    if recipe is None:
        return jsonify({"error": f"unknown dish: {dish_id}"}), 404

    stock = load_stock()
    by_id = {ing["id"]: ing for ing in stock}
    try:
        logic.deduct_for_dish(by_id, recipe)
    except ValueError as exc:
        # Physically short (or a recipe ingredient was deleted): all-or-
        # nothing, nothing was deducted.
        return jsonify({"error": str(exc)}), 409

    save_stock([by_id[ing["id"]] for ing in stock])
    return snapshot()


# Fail loudly at boot if the shipped data has unit/dimension problems.
logic.validate_data(load_recipes(), {ing["id"]: ing for ing in load_stock()})

if __name__ == "__main__":
    app.run(debug=True)

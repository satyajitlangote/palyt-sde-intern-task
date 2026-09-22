"""Domain logic for stock, recipes, and the order loop.

All math happens in base units: grams for mass, millilitres for volume.
Conversion happens in exactly one place (to_base_units), so the stock-in-kg
vs recipe-in-g scale mismatch can only be gotten wrong in one spot.

Money is untouched everywhere: prices are stored and displayed as-is.
"""

import math

MASS_UNITS = ("g", "kg")
VOLUME_UNITS = ("ml", "l")
BASE_FACTOR = {"g": 1.0, "kg": 1000.0, "ml": 1.0, "l": 1000.0}

# Floats make e.g. 0.3 * 1000 == 300.00000000000006; this tolerance stops
# that noise from dropping a portion in portions_available().
FLOAT_TOLERANCE = 1e-9


def unit_family(unit):
    """'mass' for g/kg, 'volume' for ml/l, error for anything else."""
    if unit in MASS_UNITS:
        return "mass"
    if unit in VOLUME_UNITS:
        return "volume"
    raise ValueError(f"unknown unit: {unit!r}")


def to_base_units(qty, unit):
    """Convert a quantity to base units (g for mass, ml for volume).

    This is the only place a unit factor appears. Stock quantities, par
    levels, and recipe quantities all go through here before any math.
    """
    factor = BASE_FACTOR.get(unit)
    if factor is None:
        raise ValueError(f"unknown unit: {unit!r}")
    return qty * factor


def portions_available(stock_qty, stock_unit, recipe_qty, recipe_unit):
    """How many whole portions this ingredient's stock can cover."""
    if unit_family(stock_unit) != unit_family(recipe_unit):
        # e.g. stock in kg vs recipe line in ml: a silent 1000x-style bug.
        raise ValueError(
            f"unit dimension mismatch: stock in {stock_unit!r} vs recipe in {recipe_unit!r}"
        )
    have = to_base_units(stock_qty, stock_unit)
    need = to_base_units(recipe_qty, recipe_unit)
    if need <= 0:
        raise ValueError("recipe quantity must be positive")
    return int(math.floor(have / need + FLOAT_TOLERANCE))


def dish_status(recipe, stock_by_id):
    """Apply the brief's availability rule and expose what it hides.

    Rule as given: a dish is unavailable if ANY ingredient is strictly below
    its par level (qty == par still counts as available - boundary stated in
    the write-up). Caveat (argued in the PR write-up): par is a reorder
    threshold, not a can-this-be-made threshold, so this blocks dishes that
    still have portions left. min_portions is computed alongside so the UI
    can show what the rule is throwing away.
    """
    min_portions = None
    below_par = []
    for line in recipe["ingredients"]:
        ing = stock_by_id.get(line["id"])
        if ing is None:
            # Ingredient was deleted (or data is broken): can never cook.
            below_par.append(f"{line['id']} (missing)")
            min_portions = 0
            continue
        have = to_base_units(ing["qty"], ing["unit"])
        par = to_base_units(ing["par"], ing["unit"])
        if have < par:
            below_par.append(ing["name"])
        # Pass raw quantities; portions_available does the conversion once.
        portions = portions_available(ing["qty"], ing["unit"], line["qty"], line["unit"])
        min_portions = portions if min_portions is None else min(min_portions, portions)
    return {
        "available": not below_par,
        "below_par": below_par,
        "min_portions": min_portions,
    }


def menu(recipes, stock_by_id):
    """The menu the customer sees, in recipe order."""
    items = []
    for recipe in recipes:
        status = dish_status(recipe, stock_by_id)
        items.append(
            {
                "id": recipe["id"],
                "name": recipe["name"],
                "price": recipe["price"],
                "available": status["available"],
                "below_par": status["below_par"],
                "min_portions": status["min_portions"],
            }
        )
    return items


def deduct_for_dish(stock_by_id, recipe):
    """Deduct one portion of every ingredient for a dish order.

    All-or-nothing: if any ingredient is short, nothing is deducted and a
    ValueError is raised naming the shortfall. The caller owns persistence.
    """
    planned = []
    for line in recipe["ingredients"]:
        ing = stock_by_id.get(line["id"])
        if ing is None:
            raise ValueError(f"ingredient missing from stock: {line['id']}")
        need = to_base_units(line["qty"], line["unit"])
        have = to_base_units(ing["qty"], ing["unit"])
        if have < need:
            raise ValueError(f"not enough {ing['name']}: need {need:g} base units, have {have:g}")
        planned.append((ing, need))
    # Only mutate after every line has passed the check above.
    for ing, need in planned:
        remaining_base = to_base_units(ing["qty"], ing["unit"]) - need
        ing["qty"] = round(remaining_base / BASE_FACTOR[ing["unit"]], 6)


def used_by(ingredient_id, recipes):
    """Names of dishes whose recipes use this ingredient."""
    return [
        recipe["name"]
        for recipe in recipes
        if any(line["id"] == ingredient_id for line in recipe["ingredients"])
    ]


def validate_data(recipes, stock_by_id):
    """Fail loudly on data problems, especially unit-scale mismatches.

    Catches the 1000x bug class at load time: a recipe line whose unit is
    a different *dimension* than the stock unit (chicken in ml, cream in g)
    is almost certainly a data error, not a conversion to make.
    """
    seen_ingredients = set()
    for ing in stock_by_id.values():
        unit_family(ing["unit"])
        if ing["id"] in seen_ingredients:
            raise ValueError(f"duplicate ingredient id: {ing['id']}")
        seen_ingredients.add(ing["id"])

    seen_dishes = set()
    for recipe in recipes:
        if recipe["id"] in seen_dishes:
            raise ValueError(f"duplicate dish id: {recipe['id']}")
        seen_dishes.add(recipe["id"])
        for line in recipe["ingredients"]:
            ing = stock_by_id.get(line["id"])
            if ing is None:
                raise ValueError(f"dish {recipe['name']!r} uses unknown ingredient {line['id']!r}")
            if unit_family(line["unit"]) != unit_family(ing["unit"]):
                raise ValueError(
                    f"dish {recipe['name']!r}: unit mismatch for {ing['name']} - "
                    f"stocked in {ing['unit']!r}, recipe line uses {line['unit']!r}"
                )
            if to_base_units(line["qty"], line["unit"]) <= 0:
                raise ValueError(f"dish {recipe['name']!r}: recipe quantity must be positive")

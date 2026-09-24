"""Tests for the logic that decides availability and deduction only.

Every expected number below was traced by hand against stock.json and
recipes.json (see PR description for the working). If the logic is wrong,
these are designed to fail, not to pass vacuously.
"""

import json
import re
from pathlib import Path

import pytest

import logic

HERE = Path(__file__).parent


@pytest.fixture
def stock_by_id():
    stock = json.loads((HERE / "stock.json").read_text(encoding="utf-8"))
    return {ing["id"]: ing for ing in stock}


@pytest.fixture
def recipes():
    return json.loads((HERE / "recipes.json").read_text(encoding="utf-8"))


# --- unit conversion ------------------------------------------------------


def test_kg_stock_against_g_recipe_crosses_the_scale():
    # 1.5 kg cashews = 1500 g; korma needs 60 g/portion -> 25 portions.
    # Off-by-1000 anywhere and this returns 0 instead of 25.
    assert logic.portions_available(1.5, "kg", 60, "g") == 25


def test_same_scale_conversion():
    # 750 g butter vs 40 g/portion -> 18 portions.
    assert logic.portions_available(750, "g", 40, "g") == 18


def test_litres_convert_to_millilitres():
    assert logic.to_base_units(2, "l") == 2000.0
    assert logic.portions_available(2, "l", 500, "ml") == 4


def test_cross_family_comparison_is_rejected():
    # g vs ml is a data error, not a conversion to make.
    with pytest.raises(ValueError):
        logic.portions_available(1, "kg", 100, "ml")


def test_unknown_unit_is_rejected():
    with pytest.raises(ValueError):
        logic.to_base_units(5, "lb")


# --- conversion probes: pinning the absolute scale --------------------------
# The scenario tests below assert RELATIVE correctness: they would still pass
# if every unit factor were wrong by the same amount, because the hand
# arithmetic in their comments would be wrong the same way. These probes pin
# the ABSOLUTE meaning of the base units, so a uniform factor slip fails.


def test_kilo_prefix_is_a_definition_not_a_derivation():
    # SI definition, not a derivation: kilo means 1000, for both mass (kg->g)
    # and volume (l->ml). If this fails, a unit factor was changed. Fix the
    # factor, not this test.
    assert logic.to_base_units(1, "kg") == 1000.0
    assert logic.to_base_units(1, "l") == 1000.0
    assert logic.to_base_units(1, "g") == 1.0
    assert logic.to_base_units(1, "ml") == 1.0


def test_factor_bracket_rules_out_magnitude_slips():
    # 1 kg must cover a 999 g portion but not a 1001 g one, so the kg->g
    # factor must land in [999, 1001). Every plausible decade slip -- 1, 10,
    # 100, 10000, or a swapped table row -- falls outside that bracket.
    assert logic.portions_available(1, "kg", 999, "g") == 1
    assert logic.portions_available(1, "kg", 1001, "g") == 0


def test_kilo_factors_cannot_drift_apart():
    # kg and l share the kilo prefix. If one table entry is mistyped, this
    # fails even when every mass-based (or volume-based) test stays green.
    assert logic.to_base_units(1, "kg") == logic.to_base_units(1, "l")


def test_conversion_table_source_is_the_shipped_definition():
    # Strongest tripwire: reads the factor table straight out of logic.py, so
    # the definitions cannot drift even if every expectation above were
    # 'updated' to match a wrong implementation. If this fails, someone
    # changed a unit factor: restore 1000, do not edit this test.
    source = Path(logic.__file__).read_text(encoding="utf-8")
    match = re.search(r"BASE_FACTOR\s*=\s*\{(.*?)\}", source, re.DOTALL)
    assert match, "BASE_FACTOR table not found in logic.py"
    table = dict(
        (name, float(value))
        for name, value in re.findall(r'"?(\w+)"?\s*:\s*([\d.]+)', match.group(1))
    )
    assert table == {"g": 1.0, "kg": 1000.0, "ml": 1.0, "l": 1000.0}


# --- availability (the as-given rule) -------------------------------------


def test_dish_blocked_by_any_ingredient_below_par(stock_by_id, recipes):
    # Paneer Tikka: coriander 80 g is below its 100 g par, everything else
    # is fine -> unavailable under the as-given rule.
    korma = next(r for r in recipes if r["id"] == "paneer-tikka")
    status = logic.dish_status(korma, stock_by_id)
    assert status["available"] is False
    assert status["below_par"] == ["Fresh Coriander"]


def test_blocked_dish_still_has_makeable_portions(stock_by_id, recipes):
    # The critique case: coriander 80 g covers 2 portions of tikka (30 g each),
    # so stock is NOT the real blocker - par is. min_portions exposes it.
    tikka = next(r for r in recipes if r["id"] == "paneer-tikka")
    status = logic.dish_status(tikka, stock_by_id)
    assert status["below_par"] == ["Fresh Coriander"]
    assert status["min_portions"] == 2


def test_available_dish_reports_no_blockers(stock_by_id, recipes):
    biryani = next(r for r in recipes if r["id"] == "chicken-biryani")
    status = logic.dish_status(biryani, stock_by_id)
    # Binding constraint is chicken: 3.75 kg = 3750 g / 250 g-per-portion = 15.
    assert status == {
        "available": True,
        "below_par": [],
        # rice 9.4 kg = 9400 g -> 47, chicken 3.75 kg = 3750 g -> 15,
        # onions 5.7 kg = 5700 g -> 57, oil 4850 ml -> 97
        "min_portions": 15,
    }


def test_boundary_par_is_available(stock_by_id, recipes):
    # Boundary call: "below par" is strict (qty == par still available).
    # Rationale: the rule blocks on "reorder now" signal; hitting exactly par
    # is not yet below it. Stated in the write-up as a judgement call.
    stock_by_id["fresh-coriander"]["qty"] = 100  # exactly par
    tikka = next(r for r in recipes if r["id"] == "paneer-tikka")
    status = logic.dish_status(tikka, stock_by_id)
    assert status["available"] is True


def test_missing_ingredient_makes_dish_unavailable(stock_by_id, recipes):
    tikka = next(r for r in recipes if r["id"] == "paneer-tikka")
    del stock_by_id["fresh-coriander"]
    status = logic.dish_status(tikka, stock_by_id)
    assert status["available"] is False
    assert status["min_portions"] == 0


def test_menu_lists_every_dish_in_recipe_order(stock_by_id, recipes):
    items = logic.menu(recipes, stock_by_id)
    assert [i["id"] for i in items] == [r["id"] for r in recipes]
    assert {i["name"] for i in items} == {r["name"] for r in recipes}


# --- deduction -------------------------------------------------------------


def test_order_deducts_across_the_unit_scale(stock_by_id, recipes):
    # Korma: cashews 1.5 kg -> 1.44 kg, cream 2000 ml -> 1900 ml,
    # chicken 3.75 kg -> 3.5 kg, garam masala 250 g -> 245 g.
    korma = next(r for r in recipes if r["id"] == "chicken-korma")
    logic.deduct_for_dish(stock_by_id, korma)
    assert stock_by_id["cashews"]["qty"] == pytest.approx(1.44)
    assert stock_by_id["cream"]["qty"] == pytest.approx(1900)
    assert stock_by_id["chicken"]["qty"] == pytest.approx(3.55)
    assert stock_by_id["garam-masala"]["qty"] == pytest.approx(245)


def test_deduction_is_all_or_nothing(stock_by_id, recipes):
    # Korma needs 60 g cashews + 200 g chicken. Drain chicken below its
    # requirement, then order korma: nothing at all may move.
    korma = next(r for r in recipes if r["id"] == "chicken-korma")
    stock_by_id["chicken"]["qty"] = 0.1  # kg, need 0.2 kg
    with pytest.raises(ValueError):
        logic.deduct_for_dish(stock_by_id, korma)
    assert stock_by_id["cashews"]["qty"] == pytest.approx(1.5)  # untouched
    assert stock_by_id["cream"]["qty"] == pytest.approx(2000)


def test_deduction_allowed_when_below_par(stock_by_id, recipes):
    # Par must NOT block cooking: korma stays orderable even with cashews
    # below par (1.1 kg < 1.2 kg par), because 1100 g still covers 60 g portions.
    korma = next(r for r in recipes if r["id"] == "chicken-korma")
    stock_by_id["cashews"]["qty"] = 1.1
    stock_by_id["cashews"]["par"] = 1.2
    logic.deduct_for_dish(stock_by_id, korma)
    assert stock_by_id["cashews"]["qty"] == pytest.approx(1.04)


def test_sequential_orders_land_on_expected_remainder(stock_by_id, recipes):
    # Order biryani, then biryani, then korma:
    #   rice:    9.4 kg - 2*0.2 kg               = 9.0 kg
    #   chicken: 3.75 - 2*0.25 - 0.2             = 3.05 kg
    #   onions:  5.7  - 2*0.1                    = 5.5 kg
    #   oil:     4850 ml - 2*50 ml               = 4750 ml  (oil is stocked in ml)
    #   cashews: 1.5  - 0.06                     = 1.44 kg
    #   cream:   2000 ml - 100 ml                = 1900 ml
    #   masala:  250  - 5                        = 245 g
    # (biryani deducts chicken 250 g/portion; korma deducts chicken 200 g/portion)
    biryani = next(r for r in recipes if r["id"] == "chicken-biryani")
    korma = next(r for r in recipes if r["id"] == "chicken-korma")
    logic.deduct_for_dish(stock_by_id, biryani)
    logic.deduct_for_dish(stock_by_id, biryani)
    logic.deduct_for_dish(stock_by_id, korma)
    assert stock_by_id["basmati-rice"]["qty"] == pytest.approx(9.0)
    assert stock_by_id["chicken"]["qty"] == pytest.approx(3.05)
    assert stock_by_id["onions"]["qty"] == pytest.approx(5.5)
    assert stock_by_id["oil"]["qty"] == pytest.approx(4750)
    assert stock_by_id["cashews"]["qty"] == pytest.approx(1.44)
    assert stock_by_id["cream"]["qty"] == pytest.approx(1900)
    assert stock_by_id["garam-masala"]["qty"] == pytest.approx(245)


def test_last_portion_can_be_ordered_but_next_cannot(stock_by_id, recipes):
    # Jeera rice: 8 g cumin/portion, 150 g cumin in stock -> 18 portions.
    # Drain cumin to exactly 16 g = 2 portions; both orders succeed, the
    # third (needing 8 g with 0 g left) fails all-or-nothing.
    jeera = next(r for r in recipes if r["id"] == "jeera-rice")
    stock_by_id["cumin"]["qty"] = 16
    logic.deduct_for_dish(stock_by_id, jeera)
    logic.deduct_for_dish(stock_by_id, jeera)
    assert stock_by_id["cumin"]["qty"] == pytest.approx(0)
    with pytest.raises(ValueError):
        logic.deduct_for_dish(stock_by_id, jeera)


def test_missing_ingredient_blocks_deduction(stock_by_id, recipes):
    jeera = next(r for r in recipes if r["id"] == "jeera-rice")
    del stock_by_id["basmati-rice"]
    with pytest.raises(ValueError):
        logic.deduct_for_dish(stock_by_id, jeera)
    assert stock_by_id["cumin"]["qty"] == pytest.approx(150)  # untouched


# --- used_by (delete guard) ------------------------------------------------


def test_cashews_are_used_by_two_dishes(recipes):
    assert sorted(logic.used_by("cashews", recipes)) == [
        "Chicken Korma",
        "Paneer Butter Masala",
    ]


def test_bay_leaves_are_used_by_no_dish(recipes):
    assert logic.used_by("bay-leaves", recipes) == []


# --- data validation --------------------------------------------------------


def test_validate_data_accepts_the_shipped_files(recipes, stock_by_id):
    logic.validate_data(recipes, stock_by_id)  # must not raise


def test_validate_data_rejects_unit_dimension_mismatch(stock_by_id, recipes):
    recipes[0]["ingredients"][0]["unit"] = "ml"  # cashews in ml: nonsense
    with pytest.raises(ValueError, match="unit mismatch"):
        logic.validate_data(recipes, stock_by_id)


def test_validate_data_rejects_unknown_ingredient(stock_by_id, recipes):
    recipes[0]["ingredients"].append({"id": "unicorn", "qty": 1, "unit": "g"})
    with pytest.raises(ValueError, match="unknown ingredient"):
        logic.validate_data(recipes, stock_by_id)

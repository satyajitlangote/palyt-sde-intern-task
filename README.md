# Kitchen Stock + Menu Sync

Palyt SDE intern task: a stock manager, a customer menu that reflects live
stock, and the order flow connecting them. Flask + vanilla JS, no build step.
State is in-memory at request time and written back to `stock.json` on every
mutation, so a refresh (or a second tab) sees the same numbers.

## Setup & Run

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # macOS/Linux: .venv/bin/pip
.venv/Scripts/python -m pytest test_logic.py -q # the logic tests
.venv/Scripts/python app.py                     # serves http://127.0.0.1:5000
```

Open `index.html` in two browser tabs (or serve the directory), then on one
panel add/remove/restock ingredients — the customer menu on the other panel
recomputes live from the same `{stock, menu}` snapshot, no reload needed.

## Demo recording

`recordings/pytl.mp4` (optional) shows the live stock + menu flow. It is large
and intentionally left out of git; re-record with your screen-capture tool and
drop the file into `recordings/` if you want it versioned.

## Tests

- `pytest test_logic.py` — the canonical suite (26 tests). These are the
  ones hand-anchored to the shipped `stock.json`/`recipes.json`.

- `smoke_test.py` — a **throwaway** end-to-end smoke test that drives a live
  server with hardcoded numbers. Its expectations were written for an earlier
  stock baseline and are now out of sync with the shipped data, so it is not
  part of the canonical run. If you re-run it, anchor its expectations to the
  current `stock.json` (e.g. chicken 3.75 kg, korma binding = chicken at 18
  min-portions) or disable it.

## Layout

- `logic.py` — all domain rules: unit conversion, availability, deduction, used-by
- `test_logic.py` — tests for availability and deduction only, hand-verified numbers
- `app.py` — Flask API: stock CRUD, menu, orders, write-back to `stock.json`
- `index.html` / `app.js` / `style.css` — the two-panel UI

## Write-up (PR description draft)

### 1. Calls I made where the data/rules didn't settle it

- **The availability rule is wrong, and I implemented it anyway.** The brief
  blocks a dish if ANY ingredient is below par. Par is a reorder threshold, not
  a can-I-cook threshold: Paneer Tikka boots unavailable because Fresh
  Coriander (80 g) is below par (100 g) — yet the stock covers 2 full portions.
  I implemented the rule as given, but the menu prints "stock would still cover
  N portion(s)" on blocked dishes so the flaw is visible to a reviewer rather
  than argued away in a paragraph. The rule I'd defend: available = every
  ingredient covers at least one portion; below-par ingredients trigger a
  reorder flag instead of hiding dishes.
- **Boundary: "below par" is strict.** qty == par counts as available (you
  reorder when you drop below the threshold, not when you touch it). One test
  pins the boundary deliberately.
- **Deleting a used ingredient: blocked, with the evidence.** Cashews (2
  dishes) can't be deleted; the 409 response names the dishes. Bay Leaves
  (none) deletes freely. Rationale: cascading silently rewrites dish data;
  warn-then-allow creates recipes with ghost ingredients that then need a
  "missing ingredient" code path everywhere. Blocking is the smallest defensible
  rule, and the UI already shows the "used by" column so the guard surprises no one.
- **par 0 is invalid.** Under the below-par rule, par 0 would keep a dish
  "available" at literally zero stock. qty 0 is allowed (running out is real);
  par 0 is not.
- **Names are unique (case-insensitive).** Duplicate names make search results
  and the delete guard ambiguous. IDs are auto-slugged, never user-edited.
- **Editing covers qty and par only.** Renaming or re-uniting an ingredient
  would ripple into recipe semantics that recipes.json can't express, and it's
  out of scope.
- **Validation lives on both ends, server is authoritative.** The server
  re-checks everything; the client mirrors the rules only to fail fast with the
  reasons inline.

### 2. How I know the numbers are right

Every expected value in `test_logic.py` was traced by hand against the JSON
before running anything. Two examples:

- Korma: cashews stocked 1.5 kg, recipe 60 g/portion → 1500/60 = 25 portions.
  After one order: 1.44 kg. Any off-by-1000 anywhere in the conversion chain
  and these two assertions fail (0 portions, or a 1440 g deduction).
- Biryani min-portion count: rice 10000/200 = 50, chicken 4500/250 = 18,
  onions 6000/100 = 60, oil 5000/50 = 100 → min 18, asserted exactly.

The conversion happens in exactly one function (`to_base_units`), which
rejects cross-dimension comparisons (kg vs ml) instead of silently converting.
`validate_data()` runs at boot and fails loudly if any recipe line's unit
dimension disagrees with its ingredient's stock unit.

Because the scenario tests only anchor *relative* correctness, four probe
tests pin the absolute scale: the SI kilo definitions, a 999/1001 g bracket
that rules out any decade slip, a kg-vs-l drift check, and a source-level
tripwire that reads `BASE_FACTOR` out of `logic.py` and asserts the shipped
1000s. Verified by sabotage: temporarily setting kg = 100 g fails 10 tests
across conversion, availability, and deduction.

Beyond pytest, the running app was driven in headless Chrome with real
clicks: search filtering, an Order click updating both panels from one
response (cashews 1.5 → 1.44 kg and korma 20 → 19 makeable portions), the
inline editor, and the delete guard's 409 surfacing in the UI.

**What would have to be wrong for the tests to still pass anyway:** if the
*same* wrong factor (e.g. 100 instead of 1000) lived in `to_base_units` and in
my hand arithmetic, both would agree and the suite would stay green — the tests
anchor relative correctness (kg→g is ×1000) but not the absolute meaning of a
gram. Also untested by design: the browser JS. The numbers the UI shows come
from the same tested `logic.py` over HTTP, but the wiring itself is only
verified by hand in the browser (restock → dish flips, order → qty drops).

### 3. Given another day

1. Portion-aware availability (`min_portions > 0` ⇒ available) behind the
   reorder flag — the critique above, actually shipped.
2. Edit name/unit safely by migrating recipes in the same write.
3. Second-tab liveness via polling or SSE (right now a second tab refreshes
   state on its own actions only).

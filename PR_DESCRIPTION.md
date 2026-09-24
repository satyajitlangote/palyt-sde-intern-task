# Kitchen Stock + Menu Sync — Palyt SDE intern task

Flask + vanilla JS, no build step. Staff stock panel and customer menu sit side
by side; every mutation returns a fresh `{stock, menu}` snapshot that redraws
both panels — restock an ingredient and its dish comes back with no reload.

**Run:** `pip install -r requirements.txt` · `python -m pytest test_logic.py -q`
· `python app.py` → http://127.0.0.1:5000

Deliberately not built (per brief): QR, payments, accounts, tables, order history.

---

## 1. Calls I made where the data/rules didn't settle it

- **The availability rule is wrong, and I implemented it anyway.** The brief
  blocks a dish if ANY ingredient is below par — but par is a reorder
  threshold, not a can-I-cook threshold. Paneer Tikka boots unavailable
  because Fresh Coriander (80 g) is below its 100 g par, yet that stock covers
  2 full portions. Rather than argue it in a paragraph, the menu prints
  "stock would still cover N portion(s)" on blocked dishes so the flaw is
  visible. Live-demonstrated corner: qty == par == 10 g reads *available* but
  makes 0 portions of a 30 g recipe — the Order button 409s on click. The rule
  I'd defend: available = every ingredient covers ≥ 1 portion; below-par
  becomes a reorder flag instead of hiding dishes.
- **"Below par" is strict.** qty == par counts as available — you reorder when
  you drop below the threshold, not when you touch it. One test pins the boundary.
- **Deleting a used ingredient: blocked, with evidence.** A 409 names the
  dishes (Cashews → Korma + Paneer Butter Masala); Bay Leaves (unused) deletes
  freely. Cascading deletes silently rewrite recipe data; warn-then-allow
  creates ghost ingredients needing a "missing" code path everywhere. Blocking
  is the smallest defensible rule, and the stock table shows a "used by"
  column so the guard surprises no one.
- **par 0 is invalid; qty 0 is fine.** Under the below-par rule, par 0 would
  keep a dish "available" at literally zero stock. Running out is a real
  kitchen state; par 0 is not.
- **Names unique (case-insensitive)** — duplicates make search and the delete
  guard ambiguous. IDs are auto-slugged, never user-edited.
- **Editing covers qty and par only.** Renaming/re-uniting ripples into recipe
  semantics that recipes.json can't express — out of scope, stated rather than silent.
- **Validation on both ends, server authoritative.** Client mirrors rules to
  fail fast inline; the server re-checks everything.

## 2. How I know the numbers are right

Every expected value in `test_logic.py` was traced by hand against the JSON
before running anything. Korma: cashews 1.5 kg stocked vs 60 g/portion recipe
→ 1500/60 = 25 portions; after one order 1.44 kg. Any off-by-1000 in the
conversion chain fails both assertions. Conversion happens in exactly one
function (`to_base_units`), which rejects cross-dimension comparisons (kg vs
ml) instead of guessing; `validate_data()` re-checks the shipped files at boot.

Scenario tests anchor *relative* correctness — they'd still pass if every unit
factor were wrong by the same amount. Four probes pin the absolute scale: the
SI kilo definitions, a 999/1001 g bracket that rules out any decade slip, a
kg-vs-l drift check, and a source-level tripwire reading `BASE_FACTOR` out of
`logic.py`. Verified by sabotage: kg = 100 fails 10 tests across all three areas.

**What would have to be wrong for the tests to pass anyway:** the same wrong
factor living in both `to_base_units` and my hand arithmetic — both would
agree and the suite stays green. The tests prove kg→g is ×1000, not what a
gram means. Also untested by design: browser JS. The UI's numbers come from
the tested `logic.py` over HTTP, but the wiring itself is verified by hand.

Beyond pytest, the running server was driven through every endpoint with a
39-check HTTP smoke test: order → deduction across the kg/g scale (1.5 →
1.44 kg), menu recomputed in the same response (korma 20 → 19 makeable), the
delete guard's 409, all validation 400s, and the qty==par pathology above.
Fittingly, the only bug the smoke run caught was a 1000x mistake **in my own
test code** (restocked coriander with 0.3 as if kg; the row is stocked in
grams) — the API did the right thing and the test failed.

## 3. Given another day

1. Portion-aware availability (`min_portions > 0` ⇒ available) behind a
   reorder flag — the critique in §1, actually shipped.
2. Edit name/unit safely by migrating recipes in the same write.
3. Second-tab liveness via polling or SSE (a second tab currently only
   refreshes on its own actions).

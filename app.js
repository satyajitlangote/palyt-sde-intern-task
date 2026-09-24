/* Front-end. One rule: every server mutation returns a fresh snapshot
   { stock, menu }, and render() redraws both panels from it. That is the
   whole reason restocking visibly moves dishes on/off the menu with no
   reload trickery. No frameworks, no build step. */

let state = { stock: [], menu: [] };
let editingId = null; // ingredient currently being edited inline

const $ = (id) => document.getElementById(id);

// --- api -------------------------------------------------------------------

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

function apply(snapshot) {
  state = snapshot;
  render();
}

// --- rendering ---------------------------------------------------------------

function render() {
  renderStock();
  renderMenu();
  $("conn-status").textContent =
    `${state.stock.length} ingredients · ${state.menu.length} dishes`;
}

function renderStock() {
  const query = $("search").value.trim().toLowerCase();
  const body = $("stock-body");
  body.innerHTML = "";

  for (const ing of state.stock) {
    if (query && !ing.name.toLowerCase().includes(query)) continue;

    const row = document.createElement("tr");
    const belowPar = Number(ing.qty) < Number(ing.par);
    if (belowPar) row.classList.add("below-par");

    const name = document.createElement("td");
    name.className = "name";
    name.textContent = ing.name;
    row.append(name);

    const qty = document.createElement("td");
    qty.className = "num qty";
    qty.textContent = ing.qty;
    if (belowPar) {
      // reorder flag: on the qty cell in the table view, carried onto the
      // name row by the card layout, which hides the qty column's own look.
      const badge = document.createElement("span");
      badge.className = "low-badge";
      badge.textContent = "below par";
      badge.title = "below par — reorder";
      qty.append(" ", badge);
    }
    row.append(qty);

    const unit = document.createElement("td");
    unit.className = "unit";
    unit.textContent = ing.unit;
    row.append(unit);

    const par = document.createElement("td");
    par.className = "num par";
    par.textContent = ing.par;
    row.append(par);

    const used = document.createElement("td");
    used.className = "used-by";
    used.textContent = ing.used_by.length ? ing.used_by.join(", ") : "—";
    row.append(used);

    const actions = document.createElement("td");
    actions.className = "row-actions";
    if (editingId === ing.id) {
      actions.append(buildEditor(ing));
    } else {
      const editBtn = document.createElement("button");
      editBtn.className = "btn-secondary";
      editBtn.textContent = "Edit";
      editBtn.onclick = () => {
        editingId = ing.id;
        render();
      };
      const delBtn = document.createElement("button");
      delBtn.className = "btn-danger";
      delBtn.textContent = "Delete";
      delBtn.onclick = () => deleteIngredient(ing);
      actions.append(editBtn, delBtn);
    }
    row.append(actions);

    body.append(row);
  }

  if (body.children.length === 0) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 6;
    cell.textContent = query ? `no ingredients match "${query}"` : "no ingredients";
    row.append(cell);
    body.append(row);
  }
}

function buildEditor(ing) {
  const frag = document.createDocumentFragment();
  const form = document.createElement("form");
  form.className = "editing";

  const qtyIn = document.createElement("input");
  qtyIn.id = `edit-qty-${ing.id}`;
  qtyIn.value = ing.qty;
  qtyIn.inputMode = "decimal";
  qtyIn.setAttribute("aria-label", "Quantity");

  const parIn = document.createElement("input");
  parIn.id = `edit-par-${ing.id}`;
  parIn.value = ing.par;
  parIn.inputMode = "decimal";
  parIn.setAttribute("aria-label", "Par");

  const saveBtn = document.createElement("button");
  saveBtn.type = "submit";
  saveBtn.className = "btn-primary";
  saveBtn.textContent = "Save";

  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "btn-secondary cancel";
  cancelBtn.textContent = "Cancel";
  cancelBtn.onclick = () => {
    editingId = null;
    render();
  };

  // Enter submits, Escape cancels (submit handler lives on the form)
  form.onsubmit = (e) => {
    e.preventDefault();
    api("PUT", `/api/stock/${ing.id}`, { qty: qtyIn.value, par: parIn.value })
      .then((snap) => {
        editingId = null;
        apply(snap);
      })
      .catch((err) => flash(err.message, true));
  };
  form.onkeydown = (e) => {
    if (e.key === "Escape") cancelBtn.click();
  };

  form.append(qtyIn, parIn, saveBtn, cancelBtn);
  frag.append(form);
  return frag;
}

function renderMenu() {
  const list = $("menu-list");
  list.innerHTML = "";
  for (const dish of state.menu) {
    const card = document.createElement("div");
    card.className = dish.available ? "dish" : "dish unavailable";

    const head = document.createElement("div");
    head.className = "dish-head";
    const name = document.createElement("strong");
    name.textContent = dish.name;
    const price = document.createElement("span");
    price.className = "price";
    price.textContent = `₹${dish.price}`;
    head.append(name, price);
    card.append(head);

    const note = document.createElement("div");
    note.className = "note";
    if (dish.available) {
      note.textContent =
        dish.min_portions === 0
          ? ""
          : `${dish.min_portions} portion${dish.min_portions === 1 ? "" : "s"} makeable`;
    } else {
      note.innerHTML = ""; // built with textContent below
      const label = document.createElement("span");
      label.className = "warn";
      label.textContent = `Unavailable — below par: ${dish.below_par.join(", ")}`;
      note.append(label);
      if (dish.min_portions > 0) {
        // The as-given rule blocks this dish even though stock covers it.
        // Surfacing the discrepancy on purpose (see PR write-up).
        note.append(
          document.createTextNode(
            ` · stock would still cover ${dish.min_portions} portion(s)`
          )
        );
      }
    }
    card.append(note);

    const orderBtn = document.createElement("button");
    orderBtn.textContent = "Order";
    orderBtn.disabled = !dish.available;
    orderBtn.onclick = () =>
      api("POST", "/api/orders", { dish_id: dish.id })
        .then((snap) => {
          flash(`Ordered ${dish.name} — stock updated`);
          apply(snap);
        })
        .catch((err) => flash(err.message, true));
    card.append(orderBtn);

    list.append(card);
  }
}

// --- actions -------------------------------------------------------------------

async function addIngredient(event) {
  event.preventDefault();
  const name = $("f-name").value.trim();
  const qty = $("f-qty").value.trim();
  const par = $("f-par").value.trim();
  const unit = $("f-unit").value;

  // Client-side validation mirrors the server's rules with stated reasons;
  // the server remains authoritative (it re-checks everything).
  const errors = [];
  if (!name) errors.push("name is required");
  if (qty === "" || Number.isNaN(Number(qty)))
    errors.push("qty must be a number");
  else if (Number(qty) < 0) errors.push("qty cannot be negative");
  if (par === "" || Number.isNaN(Number(par)))
    errors.push("par must be a number");
  else if (Number(par) <= 0)
    errors.push("par must be greater than 0 (par 0 would make dishes 'available' at zero stock)");
  $("form-error").textContent = errors.join("; ");
  if (errors.length) return;

  try {
    const snap = await api("POST", "/api/stock", { name, qty, par, unit });
    event.target.reset();
    apply(snap);
  } catch (err) {
    $("form-error").textContent = err.message;
  }
}

function deleteIngredient(ing) {
  // No confirm() dialog: delete immediately, offer Undo in the toast.
  // Only unused ingredients can be deleted (the server 409s otherwise), so
  // restoring via POST /api/stock cannot orphan any recipe reference — the
  // id is slug-derived from the unique name, so it comes back identical.
  api("DELETE", `/api/stock/${ing.id}`)
    .then((snap) => {
      apply(snap);
      toastWithUndo(`Deleted ${ing.name}`, () => {
        api("POST", "/api/stock", {
          name: ing.name,
          qty: ing.qty,
          par: ing.par,
          unit: ing.unit,
        })
          .then((restored) => {
            apply(restored);
            flash(`Restored ${ing.name}`);
          })
          .catch((err) => flash(err.message, true));
      });
    })
    .catch((err) => {
      // e.g. the 409 for ingredients still in use
      flash(err.message, true);
    });
}

// --- flash / toast messages -----------------------------------------------------

let flashTimer = null;
let flashCountdown = null;
let flashDeadline = 0;

function hideFlash() {
  clearTimeout(flashTimer);
  clearInterval(flashCountdown);
  $("flash").className = "";
}

function renderFlash(message, { isError, action, actionLabel, seconds }) {
  const el = $("flash");
  el.innerHTML = "";

  const msg = document.createElement("span");
  msg.className = "flash-msg";
  msg.textContent = message;
  el.append(msg);

  if (action) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = actionLabel;
    btn.onclick = () => {
      hideFlash();
      action();
    };
    el.append(btn);
  }

  if (seconds > 0) {
    const bar = document.createElement("span");
    bar.className = "flash-bar";
    el.append(bar);
    flashDeadline = Date.now() + seconds * 1000;
    // keep hover from hiding the toast while a countdown bar is running
    el.onmouseenter = () => pauseFlash();
    el.onmouseleave = () => resumeFlash();
  } else {
    el.onmouseenter = null;
    el.onmouseleave = null;
  }

  el.className = "show" + (isError ? " error" : "");
}

function pauseFlash() {
  clearTimeout(flashTimer);
  clearInterval(flashCountdown);
}

function resumeFlash() {
  if (flashDeadline <= Date.now()) return;
  scheduleFlashHide();
}

function scheduleFlashHide() {
  clearTimeout(flashTimer);
  flashTimer = setTimeout(hideFlash, Math.max(0, flashDeadline - Date.now()));
}

function flash(message, isError = false) {
  clearTimeout(flashTimer);
  clearInterval(flashCountdown);
  renderFlash(message, { isError, action: null, seconds: 3 });
  scheduleFlashHide();
}

function toastWithUndo(message, undoFn) {
  clearTimeout(flashTimer);
  clearInterval(flashCountdown);
  renderFlash(message, {
    isError: false,
    action: undoFn,
    actionLabel: "Undo",
    seconds: 6,
  });
  scheduleFlashHide();
}

// --- boot ------------------------------------------------------------------------

$("add-form").addEventListener("submit", addIngredient);
$("search").addEventListener("input", render);

api("GET", "/api/stock")
  .then(apply)
  .catch((err) => flash(`cannot reach server: ${err.message}`, true));

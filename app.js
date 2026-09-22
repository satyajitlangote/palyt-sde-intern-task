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

    const name = document.createElement("td");
    name.textContent = ing.name;
    row.append(name);

    const qty = document.createElement("td");
    qty.className = "num";
    qty.textContent = ing.qty;
    if (Number(ing.qty) < Number(ing.par)) {
      qty.classList.add("low");
      qty.title = "below par — reorder";
    }
    row.append(qty);

    const unit = document.createElement("td");
    unit.textContent = ing.unit;
    row.append(unit);

    const par = document.createElement("td");
    par.className = "num";
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
      editBtn.textContent = "Edit";
      editBtn.onclick = () => {
        editingId = ing.id;
        render();
      };
      const delBtn = document.createElement("button");
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
  const qtyIn = document.createElement("input");
  qtyIn.id = `edit-qty-${ing.id}`;
  qtyIn.value = ing.qty;
  const parIn = document.createElement("input");
  parIn.id = `edit-par-${ing.id}`;
  parIn.value = ing.par;

  const saveBtn = document.createElement("button");
  saveBtn.textContent = "Save";
  saveBtn.onclick = () =>
    api("PUT", `/api/stock/${ing.id}`, {
      qty: qtyIn.value,
      par: parIn.value,
    })
      .then((snap) => {
        editingId = null;
        apply(snap);
      })
      .catch((err) => flash(err.message, true));

  const cancelBtn = document.createElement("button");
  cancelBtn.textContent = "Cancel";
  cancelBtn.onclick = () => {
    editingId = null;
    render();
  };

  frag.append(qtyIn, parIn, saveBtn, cancelBtn);
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

async function deleteIngredient(ing) {
  const usage =
    ing.used_by.length > 0
      ? `\n\nIt is used by: ${ing.used_by.join(", ")}`
      : "\n\nNo dish uses it.";
  if (!window.confirm(`Delete ${ing.name}?${usage}`)) return;
  try {
    apply(await api("DELETE", `/api/stock/${ing.id}`));
  } catch (err) {
    flash(err.message, true); // e.g. the 409 for ingredients still in use
  }
}

// --- flash messages -----------------------------------------------------------

let flashTimer = null;
function flash(message, isError = false) {
  const el = $("flash");
  el.textContent = message;
  el.className = "show" + (isError ? " error" : "");
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => {
    el.className = "";
  }, 3000);
}

// --- boot ------------------------------------------------------------------------

$("add-form").addEventListener("submit", addIngredient);
$("search").addEventListener("input", render);

api("GET", "/api/stock")
  .then(apply)
  .catch((err) => flash(`cannot reach server: ${err.message}`, true));

(() => {
  const API = (window.API_URL || "").replace(/\/$/, "");
  const esc = s => String(s ?? "").replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const S = {
    view: "shop", cat: "all", flash: null, error: null, edit: null,
    cid: +localStorage.getItem("cid") || null,
    token: localStorage.getItem("token"),
    state: { currency: "", mission: 1 }, chars: [], items: [], log: [],
  };

  // ------------------------------------------------------------ API
  async function api(path, method = "GET", data) {
    const headers = { "Content-Type": "application/json" };
    if (S.token) headers.Authorization = "Bearer " + S.token;
    let r;
    try {
      r = await fetch(API + path, { method, headers, body: data ? JSON.stringify(data) : undefined });
    } catch {
      throw new Error("Can't reach the server");
    }
    const d = await r.json().catch(() => ({}));
    if (r.status === 401 && path.startsWith("/api/gm") && path !== "/api/gm/login") {
      S.token = null; localStorage.removeItem("token");
    }
    if (!r.ok) throw new Error(d.error || r.statusText);
    return d;
  }

  async function refresh() {
    try {
      const q = S.cid ? `?character_id=${S.cid}` : "";
      [S.state, S.chars, S.items] = await Promise.all(
        [api("/api/state"), api("/api/characters"), api("/api/items" + q)]);
      if (S.cid && !S.chars.some(c => c.id === S.cid)) { S.cid = null; localStorage.removeItem("cid"); }
      if (S.view === "gm" && S.token) S.log = await api("/api/gm/log");
      S.error = null;
    } catch (e) { S.error = e.message; }
    render();
  }

  async function act(fn) {
    try { const d = await fn(); S.flash = { t: "ok", m: d.message || "Done." }; }
    catch (e) { S.flash = { t: "error", m: e.message }; }
    await refresh();
  }

  // ---------------------------------------------------------- views
  const tags = i => `<div class="tags"><span class="tag ${i.category}">${i.category}</span>
    ${i.inventory_bonus ? `<span class="tag">+${i.inventory_bonus} slots</span>` : ""}
    ${i.per_mission_limit ? `<span class="tag">${i.per_mission_limit}× per mission</span>` : ""}
    ${i.max_owned ? `<span class="tag">max ${i.max_owned} owned</span>` : ""}
    ${i.owned ? `<span class="tag owned">owned: ${i.owned}</span>` : ""}</div>`;

  function shop() {
    const cur = esc(S.state.currency);
    const cards = S.items.filter(i => S.cat === "all" || i.category === S.cat).map(i => {
      const reason = S.cid ? i.reason : "Pick a character";
      return `<article class="card ${i.in_stock ? "" : "soldout"}">
        <div class="card-head"><h3>${esc(i.name)}</h3><span class="price">${i.price}</span></div>
        ${tags(i)}<p>${esc(i.description)}</p>
        <button data-act="buy" data-id="${i.id}" ${reason ? "disabled" : ""}>${esc(reason || `Buy for ${i.price} ${S.state.currency}`)}</button>
      </article>`;
    }).join("");
    const tabs = [["all", "All"], ["consumable", "Consumables"], ["equipment", "Equipment"]]
      .map(([v, t]) => `<a href="#" data-act="cat" data-v="${v}" class="${S.cat === v ? "on" : ""}">${t}</a>`).join("");
    return `<section class="rules"><h2>Rules</h2><ul>
      <li>You may not steal from this shop.</li>
      <li>You may not question how any of these items are here.</li>
      <li>You may not try to intimidate me for discounts.</li><li>Be kind.</li></ul>
      <p class="muted">Each character may hold up to 3 consumable items, unless they acquire an item that grants more
      inventory space. Items that grant inventory space can only be bought once per mission. Prices are in ${cur}.</p></section>
      <div class="tabs">${tabs}</div><div class="grid">${cards}</div>`;
  }

  function party() {
    if (!S.chars.length) return `<h2>Party</h2><p class="muted">No characters yet. The GM can add them.</p>`;
    return `<h2>Party</h2><div class="grid">` + S.chars.map(c => `<article class="card">
      <div class="card-head"><h3>${esc(c.name)}</h3><span class="price">${c.money} ${esc(S.state.currency)}</span></div>
      <div class="tags"><span class="tag">consumables ${c.held} / ${c.capacity}</span></div>
      ${c.inventory.length ? "" : `<p class="muted">Empty inventory.</p>`}
      <ul class="inv">${c.inventory.map(i => `<li><div><strong>${esc(i.name)}</strong>${i.qty > 1 ? " ×" + i.qty : ""}
        <span class="tag ${i.category}">${i.category}</span><div class="muted small">${esc(i.description)}</div></div>
        ${i.category === "consumable" ? `<button class="small" data-act="use" data-cid="${c.id}" data-id="${i.id}">Use</button>` : ""}
        </li>`).join("")}</ul></article>`).join("") + `</div>`;
  }

  function itemForm() {
    const e = S.edit, v = k => esc(e[k] ?? "");
    return `<form class="panel stack" data-form="item"><h3>${e.id ? "Edit" : "New"} item</h3>
      <label>Name <input name="name" required value="${v("name")}"></label>
      <label>Price <input type="number" min="0" name="price" value="${e.price ?? 0}"></label>
      <label>Description <textarea name="description" rows="4">${v("description")}</textarea></label>
      <label>Type <select name="category">
        <option value="consumable" ${e.category !== "equipment" ? "selected" : ""}>Consumable (uses a slot)</option>
        <option value="equipment" ${e.category === "equipment" ? "selected" : ""}>Equipment (no slot)</option></select></label>
      <label>Extra inventory slots granted <input type="number" name="inventory_bonus" value="${e.inventory_bonus ?? 0}"></label>
      <label>Max purchases per mission (blank = no limit) <input type="number" min="1" name="per_mission_limit" value="${v("per_mission_limit")}"></label>
      <label>Max copies owned (blank = no limit) <input type="number" min="1" name="max_owned" value="${v("max_owned")}"></label>
      <label class="check"><input type="checkbox" name="in_stock" ${e.in_stock !== false ? "checked" : ""}> In stock</label>
      <div class="row"><button>Save</button><button type="button" class="ghost" data-act="canceledit">Cancel</button></div></form>`;
  }

  function gm() {
    if (!S.token) return `<section class="panel narrow"><h2>GM login</h2>
      <form class="row" data-form="login"><input type="password" name="password" placeholder="GM password" required autofocus>
      <button>Enter</button></form></section>`;
    const cur = esc(S.state.currency);
    const itemOpts = S.items.map(i => `<option value="${i.id}">${esc(i.name)}</option>`).join("");
    const chars = S.chars.map(c => `<div class="char">
      <div class="row between"><h4>${esc(c.name)} — ${c.money} ${cur} <span class="muted small">(slots ${c.held}/${c.capacity})</span></h4>
        <button class="small danger" data-act="delchar" data-cid="${c.id}">Delete</button></div>
      <form class="row" data-form="money"><input type="hidden" name="cid" value="${c.id}">
        <input type="number" name="amount" placeholder="Amount (e.g. 50 or -20)" required>
        <button class="small" name="mode" value="add">Add / subtract</button>
        <button class="small ghost" name="mode" value="set">Set to</button></form>
      <form class="row" data-form="grant"><input type="hidden" name="cid" value="${c.id}">
        <select name="item_id">${itemOpts}</select><input type="number" name="qty" value="1" min="1" style="width:4.5rem">
        <button class="small">Give item</button></form>
      <ul class="inv compact">${c.inventory.map(i => `<li><span>${esc(i.name)}${i.qty > 1 ? " ×" + i.qty : ""}</span>
        <button class="small ghost" data-act="remove" data-cid="${c.id}" data-id="${i.id}">Remove one</button></li>`).join("")
        || `<li class="muted">Empty inventory</li>`}</ul></div>`).join("");
    const rows = S.items.map(i => `<tr class="${i.in_stock ? "" : "soldout"}"><td>${esc(i.name)}</td><td>${i.price}</td><td>${i.category}</td>
      <td class="small muted">${i.inventory_bonus ? `+${i.inventory_bonus} slots · ` : ""}${i.per_mission_limit ? `${i.per_mission_limit}/mission · ` : ""}${i.max_owned ? `max ${i.max_owned}` : ""}</td>
      <td><button class="small ${i.in_stock ? "" : "ghost"}" data-act="toggle" data-id="${i.id}">${i.in_stock ? "In stock" : "Sold out"}</button></td>
      <td class="row"><button class="small ghost" data-act="edit" data-id="${i.id}">Edit</button>
        <button class="small danger" data-act="delitem" data-id="${i.id}">Delete</button></td></tr>`).join("");
    return `<div class="row between"><h2>GM dashboard</h2><button class="small ghost" data-act="logout">Log out</button></div>
      <section class="panel"><h3>Campaign</h3><div class="row"><span>Current mission: <strong>${S.state.mission}</strong></span>
        <button data-act="mission">Start next mission</button>
        <form class="row" data-form="currency"><input name="currency" value="${cur}" size="8"><button class="small ghost">Rename currency</button></form></div></section>
      <section class="panel"><h3>Characters</h3>
        <form class="row" data-form="addchar"><input name="name" placeholder="Character name" required>
          <input name="money" type="number" placeholder="Starting ${cur}" value="0"><button>Add character</button></form>
        ${chars || `<p class="muted">No characters yet.</p>`}</section>
      ${S.edit ? itemForm() : ""}
      <section class="panel"><div class="row between"><h3>Shop items</h3><button data-act="edit">+ New item</button></div>
        <table><thead><tr><th>Item</th><th>Price</th><th>Type</th><th>Limits</th><th>Stock</th><th></th></tr></thead><tbody>${rows}</tbody></table></section>
      <section class="panel"><h3>Activity log</h3><ul class="log">${S.log.map(l =>
        `<li><span class="muted small">${esc(l.ts)} · M${l.mission}</span> ${esc(l.message)}</li>`).join("") || `<li class="muted">Nothing yet.</li>`}</ul></section>`;
  }

  function render() {
    const a = S.chars.find(c => c.id === S.cid);
    const nav = [["shop", "Shop"], ["party", "Party"], ["gm", "GM"]]
      .map(([v, t]) => `<a href="#" data-act="view" data-v="${v}">${t}</a>`).join("");
    const opts = S.chars.map(c => `<option value="${c.id}" ${c.id === S.cid ? "selected" : ""}>${esc(c.name)}</option>`).join("");
    document.getElementById("app").innerHTML = `
      <header class="top"><div class="brand"><h1>The Azure Shop</h1><p>“Look or buy. Be welcome in Blue”</p></div>
        <nav>${nav}</nav>
        <div class="who"><label for="cid">Shopping as</label><select id="cid"><option value="">— choose —</option>${opts}</select></div></header>
      ${a ? `<div class="wallet"><strong>${esc(a.name)}</strong><span class="money">${a.money} ${esc(S.state.currency)}</span>
        <span>Consumable slots: ${a.held} / ${a.capacity}</span><span class="muted">Mission ${S.state.mission}</span></div>` : ""}
      <main>${S.error ? `<div class="flash error">${esc(S.error)}. Check API_URL in config.js and that the back-end is running.</div>` : ""}
        ${S.flash ? `<div class="flash ${S.flash.t}">${esc(S.flash.m)}</div>` : ""}
        ${S.view === "party" ? party() : S.view === "gm" ? gm() : shop()}</main>`;
  }

  // -------------------------------------------------------- events
  const A = {
    view: d => { S.view = d.v; S.flash = null; S.edit = null; return refresh(); },
    cat: d => { S.cat = d.v; render(); },
    buy: d => act(() => api("/api/buy", "POST", { character_id: S.cid, item_id: +d.id })),
    use: d => act(() => api("/api/use", "POST", { character_id: +d.cid, item_id: +d.id })),
    remove: d => act(() => api(`/api/gm/characters/${d.cid}/remove`, "POST", { item_id: +d.id })),
    delchar: d => confirm("Delete this character and their inventory?") && act(() => api(`/api/gm/characters/${d.cid}`, "DELETE")),
    mission: () => confirm("Start the next mission? Per-mission limits reset.") && act(() => api("/api/gm/mission/new", "POST")),
    toggle: d => act(() => api(`/api/gm/items/${d.id}/toggle`, "POST")),
    delitem: d => confirm("Delete this item from the shop and all inventories?") && act(() => api(`/api/gm/items/${d.id}`, "DELETE")),
    edit: d => { S.edit = d.id ? { ...S.items.find(i => i.id == d.id) } : {}; render(); window.scrollTo(0, 0); },
    canceledit: () => { S.edit = null; render(); },
    logout: () => { S.token = null; localStorage.removeItem("token"); render(); },
  };

  const num = v => (v === "" || v == null ? null : +v);
  const F = {
    login: f => api("/api/gm/login", "POST", { password: f.password }).then(d => {
      S.token = d.token; localStorage.setItem("token", d.token); return { message: "Logged in." }; }),
    addchar: f => api("/api/gm/characters", "POST", { name: f.name, money: +f.money || 0 }),
    money: (f, mode) => api(`/api/gm/characters/${f.cid}/money`, "POST", { amount: +f.amount, mode }),
    grant: f => api(`/api/gm/characters/${f.cid}/grant`, "POST", { item_id: +f.item_id, qty: +f.qty || 1 }),
    currency: f => api("/api/gm/currency", "POST", { currency: f.currency }),
    item: f => {
      const data = { name: f.name, price: +f.price || 0, description: f.description, category: f.category,
        inventory_bonus: +f.inventory_bonus || 0, per_mission_limit: num(f.per_mission_limit),
        max_owned: num(f.max_owned), in_stock: !!f.in_stock };
      const req = S.edit.id ? api(`/api/gm/items/${S.edit.id}`, "PUT", data) : api("/api/gm/items", "POST", data);
      return req.then(d => { S.edit = null; return d; });
    },
  };

  document.addEventListener("click", e => {
    const el = e.target.closest("[data-act]");
    if (!el || el.disabled) return;
    e.preventDefault();
    A[el.dataset.act]?.(el.dataset);
  });
  document.addEventListener("submit", e => {
    const form = e.target.closest("[data-form]");
    if (!form) return;
    e.preventDefault();
    const f = Object.fromEntries(new FormData(form));
    act(() => F[form.dataset.form](f, e.submitter && e.submitter.value));
  });
  document.addEventListener("change", e => {
    if (e.target.id !== "cid") return;
    S.cid = +e.target.value || null;
    S.cid ? localStorage.setItem("cid", S.cid) : localStorage.removeItem("cid");
    refresh();
  });

  refresh();
})();

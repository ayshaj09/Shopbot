// Bazaar Lane seller dashboard.
(function () {
  const { api, esc, money, fmtDate, statusBadge, toast } = window.SB;
  const session = window.SB.storage("sessionStorage");
  const $ = (s) => document.querySelector(s);
  const TOKEN_KEY = "shopbot.adminToken";
  const STATUSES = ["placed", "shipped", "delivered", "cancelled"];

  let token = session.get(TOKEN_KEY);
  let conversations = [];
  let products = [];

  // Admin request: adds the token, kicks back to login on 401.
  async function adminApi(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({}, opts.headers, { "X-Admin-Token": token || "" });
    try {
      return await api(path, opts);
    } catch (err) {
      if (err.status === 401) {
        logout("Your token was rejected. Please log in again.");
      } else if (err.status === 0) {
        showBanner(err.message);
      }
      throw err;
    }
  }

  function showBanner(msg) { const b = $("#apiBanner"); b.textContent = "⚠️ " + msg; b.hidden = false; }
  const hideBanner = () => { $("#apiBanner").hidden = true; };

  // ---------- login ----------
  function showLogin(message) {
    $("#dashboard").hidden = true;
    $("#logout").hidden = true;
    $("#refresh").hidden = true;
    $("#login").hidden = false;
    $("#loginError").hidden = !message;
    $("#loginError").textContent = message || "";
    setTimeout(() => $("#token").focus(), 50);
  }

  function logout(message) {
    token = null;
    session.del(TOKEN_KEY);
    showLogin(message);
  }

  $("#loginForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    token = $("#token").value.trim();
    if (!token) return;
    try {
      const stats = await adminApi("/admin/stats");
      session.set(TOKEN_KEY, token);
      $("#token").value = "";
      openDashboard(stats);
    } catch (err) {
      if (err.status !== 401) showLogin(err.message);
    }
  });

  $("#logout").addEventListener("click", () => logout());
  $("#refresh").addEventListener("click", () => loadAll());

  function openDashboard(stats) {
    $("#login").hidden = true;
    $("#dashboard").hidden = false;
    $("#logout").hidden = false;
    $("#refresh").hidden = false;
    loadAll(stats);
  }

  // ---------- tabs ----------
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b === btn));
      document.querySelectorAll(".tab-panel").forEach((p) => { p.hidden = p.id !== "tab-" + btn.dataset.tab; });
    });
  });

  async function loadAll(stats) {
    hideBanner();
    const jobs = [
      stats ? Promise.resolve(renderStats(stats)) : loadStats(),
      loadOrders(), loadProducts(), loadConversations(), loadCustomers(),
    ];
    await Promise.allSettled(jobs);
  }

  // ---------- stats ----------
  async function loadStats() {
    try { renderStats(await adminApi("/admin/stats")); }
    catch (err) { $("#stats").innerHTML = '<div class="empty">Stats unavailable: ' + esc(err.message) + "</div>"; }
  }

  function renderStats(s) {
    const n = (x) => Number(x || 0).toLocaleString("en-US");
    const low = s.low_stock || [];
    const cards = [
      ["Revenue", money(s.revenue), ""],
      ["Orders", n(s.orders), ""],
      ["Open orders", n(s.open_orders), "placed or shipped"],
      ["Customers", n(s.users), ""],
      ["Conversations", n(s.conversations), n(s.messages) + " messages"],
      ["Avg reply time", (Number(s.avg_latency_ms || 0) / 1000).toFixed(2) + " s", ""],
      ["Tokens in / out", n(s.tokens_in) + " / " + n(s.tokens_out), ""],
      ["Est. cost", "$" + Number(s.est_cost_usd || 0).toFixed(4), "LLM usage"],
      ["Blocked messages", n(s.blocked), "guardrail hits", Number(s.blocked) > 0 ? "warn" : ""],
      ["Low stock", n(low.length),
        low.length ? low.map((p) => esc(p.name) + " (" + esc(p.stock) + ")").join(", ") : "all good",
        low.length ? "warn" : ""],
    ];
    $("#stats").innerHTML = cards.map((c) =>
      '<div class="stat' + (c[3] ? " stat-" + c[3] : "") + '"><span class="stat-label">' + esc(c[0]) + "</span>" +
      '<span class="stat-value">' + esc(c[1]) + "</span>" +
      (c[2] ? '<span class="stat-sub">' + c[2] + "</span>" : "") + "</div>").join("");
    // note: c[2] for low stock is already escaped above; other subs are plain strings built here
  }

  // ---------- orders ----------
  async function loadOrders() {
    const t = $("#ordersTable");
    try {
      const orders = await adminApi("/admin/orders");
      if (!orders.length) { t.innerHTML = '<tr><td class="empty">No orders yet.</td></tr>'; return; }
      t.innerHTML = "<thead><tr><th>#</th><th>Date</th><th>Customer</th><th>Items</th><th>Total</th><th>Ship to</th><th>Status</th></tr></thead><tbody>" +
        orders.map((o) =>
          "<tr><td>" + esc(o.id) + "</td>" +
          '<td class="nowrap">' + esc(fmtDate(o.created_at)) + "</td>" +
          "<td>" + esc(o.customer_name || "—") + '<div class="muted small">' + esc(o.user_id) + "</div></td>" +
          "<td>" + (o.items || []).map((i) => esc(i.quantity) + " × " + esc(i.name)).join("<br>") + "</td>" +
          '<td class="nowrap">' + money(o.total) + "</td>" +
          '<td class="wrap">' + esc(o.shipping_address) + "</td>" +
          "<td>" + statusSelect(o) + "</td></tr>").join("") + "</tbody>";
    } catch (err) {
      t.innerHTML = '<tr><td class="empty">Couldn\'t load orders: ' + esc(err.message) + "</td></tr>";
    }
  }

  function statusSelect(o) {
    const cur = String(o.status || "").toLowerCase();
    const opts = STATUSES.includes(cur) ? STATUSES : [cur].concat(STATUSES);
    return '<select class="input input-sm status-select status-' + esc(cur) + '" data-id="' + esc(o.id) + '" data-prev="' + esc(cur) + '">' +
      opts.map((s) => '<option value="' + esc(s) + '"' + (s === cur ? " selected" : "") + ">" + esc(s) + "</option>").join("") + "</select>";
  }

  $("#ordersTable").addEventListener("change", async (e) => {
    const sel = e.target.closest(".status-select");
    if (!sel) return;
    const prev = sel.dataset.prev;
    sel.disabled = true;
    try {
      await adminApi("/admin/orders/" + encodeURIComponent(sel.dataset.id), { method: "PATCH", body: { status: sel.value } });
      sel.dataset.prev = sel.value;
      sel.className = "input input-sm status-select status-" + sel.value;
      toast("Order #" + sel.dataset.id + " marked " + sel.value, "ok");
      loadStats();
    } catch (err) {
      sel.value = prev;
      toast("Couldn't update order: " + err.message, "error");
    } finally {
      sel.disabled = false;
    }
  });

  // ---------- products ----------
  async function loadProducts() {
    const t = $("#productsTable");
    try {
      products = await adminApi("/admin/products");
      $("#categoryList").innerHTML = Array.from(new Set(products.map((p) => p.category).filter(Boolean)))
        .map((c) => '<option value="' + esc(c) + '">').join("");
      if (!products.length) { t.innerHTML = '<tr><td class="empty">No products yet.</td></tr>'; return; }
      t.innerHTML = "<thead><tr><th>#</th><th>Name</th><th>Category</th><th>Price (PKR)</th><th>Stock</th><th>Visible</th></tr></thead><tbody>" +
        products.map((p) => {
          const active = p.active === undefined ? true : !!p.active;
          return '<tr class="' + (active ? "" : "row-muted") + '"><td>' + esc(p.id) + "</td>" +
            "<td><strong>" + esc(p.name) + '</strong><div class="muted small clamp">' + esc(p.description) + "</div></td>" +
            "<td>" + esc(p.category) + "</td>" +
            '<td><input class="input input-sm num" type="number" min="0" step="1" data-id="' + esc(p.id) + '" data-field="price" value="' + esc(p.price) + '"></td>' +
            '<td><input class="input input-sm num' + (Number(p.stock) <= 5 ? " low" : "") + '" type="number" min="0" step="1" data-id="' + esc(p.id) + '" data-field="stock" value="' + esc(p.stock) + '"></td>' +
            '<td><input type="checkbox" data-id="' + esc(p.id) + '" data-field="active"' + (active ? " checked" : "") + "></td></tr>";
        }).join("") + "</tbody>";
    } catch (err) {
      t.innerHTML = '<tr><td class="empty">Couldn\'t load products: ' + esc(err.message) + "</td></tr>";
    }
  }

  $("#productsTable").addEventListener("change", async (e) => {
    const el = e.target;
    const field = el.dataset && el.dataset.field;
    if (!field) return;
    const product = products.find((p) => String(p.id) === el.dataset.id) || {};
    let value;
    if (field === "active") {
      value = el.checked;
    } else {
      value = Number(el.value);
      if (!Number.isFinite(value) || value < 0 || el.value === "") {
        toast("Enter a number of 0 or more.", "error");
        el.value = product[field];
        return;
      }
      value = Math.round(value);
    }
    el.disabled = true;
    try {
      await adminApi("/admin/products/" + encodeURIComponent(el.dataset.id), { method: "PATCH", body: { [field]: value } });
      product[field] = value;
      toast((product.name || "Product") + ": " + field + " saved", "ok");
      if (field === "active") el.closest("tr").classList.toggle("row-muted", !value);
      if (field === "stock") { el.classList.toggle("low", value <= 5); loadStats(); }
    } catch (err) {
      if (field === "active") el.checked = !value; else el.value = product[field];
      toast("Couldn't save: " + err.message, "error");
    } finally {
      el.disabled = false;
    }
  });
  // Enter in an inline input commits it.
  $("#productsTable").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.matches("input.num")) e.target.blur();
  });

  // Add product modal
  $("#addProduct").addEventListener("click", () => {
    $("#productForm").reset();
    $("#productModal").hidden = false;
    setTimeout(() => $("#productForm").elements.name.focus(), 50);
  });
  $("#cancelProduct").addEventListener("click", () => { $("#productModal").hidden = true; });
  $("#productForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target.elements;
    const body = {
      name: f.name.value.trim(),
      category: f.category.value.trim(),
      price: Math.round(Number(f.price.value)),
      stock: Math.round(Number(f.stock.value)),
      description: f.description.value.trim(),
    };
    const submit = e.target.querySelector("button[type=submit]");
    submit.disabled = true;
    try {
      await adminApi("/admin/products", { method: "POST", body });
      $("#productModal").hidden = true;
      toast("Added " + body.name, "ok");
      loadProducts();
    } catch (err) {
      toast("Couldn't add product: " + err.message, "error");
    } finally {
      submit.disabled = false;
    }
  });

  // ---------- conversations ----------
  function toolNames(tc) {
    if (typeof tc === "string") {
      try { tc = JSON.parse(tc); } catch (_) { return tc ? [tc] : []; }
    }
    if (!Array.isArray(tc)) return [];
    return tc.map((t) => (t && t.name) ? t.name : String(t));
  }

  async function loadConversations() {
    try {
      conversations = await adminApi("/admin/conversations");
      renderConversations();
    } catch (err) {
      $("#convTable").innerHTML = '<tr><td class="empty">Couldn\'t load conversations: ' + esc(err.message) + "</td></tr>";
    }
  }

  function renderConversations() {
    const q = $("#convFilter").value.trim().toLowerCase();
    const blockedOnly = $("#convBlocked").checked;
    const rows = conversations.filter((c) => {
      if (blockedOnly && !c.blocked) return false;
      if (!q) return true;
      return [c.user_id, c.thread_id, c.user_message, c.reply].some((v) => String(v || "").toLowerCase().includes(q));
    });
    const t = $("#convTable");
    if (!rows.length) { t.innerHTML = '<tr><td class="empty">No conversations to show.</td></tr>'; return; }
    t.innerHTML = "<thead><tr><th>Time</th><th>User / thread</th><th>Message</th><th>Reply</th><th>Tools</th><th>Tokens</th><th>Latency</th></tr></thead><tbody>" +
      rows.map((c) =>
        '<tr class="' + (c.blocked ? "row-blocked" : "") + '">' +
        '<td class="nowrap">' + esc(fmtDate(c.created_at)) + (c.blocked ? '<div><span class="badge badge-cancelled">blocked</span></div>' : "") + "</td>" +
        "<td>" + esc(c.user_id) + '<div class="muted small">' + esc(c.thread_id) + "</div></td>" +
        '<td class="wrap">' + esc(c.user_message) + "</td>" +
        '<td class="wrap"><details><summary>' + esc(String(c.reply || "").slice(0, 90)) + (String(c.reply || "").length > 90 ? "…" : "") +
          "</summary><div>" + esc(c.reply).replace(/\n/g, "<br>") + "</div></details></td>" +
        "<td>" + toolNames(c.tool_calls).map((n) => '<span class="tool">' + esc(n) + "</span>").join(" ") + "</td>" +
        '<td class="nowrap">' + esc(c.tokens_in || 0) + " / " + esc(c.tokens_out || 0) + "</td>" +
        '<td class="nowrap">' + esc(Math.round(Number(c.latency_ms) || 0)) + " ms</td></tr>").join("") + "</tbody>";
  }
  $("#convFilter").addEventListener("input", renderConversations);
  $("#convBlocked").addEventListener("change", renderConversations);

  // ---------- customers ----------
  async function loadCustomers() {
    const t = $("#customersTable");
    try {
      const customers = await adminApi("/admin/customers");
      if (!customers.length) { t.innerHTML = '<tr><td class="empty">No customers yet.</td></tr>'; return; }
      t.innerHTML = "<thead><tr><th>User</th><th>Conversations</th><th>Messages</th><th>Orders</th><th>Memories</th><th>Last seen</th></tr></thead><tbody>" +
        customers.map((c) =>
          "<tr><td><strong>" + esc(c.user_id) + "</strong></td>" +
          "<td>" + esc(c.conversations) + "</td><td>" + esc(c.messages) + "</td><td>" + esc(c.orders) + "</td>" +
          '<td class="wrap">' + ((c.memories || []).length
            ? '<ul class="plain">' + c.memories.map((m) => "<li>" + esc(typeof m === "string" ? m : m.fact) + "</li>").join("") + "</ul>"
            : '<span class="muted">—</span>') + "</td>" +
          '<td class="nowrap">' + esc(fmtDate(c.last_seen)) + "</td></tr>").join("") + "</tbody>";
    } catch (err) {
      t.innerHTML = '<tr><td class="empty">Couldn\'t load customers: ' + esc(err.message) + "</td></tr>";
    }
  }

  // ---------- boot ----------
  if (token) {
    adminApi("/admin/stats").then(openDashboard).catch((err) => { if (err.status !== 401) showLogin(err.message); });
  } else {
    showLogin();
  }
})();

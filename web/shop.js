// Shop page: filters, sorting, product grid and details popup.
(function () {
  const { api, esc, money, CATEGORIES, categoryEmoji, productImage, productCard, stockLabel, buyMessage, banner } = window.SB;
  const $ = (s) => document.querySelector(s);
  const params = new URLSearchParams(location.search);

  let products = [];
  let priceCeiling = 10000;
  const state = { q: "", category: "", maxPrice: null, inStock: false, sort: "featured" };

  // ?category= is matched case-insensitively against the known categories.
  const wanted = (params.get("category") || "").trim().toLowerCase();
  if (wanted) state.category = CATEGORIES.find((c) => c.toLowerCase() === wanted) || params.get("category").trim();
  const openProductId = params.get("product");

  function categoriesInUse() {
    const extra = products.map((p) => p.category).filter((c) => c && !CATEGORIES.some((k) => k.toLowerCase() === String(c).toLowerCase()));
    return CATEGORIES.concat(Array.from(new Set(extra)));
  }

  function renderChips() {
    $("#catChips").innerHTML = [""].concat(categoriesInUse()).map((c) =>
      '<button type="button" class="chip' + (c.toLowerCase() === state.category.toLowerCase() ? " active" : "") + '" data-cat="' + esc(c) + '">' +
      (c ? categoryEmoji(c) + " " + esc(c) : "All") + "</button>").join("");
  }

  function setupSlider() {
    const max = products.reduce((m, p) => Math.max(m, Number(p.price) || 0), 0);
    priceCeiling = Math.max(500, Math.ceil(max / 500) * 500);
    const s = $("#maxPrice");
    s.max = priceCeiling;
    if (state.maxPrice === null || state.maxPrice > priceCeiling) state.maxPrice = priceCeiling;
    s.value = state.maxPrice;
    updatePriceLabel();
  }

  function updatePriceLabel() {
    $("#priceLabel").textContent = state.maxPrice >= priceCeiling ? "Any" : "up to " + money(state.maxPrice);
  }

  function filtered() {
    const q = state.q.toLowerCase();
    const list = products.filter((p) => {
      if (state.category && String(p.category).toLowerCase() !== state.category.toLowerCase()) return false;
      if (state.inStock && !(Number(p.stock) > 0)) return false;
      if (state.maxPrice !== null && state.maxPrice < priceCeiling && Number(p.price) > state.maxPrice) return false;
      if (q && !(String(p.name) + " " + String(p.description) + " " + String(p.category)).toLowerCase().includes(q)) return false;
      return true;
    });
    const byNum = (k, dir) => (a, b) => dir * ((Number(a[k]) || 0) - (Number(b[k]) || 0));
    if (state.sort === "price-asc") list.sort(byNum("price", 1));
    else if (state.sort === "price-desc") list.sort(byNum("price", -1));
    else if (state.sort === "name") list.sort((a, b) => String(a.name).localeCompare(String(b.name)));
    else list.sort(byNum("id", 1));
    return list;
  }

  function render() {
    const list = filtered();
    $("#resultsTitle").textContent = state.category ? state.category : "All products";
    $("#resultsCount").textContent = list.length + " of " + products.length + " products";
    $("#grid").innerHTML = list.length
      ? list.map(productCard).join("")
      : '<div class="empty">No products match these filters. <button type="button" class="btn btn-ghost btn-sm" data-reset>Reset filters</button></div>';
  }

  function syncUrl() {
    const p = new URLSearchParams(location.search);
    if (state.category) p.set("category", state.category); else p.delete("category");
    p.delete("product");
    const qs = p.toString();
    history.replaceState(null, "", location.pathname + (qs ? "?" + qs : ""));
  }

  // ---------- filter events ----------
  let t = null;
  $("#search").addEventListener("input", (e) => {
    clearTimeout(t);
    t = setTimeout(() => { state.q = e.target.value.trim(); render(); }, 150);
  });
  $("#catChips").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    state.category = chip.dataset.cat;
    renderChips();
    render();
    syncUrl();
  });
  $("#maxPrice").addEventListener("input", (e) => {
    state.maxPrice = Number(e.target.value);
    updatePriceLabel();
    render();
  });
  $("#inStock").addEventListener("change", (e) => { state.inStock = e.target.checked; render(); });
  $("#sort").addEventListener("change", (e) => { state.sort = e.target.value; render(); });

  function reset() {
    Object.assign(state, { q: "", category: "", maxPrice: priceCeiling, inStock: false, sort: "featured" });
    $("#search").value = "";
    $("#inStock").checked = false;
    $("#sort").value = "featured";
    $("#maxPrice").value = priceCeiling;
    updatePriceLabel();
    renderChips();
    render();
    syncUrl();
  }
  $("#reset").addEventListener("click", reset);
  $("#grid").addEventListener("click", (e) => { if (e.target.closest("[data-reset]")) reset(); });

  $("#filtersToggle").addEventListener("click", () => {
    document.querySelector(".filters").classList.toggle("expanded");
  });

  $("#helpForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $("#helpInput").value.trim();
    if (!text) { $("#helpInput").focus(); return; }
    $("#helpInput").value = "";
    window.ShopChat.ask(text);
  });

  // ---------- product popup ----------
  let current = null;
  function openProduct(p) {
    current = p;
    $("#pmThumb").innerHTML = productImage(p);
    $("#pmId").textContent = "#" + p.id;
    $("#pmCat").textContent = p.category;
    $("#pmName").textContent = p.name;
    $("#pmDesc").textContent = p.description || "";
    $("#pmPrice").textContent = money(p.price);
    $("#pmStock").innerHTML = stockLabel(p.stock);
    const inStock = Number(p.stock) > 0;
    $("#pmBuy").disabled = !inStock;
    $("#pmBuy").textContent = inStock ? "Buy with ShopBot" : "Out of stock";
    $("#productModal").hidden = false;
    $("#pmClose").focus();
  }
  function closeProduct() { $("#productModal").hidden = true; current = null; }

  $("#grid").addEventListener("click", (e) => {
    const buy = e.target.closest(".buy");
    if (buy) { window.ShopChat.ask(buyMessage(buy.dataset.name, buy.dataset.id)); return; }
    const card = e.target.closest(".product");
    if (card) {
      const p = products.find((x) => String(x.id) === card.dataset.id);
      if (p) openProduct(p);
    }
  });
  $("#grid").addEventListener("keydown", (e) => {
    const card = e.target.closest(".product");
    if (card && e.target === card && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      const p = products.find((x) => String(x.id) === card.dataset.id);
      if (p) openProduct(p);
    }
  });
  $("#pmClose").addEventListener("click", closeProduct);
  $("#productModal").addEventListener("click", (e) => { if (e.target.id === "productModal") closeProduct(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#productModal").hidden) closeProduct(); });
  $("#pmBuy").addEventListener("click", () => {
    if (!current) return;
    const p = current;
    closeProduct();
    window.ShopChat.ask(buyMessage(p.name, p.id));
  });
  $("#pmAsk").addEventListener("click", () => {
    if (!current) return;
    const p = current;
    closeProduct();
    window.ShopChat.prefill("About the " + p.name + " (id " + p.id + "): ");
  });

  // ---------- data ----------
  async function load() {
    try {
      products = await api("/products");
      banner(null);
    } catch (err) {
      banner(err.message);
      $("#grid").innerHTML = '<div class="empty">Products could not be loaded.</div>';
      return;
    }
    setupSlider();
    renderChips();
    render();
    if (current) { // refresh an open popup after stock changes
      const fresh = products.find((x) => String(x.id) === String(current.id));
      if (fresh) openProduct(fresh);
    }
  }

  window.addEventListener("shopchat:stock-changed", load);
  renderChips();
  load().then(() => {
    if (openProductId) {
      const p = products.find((x) => String(x.id) === openProductId);
      if (p) openProduct(p);
    }
  });
})();

// Home page: category tiles and "Popular right now".
(function () {
  const { api, esc, CATEGORIES, categoryEmoji, productCard, buyMessage, banner } = window.SB;
  const $ = (s) => document.querySelector(s);
  let products = [];

  document.getElementById("tryGift").addEventListener("click", () => {
    window.ShopChat.ask("I need a gift under Rs 4,000");
  });

  function renderCategories() {
    const counts = {};
    products.forEach((p) => { counts[String(p.category).toLowerCase()] = (counts[String(p.category).toLowerCase()] || 0) + 1; });
    $("#categories").innerHTML = CATEGORIES.map((c) => {
      const n = counts[c.toLowerCase()] || 0;
      return '<a class="cat-tile" href="shop.html?category=' + encodeURIComponent(c) + '">' +
        '<span class="cat-emoji">' + categoryEmoji(c) + "</span>" +
        "<strong>" + esc(c) + "</strong>" +
        '<span class="muted small">' + n + " item" + (n === 1 ? "" : "s") + "</span></a>";
    }).join("");
  }

  // No sales data on the client, so "popular" = in-stock items, one per category first.
  function pickPopular() {
    const inStock = products.filter((p) => Number(p.stock) > 0);
    const picked = [];
    const seen = new Set();
    inStock.forEach((p) => {
      const c = String(p.category).toLowerCase();
      if (picked.length < 4 && !seen.has(c)) { seen.add(c); picked.push(p); }
    });
    inStock.forEach((p) => { if (picked.length < 4 && !picked.includes(p)) picked.push(p); });
    return picked;
  }

  function renderPopular() {
    const list = pickPopular();
    $("#popular").innerHTML = list.length
      ? list.map(productCard).join("")
      : '<div class="empty">No products available right now.</div>';
  }

  $("#popular").addEventListener("click", (e) => {
    const buy = e.target.closest(".buy");
    if (buy) { window.ShopChat.ask(buyMessage(buy.dataset.name, buy.dataset.id)); return; }
    const card = e.target.closest(".product");
    if (card) location.href = "shop.html?product=" + encodeURIComponent(card.dataset.id);
  });
  $("#popular").addEventListener("keydown", (e) => {
    const card = e.target.closest(".product");
    if (card && e.key === "Enter" && e.target === card) location.href = "shop.html?product=" + encodeURIComponent(card.dataset.id);
  });

  async function load() {
    try {
      products = await api("/products");
      banner(null);
    } catch (err) {
      products = [];
      banner(err.message);
      $("#popular").innerHTML = '<div class="empty">Products could not be loaded.</div>';
      renderCategories();
      return;
    }
    renderCategories();
    renderPopular();
  }

  window.addEventListener("shopchat:stock-changed", load);
  renderCategories();
  load();
})();

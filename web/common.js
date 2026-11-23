// Shared helpers for every page (store, shop, chat widget, admin).
(function () {
  const API = String(window.SHOPBOT_API || "").replace(/\/+$/, "");

  class ApiError extends Error {
    constructor(status, message) {
      super(message || "Request failed (" + status + ")");
      this.status = status;
    }
  }

  // Every API value goes through esc() before it touches innerHTML.
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  async function api(path, opts) {
    opts = opts || {};
    const headers = Object.assign({}, opts.headers || {});
    const init = { method: opts.method || "GET", headers };
    if (opts.body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    let res;
    try {
      res = await fetch(API + path, init);
    } catch (e) {
      throw new ApiError(0, "Can't reach the ShopBot server. It may be waking up, so please try again in a minute.");
    }
    if (!res.ok) {
      let detail = "";
      try {
        const j = await res.json();
        detail = typeof j.detail === "string" ? j.detail : (j.error || j.message || "");
      } catch (_) { /* not JSON */ }
      throw new ApiError(res.status, detail || "Request failed (" + res.status + ")");
    }
    if (res.status === 204) return null;
    const type = res.headers.get("content-type") || "";
    return type.includes("json") ? res.json() : res.text();
  }

  const qs = (params) => {
    const p = new URLSearchParams();
    Object.keys(params).forEach((k) => {
      if (params[k] !== undefined && params[k] !== null && params[k] !== "") p.set(k, params[k]);
    });
    return p.toString();
  };

  const money = (n) => "Rs " + Math.round(Number(n) || 0).toLocaleString("en-US");

  function fmtDate(value) {
    if (!value) return "";
    const d = new Date(value);
    if (isNaN(d)) return String(value);
    return d.toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  const CATEGORIES = ["Clothing", "Footwear", "Accessories", "Electronics", "Home", "Beauty"];
  const CATEGORY_EMOJI = {
    clothing: "👕", footwear: "👟", accessories: "👜", electronics: "🎧", home: "🏠", beauty: "💄",
    shoes: "👟", fashion: "👗", bags: "👜", wallets: "👛", audio: "🎧", phones: "📱",
    kitchen: "🍳", decor: "🪴", skincare: "🧴", fragrance: "🌸", watches: "⌚", jewellery: "💍",
  };
  function categoryEmoji(category) {
    const key = String(category || "").toLowerCase().trim();
    if (CATEGORY_EMOJI[key]) return CATEGORY_EMOJI[key];
    const hit = Object.keys(CATEGORY_EMOJI).find((k) => key.includes(k));
    return hit ? CATEGORY_EMOJI[hit] : "🛍️";
  }

  // Bot replies: escape first, then allow **bold** and line breaks.
  function formatReply(text) {
    return esc(text)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\n/g, "<br>");
  }

  function statusBadge(status) {
    const s = String(status || "unknown").toLowerCase();
    return '<span class="badge badge-' + esc(s) + '">' + esc(s) + "</span>";
  }

  function stockLabel(stock) {
    stock = Number(stock) || 0;
    if (stock <= 0) return '<span class="stock stock-out">Out of stock</span>';
    return '<span class="stock' + (stock <= 5 ? " stock-low" : "") + '">' + esc(stock) + " in stock</span>";
  }

  // Product card used on the home page and the shop page.
  function productCard(p) {
    const inStock = (Number(p.stock) || 0) > 0;
    return '<article class="card product" data-id="' + esc(p.id) + '" tabindex="0">' +
      '<div class="thumb" aria-hidden="true">' + categoryEmoji(p.category) + "</div>" +
      '<div class="product-body">' +
        '<div class="product-meta"><span class="muted">#' + esc(p.id) + '</span><span class="tag">' + esc(p.category) + "</span></div>" +
        "<h3>" + esc(p.name) + "</h3>" +
        '<p class="desc">' + esc(p.description) + "</p>" +
        '<div class="product-foot"><span class="price">' + money(p.price) + "</span>" + stockLabel(p.stock) + "</div>" +
        '<button type="button" class="btn btn-primary btn-block buy" data-id="' + esc(p.id) + '" data-name="' + esc(p.name) + '"' +
          (inStock ? "" : " disabled") + ">" + (inStock ? "Buy with ShopBot" : "Out of stock") + "</button>" +
      "</div></article>";
  }

  const buyMessage = (name, id) => "I want to buy the " + name + " (id " + id + ")";

  const storage = (area) => ({
    get(k) { try { return window[area].getItem(k); } catch (_) { return null; } },
    set(k, v) { try { window[area].setItem(k, v); } catch (_) { /* storage blocked */ } },
    del(k) { try { window[area].removeItem(k); } catch (_) { /* storage blocked */ } },
  });

  function toast(message, kind) {
    let box = document.getElementById("toasts");
    if (!box) {
      box = document.createElement("div");
      box.id = "toasts";
      document.body.appendChild(box);
    }
    const el = document.createElement("div");
    el.className = "toast" + (kind ? " toast-" + kind : "");
    el.textContent = message;
    box.appendChild(el);
    setTimeout(() => el.remove(), 3500);
  }

  function banner(msg) {
    const b = document.getElementById("apiBanner");
    if (!b) return;
    if (msg) { b.textContent = "⚠️ " + msg; b.hidden = false; } else { b.hidden = true; }
  }

  window.SB = {
    API, ApiError, api, esc, qs, money, fmtDate, CATEGORIES, categoryEmoji, formatReply,
    statusBadge, stockLabel, productCard, buyMessage, storage, toast, banner,
  };
})();

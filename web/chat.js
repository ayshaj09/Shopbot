// ShopBot chat widget, shared by index.html and shop.html.
// If the page has <div id="chat-root"> the chat is built inside it,
// otherwise a floating "Ask ShopBot" bubble opens it.
(function () {
  const { api, esc, qs, fmtDate, formatReply, statusBadge, money, toast, banner } = window.SB;
  const local = window.SB.storage("localStorage");

  const USER_KEY = "shopbot.user";
  const threadKey = (uid) => "shopbot.thread." + uid;
  const SUGGESTIONS = [
    "Show me earbuds under 7000",
    "I need a gift for my mother under 4000",
    "Where is my order?",
  ];

  let user = null;
  let threadId = null;
  let sending = false;
  let pendingMessage = null;
  let floating = false;
  let root, panel, fab;
  const q = (sel) => panel.querySelector(sel);

  // ---------- identity ----------
  // Letters and digits are kept so that e.g. ali1@x.com and ali2@x.com stay different customers.
  const userIdFor = (email) => "u-" + email.toLowerCase().trim().replace(/[^a-z0-9]/g, "-");
  const newThreadId = () => "t-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);

  function loadUser() {
    try {
      const u = JSON.parse(local.get(USER_KEY) || "null");
      if (u && u.id && u.name) return u;
    } catch (_) { /* corrupted */ }
    return null;
  }

  function setUser(u) {
    user = u;
    if (u) {
      local.set(USER_KEY, JSON.stringify(u));
      threadId = local.get(threadKey(u.id));
      if (!threadId) { threadId = newThreadId(); local.set(threadKey(u.id), threadId); }
    } else {
      local.del(USER_KEY);
      threadId = null;
    }
    updateNav();
  }

  function updateNav() {
    document.querySelectorAll("[data-sc-user]").forEach((el) => {
      el.textContent = user ? "👋 " + user.name : "";
      el.hidden = !user;
    });
    document.querySelectorAll("[data-sc-switch]").forEach((el) => {
      el.textContent = user ? "Switch customer" : "Sign in";
    });
  }

  // ---------- markup ----------
  function buildPanel() {
    const el = document.createElement("section");
    el.className = "sc" + (floating ? " sc-floating" : "");
    el.setAttribute("aria-label", "ShopBot chat");
    el.innerHTML =
      '<div class="sc-head">' +
        '<div class="sc-title"><span class="sc-avatar">🤖</span><div><strong>ShopBot</strong>' +
        '<small class="sc-status">Your Bazaar Lane assistant</small></div></div>' +
        '<div class="sc-actions">' +
          '<button type="button" class="btn btn-ghost btn-sm sc-new">New chat</button>' +
          (floating ? '<button type="button" class="btn btn-ghost btn-sm sc-close" aria-label="Close chat">✕</button>' : "") +
        "</div>" +
      "</div>" +
      '<div class="sc-tabs" role="tablist">' +
        '<button type="button" class="sc-tab active" data-tab="chat">Chat</button>' +
        '<button type="button" class="sc-tab" data-tab="orders">My orders</button>' +
        '<button type="button" class="sc-tab" data-tab="memories">Remembered</button>' +
      "</div>" +
      '<div class="sc-pane sc-pane-chat" data-pane="chat">' +
        '<div class="sc-messages" aria-live="polite"></div>' +
        '<div class="sc-suggestions"></div>' +
        '<form class="sc-form">' +
          '<input class="input sc-input" autocomplete="off" maxlength="2000" placeholder="Ask about products, prices, orders…" aria-label="Message">' +
          '<button type="submit" class="btn btn-primary sc-send">Send</button>' +
        "</form>" +
      "</div>" +
      '<div class="sc-pane" data-pane="orders" hidden><div class="sc-list sc-orders"></div></div>' +
      '<div class="sc-pane" data-pane="memories" hidden>' +
        '<p class="muted small">Facts ShopBot saved to help you across chats.</p>' +
        '<div class="sc-list sc-memories"></div>' +
        '<button type="button" class="btn btn-danger btn-block sc-forget">Forget everything</button>' +
      "</div>";
    return el;
  }

  function buildSignin() {
    const m = document.createElement("div");
    m.className = "modal";
    m.id = "sc-signin";
    m.hidden = true;
    m.innerHTML =
      '<form class="modal-card">' +
        "<h2>Sign in to chat with ShopBot</h2>" +
        '<p class="muted">We use your email to keep your orders and chats together.</p>' +
        '<label>Name <input name="name" class="input" required maxlength="80" autocomplete="name"></label>' +
        '<label>Email <input name="email" class="input" type="email" required maxlength="120" autocomplete="email"></label>' +
        '<div class="row-end"><button type="button" class="btn btn-ghost sc-cancel">Not now</button>' +
        '<button type="submit" class="btn btn-primary">Start chatting</button></div>' +
      "</form>";
    document.body.appendChild(m);
    const form = m.querySelector("form");
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const name = form.elements.name.value.trim();
      const email = form.elements.email.value.trim().toLowerCase();
      if (!name || !email) return;
      m.hidden = true;
      setUser({ name, email, id: userIdFor(email) });
      resetChat();
      loadHistory().then(() => {
        if (pendingMessage) { const t = pendingMessage; pendingMessage = null; send(t); }
      });
      loadOrders();
      loadMemories();
    });
    m.querySelector(".sc-cancel").addEventListener("click", () => { m.hidden = true; pendingMessage = null; });
    m.addEventListener("click", (e) => { if (e.target === m) { m.hidden = true; pendingMessage = null; } });
    return m;
  }

  function showSignin() {
    const m = document.getElementById("sc-signin");
    const form = m.querySelector("form");
    form.reset();
    m.hidden = false;
    setTimeout(() => form.elements.name.focus(), 50);
  }

  // ---------- messages ----------
  function addMessage(role, text, tools) {
    const wrap = document.createElement("div");
    wrap.className = "msg " + (role === "user" ? "msg-user" : "msg-bot");
    let html = '<div class="bubble">' + (role === "user" ? esc(text).replace(/\n/g, "<br>") : formatReply(text)) + "</div>";
    if (tools && tools.length) {
      html += '<div class="tools"><span>🔧 tools used:</span>' + tools.map((t) => {
        const name = t && t.name ? t.name : String(t);
        const args = t && t.args ? JSON.stringify(t.args) : "";
        return '<span class="tool" title="' + esc(args) + '">' + esc(name) + "</span>";
      }).join("") + "</div>";
    }
    wrap.innerHTML = html;
    q(".sc-messages").appendChild(wrap);
    scrollDown();
  }

  function scrollDown() { const m = q(".sc-messages"); m.scrollTop = m.scrollHeight; }

  function resetChat() {
    q(".sc-messages").innerHTML = "";
    addMessage("assistant", "Assalam o Alaikum" + (user ? ", " + user.name : "") +
      "! I'm ShopBot. Tell me what you need and I'll find it, compare options, place your order and track it for you.");
    renderSuggestions(true);
  }

  function renderSuggestions(show) {
    q(".sc-suggestions").innerHTML = show
      ? SUGGESTIONS.map((s) => '<button type="button" class="chip" data-text="' + esc(s) + '">' + esc(s) + "</button>").join("")
      : "";
  }

  function setTyping(on) {
    const box = q(".sc-messages");
    let t = box.querySelector(".typing-row");
    if (on && !t) {
      t = document.createElement("div");
      t.className = "msg msg-bot typing-row";
      t.innerHTML = '<div class="bubble typing"><span></span><span></span><span></span></div>';
      box.appendChild(t);
      scrollDown();
    } else if (!on && t) {
      t.remove();
    }
    q(".sc-status").textContent = on ? "ShopBot is typing…" : "Your Bazaar Lane assistant";
  }

  async function loadHistory() {
    if (!user) return;
    const uid = user.id, tid = threadId;
    try {
      const history = await api("/me/history?" + qs({ user_id: uid, thread_id: tid }));
      if (!user || user.id !== uid || threadId !== tid) return; // switched meanwhile
      if (history && history.length) {
        q(".sc-messages").innerHTML = "";
        history.forEach((m) => addMessage(m.role, m.text));
        renderSuggestions(false);
      }
    } catch (err) {
      if (err.status === 0) banner(err.message);
    }
  }

  async function send(text) {
    text = String(text || "").trim();
    if (!text) return;
    if (sending) { toast("ShopBot is still replying. Please wait a moment.", "error"); return; }
    if (!user) { pendingMessage = text; showSignin(); return; }
    const uid = user.id, tid = threadId;   // a reply for another customer/chat must not show here
    sending = true;
    q(".sc-send").disabled = true;
    renderSuggestions(false);
    addMessage("user", text);
    setTyping(true);
    try {
      const res = await api("/chat", { method: "POST", body: { user_id: uid, thread_id: tid, message: text } });
      setTyping(false);
      if (!user || user.id !== uid || threadId !== tid) return;   // switched customer or started a new chat meanwhile
      banner(null);
      const tools = res.tool_calls || [];
      addMessage("assistant", res.reply || "(no reply)", tools);
      const names = tools.map((t) => (t && t.name) || String(t));
      if (names.includes("confirm_action")) {
        window.dispatchEvent(new CustomEvent("shopchat:stock-changed"));
        loadOrders();
      } else if (names.some((n) => /order/i.test(n))) {
        loadOrders();
      }
      // remember / forget_me change what the "Remembered" tab shows
      if (names.some((n) => /memor|remember|forget/i.test(n))) loadMemories();
    } catch (err) {
      setTyping(false);
      if (!user || user.id !== uid || threadId !== tid) return;
      addMessage("assistant", err.status === 0
        ? "Sorry, I can't reach the store server right now. Please try again in a minute."
        : "Sorry, something went wrong (" + err.message + "). Please try again.");
      if (err.status === 0) banner(err.message);
    } finally {
      sending = false;
      q(".sc-send").disabled = false;
      if (!floating || panel.classList.contains("open")) q(".sc-input").focus({ preventScroll: true });
    }
  }

  // ---------- orders & memories ----------
  async function loadOrders() {
    const box = q(".sc-orders");
    if (!user) { box.innerHTML = '<div class="empty">Sign in to see your orders.</div>'; return; }
    box.innerHTML = '<div class="empty">Loading…</div>';
    try {
      const orders = await api("/me/orders?" + qs({ user_id: user.id }));
      if (!orders.length) { box.innerHTML = '<div class="empty">No orders yet. Ask ShopBot to help you buy something!</div>'; return; }
      box.innerHTML = orders.map((o) =>
        '<article class="order">' +
          '<div class="order-head"><strong>Order #' + esc(o.id) + "</strong>" + statusBadge(o.status) + "</div>" +
          '<div class="muted small">' + esc(fmtDate(o.created_at)) + "</div>" +
          '<ul class="items">' + (o.items || []).map((i) =>
            "<li><span>" + esc(i.quantity) + " × " + esc(i.name) + "</span><span>" + money((Number(i.unit_price) || 0) * (Number(i.quantity) || 0)) + "</span></li>").join("") +
          "</ul>" +
          '<div class="order-foot"><span class="muted small">📦 ' + esc(o.shipping_address) + "</span><strong>" + money(o.total) + "</strong></div>" +
        "</article>").join("");
    } catch (err) {
      box.innerHTML = '<div class="empty">Couldn\'t load your orders: ' + esc(err.message) + "</div>";
    }
  }

  async function loadMemories() {
    const box = q(".sc-memories");
    const btn = q(".sc-forget");
    if (!user) { box.innerHTML = '<div class="empty">Sign in to see what ShopBot remembers.</div>'; btn.disabled = true; return; }
    try {
      const mems = await api("/me/memories?" + qs({ user_id: user.id }));
      btn.disabled = !mems.length;
      box.innerHTML = mems.length
        ? '<ul class="memory-list">' + mems.map((m) =>
            "<li><span>🧠 " + esc(m.fact) + '</span><span class="muted small">' + esc(fmtDate(m.saved)) + "</span></li>").join("") + "</ul>"
        : '<div class="empty">ShopBot doesn\'t remember anything about you yet.</div>';
    } catch (err) {
      box.innerHTML = '<div class="empty">Couldn\'t load memories: ' + esc(err.message) + "</div>";
    }
  }

  let forgetArmed = null;
  async function forget() {
    const btn = q(".sc-forget");
    if (!forgetArmed) {
      btn.textContent = "Click again to confirm";
      forgetArmed = setTimeout(() => { forgetArmed = null; btn.textContent = "Forget everything"; }, 4000);
      return;
    }
    clearTimeout(forgetArmed);
    forgetArmed = null;
    btn.textContent = "Forget everything";
    try {
      await api("/me/memories?" + qs({ user_id: user.id }), { method: "DELETE" });
      toast("ShopBot has forgotten everything about you.", "ok");
      loadMemories();
    } catch (err) {
      toast("Couldn't clear memories: " + err.message, "error");
    }
  }

  // ---------- tabs, open/close ----------
  function showTab(name) {
    panel.querySelectorAll(".sc-tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    panel.querySelectorAll(".sc-pane").forEach((p) => { p.hidden = p.dataset.pane !== name; });
    if (name === "orders") loadOrders();
    if (name === "memories") loadMemories();
    if (name === "chat") scrollDown();
  }

  function open(tab) {
    if (floating) {
      panel.classList.add("open");
      fab.hidden = true;
      document.body.classList.add("sc-open");
    } else {
      const r = panel.getBoundingClientRect();
      if (r.top < 0 || r.bottom > window.innerHeight) panel.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    showTab(tab || "chat");
  }

  function close() {
    if (!floating) return;
    panel.classList.remove("open");
    fab.hidden = false;
    document.body.classList.remove("sc-open");
  }

  function wire() {
    panel.querySelector(".sc-form").addEventListener("submit", (e) => {
      e.preventDefault();
      const input = q(".sc-input");
      const text = input.value;
      input.value = "";
      send(text);
    });
    q(".sc-suggestions").addEventListener("click", (e) => {
      const chip = e.target.closest(".chip");
      if (chip) send(chip.dataset.text);
    });
    panel.querySelectorAll(".sc-tab").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
    q(".sc-new").addEventListener("click", () => {
      if (!user) { showSignin(); return; }
      threadId = newThreadId();
      local.set(threadKey(user.id), threadId);
      showTab("chat");
      resetChat();
      q(".sc-input").focus();
    });
    q(".sc-forget").addEventListener("click", forget);
    if (floating) {
      fab.addEventListener("click", () => { open("chat"); q(".sc-input").focus(); });
      q(".sc-close").addEventListener("click", close);
      document.addEventListener("keydown", (e) => { if (e.key === "Escape" && panel.classList.contains("open")) close(); });
    }

    // Nav hooks available on any page.
    document.querySelectorAll("[data-sc-switch]").forEach((b) => b.addEventListener("click", (e) => {
      e.preventDefault();
      if (user) {
        setUser(null);
        resetChat();
        loadOrders();
        loadMemories();
      }
      showSignin();
    }));
    document.querySelectorAll("[data-sc-open]").forEach((a) => a.addEventListener("click", (e) => {
      e.preventDefault();
      open(a.dataset.scOpen || "chat");
    }));
  }

  function mount() {
    root = document.getElementById("chat-root");
    floating = !root;
    panel = buildPanel();
    if (floating) {
      fab = document.createElement("button");
      fab.type = "button";
      fab.className = "sc-fab";
      fab.innerHTML = "💬 <span>Ask ShopBot</span>";
      document.body.appendChild(fab);
      document.body.appendChild(panel);
    } else {
      root.appendChild(panel);
    }
    buildSignin();
    wire();
    setUser(loadUser());
    resetChat();
    loadHistory();
    if (location.hash === "#orders") open("orders");
  }

  window.ShopChat = {
    ask(text) { open("chat"); send(text); },
    prefill(text) {
      open("chat");
      const input = q(".sc-input");
      input.value = text;
      input.focus();
      input.setSelectionRange(text.length, text.length);
    },
    open,
    close,
    user: () => user,
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();

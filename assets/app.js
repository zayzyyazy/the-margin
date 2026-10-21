// Saved reading: a list of articles kept in this browser's localStorage. Nothing leaves the device.
(() => {
  const KEY = "margin.saved.v1";
  const load = () => { try { return JSON.parse(localStorage.getItem(KEY)) || []; } catch { return []; } };
  const store = (list) => { try { localStorage.setItem(KEY, JSON.stringify(list)); return true; } catch { return false; } };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  function sync() {
    const ids = new Set(load().map((x) => x.id));
    document.querySelectorAll("button.save").forEach((b) => {
      const on = ids.has(JSON.parse(b.dataset.item).id);
      b.setAttribute("aria-pressed", on);
      b.textContent = on ? "Saved" : "Save";
    });
  }

  document.addEventListener("click", (e) => {
    const b = e.target.closest("button.save");
    if (!b) return;
    const item = JSON.parse(b.dataset.item);
    let list = load();
    list = list.some((x) => x.id === item.id) ? list.filter((x) => x.id !== item.id) : [{ ...item, saved_at: new Date().toISOString() }, ...list];
    if (!store(list)) { b.textContent = "Can’t save here"; return; }
    sync();
    if (document.getElementById("saved-list")) render();
  });

  function render() {
    const el = document.getElementById("saved-list");
    const list = load();
    if (!list.length) { el.innerHTML = '<p class="empty">Nothing saved yet. Use “Save” on any article to keep it here.</p>'; return; }
    el.innerHTML = list.map((i) => {
      const d = new Date(i.published);
      return `<article class="story"><div class="rail"><span class="strand">Saved ${esc(new Date(i.saved_at).toLocaleDateString("en-GB", { day: "numeric", month: "short" }))}</span></div>
<div class="txt"><p class="meta"><span class="src">${esc(i.source)}</span> · ${esc(d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }))}</p>
<h2><a href="${esc(i.link)}">${esc(i.title)}</a></h2><p class="foot"><a class="go" href="${esc(i.link)}">Read at ${esc(i.source)} <span aria-hidden="true">→</span></a>
<button class="save" type="button" data-item="${esc(JSON.stringify(i))}" aria-pressed="true">Saved</button></p></div></article>`;
    }).join("");
  }

  if (document.getElementById("saved-list")) render();
  sync();
})();

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
  document.addEventListener("margin:paged", sync);
})();

// Paged reading mode: flows the edition's blocks into numbered pages that fit the screen. Two pages side by side when the
// window is wide enough. Buttons, ←/→ and PageUp/PageDown, swipe, #p=N links. Phones default to scrolling; the choice is remembered.
(() => {
  const paper = document.querySelector(".paper");
  if (!paper) return;
  const MODE = "margin.mode.v1";
  const getMode = () => { try { return localStorage.getItem(MODE); } catch { return null; } };
  const setMode = (m) => { try { localStorage.setItem(MODE, m); } catch {} };
  const original = paper.cloneNode(true);
  const label = paper.dataset.label || "The Margin";
  let book = null, pages = [], at = 0, spread = false;

  const bar = document.createElement("div");
  bar.className = "reader-bar";
  bar.innerHTML = `<button type="button" class="rb-prev" aria-label="Previous page">←</button>
<span class="rb-pos" aria-live="polite"></span><button type="button" class="rb-next" aria-label="Next page">→</button>
<button type="button" class="rb-mode"></button>`;
  paper.before(bar);

  const wantPages = () => (getMode() || (innerWidth >= 760 ? "pages" : "scroll")) === "pages";

  function el(tag, cls) { const e = document.createElement(tag); if (cls) e.className = cls; return e; }

  function newPage() {
    const pg = el("section", "pg"); const inner = el("div", "pg-in"); const foot = el("footer", "pg-foot");
    pg.append(inner, foot); book.append(pg); pages.push(pg); return inner;
  }

  function paginate() {
    const keep = currentAnchor();
    if (book) book.remove();
    paper.hidden = true;
    book = el("div", "book"); pages = [];
    spread = innerWidth >= 1100 && innerWidth / innerHeight > 1.25;
    book.classList.toggle("spread", spread);
    bar.after(book);
    // page height = what is actually left of the window below the reader bar, so the page foot is never cut off
    const top = Math.max(bar.getBoundingClientRect().bottom, 0) + 18;
    book.style.setProperty("--ph", `${Math.max(420, innerHeight - (scrollY ? 70 : top) - 28)}px`);
    const blocks = [...original.querySelectorAll(".blk")].map((b) => b.cloneNode(true));
    let inner = newPage();
    for (const b of blocks) {
      const used = inner.scrollHeight / inner.clientHeight;
      const isSec = b.classList.contains("sechead") || b.classList.contains("end");
      // a new section starts a new page unless the current one is still mostly empty: no stretched, half-blank pages
      if (inner.childElementCount && isSec && used > 0.4) inner = newPage();
      inner.append(b);
      if (inner.scrollHeight > inner.clientHeight + 1 && inner.childElementCount > 1) {
        b.remove();
        const prev = inner.lastElementChild;
        inner = newPage();
        if (prev && prev.classList.contains("sechead")) inner.append(prev);   // never strand a section heading at a page foot
        inner.append(b);
      }
      if (inner.scrollHeight > inner.clientHeight + 1) inner.classList.add("tall");   // one block taller than a page scrolls inside it
    }
    pages.forEach((p, k) => { p.querySelector(".pg-foot").innerHTML = `<span>${label}</span><span>${k + 1}${k === pages.length - 1 ? " · the end" : ""}</span>`; p.id = `p${k + 1}`; });
    // contents: real page numbers
    book.querySelectorAll("[data-toc]").forEach((a) => {
      const target = book.querySelector(`#s-${a.dataset.toc}`);
      const k = target ? pages.indexOf(target.closest(".pg")) : -1;
      if (k >= 0) { a.querySelector(".pn").textContent = k + 1; a.href = `#p=${k + 1}`; }
    });
    const fromHash = parseInt((location.hash.match(/p=(\d+)/) || [])[1], 10);
    const back = anchorId ? book.querySelector(`#${CSS.escape(anchorId)}`) : null;
    show(fromHash ? fromHash - 1 : back ? pages.indexOf(back.closest(".pg")) : 0, false);
    document.dispatchEvent(new Event("margin:paged"));
  }

  let anchorId = null;
  function currentAnchor() {
    if (book && pages[at]) anchorId = pages[at].querySelector(".blk[id]")?.id || anchorId;
    return null;
  }

  function show(k, push = true) {
    const step = spread ? 2 : 1;
    k = Math.max(0, Math.min(k, pages.length - 1));
    if (spread) k -= k % 2;
    at = k;
    pages.forEach((p, i) => { p.hidden = !(i >= k && i < k + step); });
    const last = Math.min(k + step, pages.length);
    bar.querySelector(".rb-pos").textContent = step === 2 && last > k + 1 ? `Pages ${k + 1}–${last} of ${pages.length}` : `Page ${k + 1} of ${pages.length}`;
    bar.querySelector(".rb-prev").disabled = k === 0;
    bar.querySelector(".rb-next").disabled = k + step >= pages.length;
    if (push) history.replaceState(null, "", `#p=${k + 1}`);
    window.scrollTo(0, 0);
  }
  const step = () => (spread ? 2 : 1);

  function scrollMode() {
    if (book) { book.remove(); book = null; pages = []; }
    paper.hidden = false;
    bar.classList.add("scrolling");
    bar.querySelector(".rb-mode").textContent = "Read as pages";
    if (location.hash.startsWith("#p=")) history.replaceState(null, "", location.pathname);
  }

  function apply() {
    if (wantPages()) { bar.classList.remove("scrolling"); bar.querySelector(".rb-mode").textContent = "Scroll instead"; paginate(); }
    else scrollMode();
  }

  bar.querySelector(".rb-prev").addEventListener("click", () => show(at - step()));
  bar.querySelector(".rb-next").addEventListener("click", () => show(at + step()));
  bar.querySelector(".rb-mode").addEventListener("click", () => { setMode(book ? "scroll" : "pages"); apply(); });
  document.addEventListener("keydown", (e) => {
    if (!book || e.altKey || e.ctrlKey || e.metaKey || /input|textarea|select/i.test(e.target.tagName)) return;
    if (["ArrowRight", "PageDown"].includes(e.key) || (e.key === " " && !e.shiftKey)) { e.preventDefault(); show(at + step()); }
    if (["ArrowLeft", "PageUp"].includes(e.key) || (e.key === " " && e.shiftKey)) { e.preventDefault(); show(at - step()); }
    if (e.key === "Home") show(0);
    if (e.key === "End") show(pages.length - 1);
  });
  let tx = null, ty = null;
  document.addEventListener("touchstart", (e) => { if (book) { tx = e.touches[0].clientX; ty = e.touches[0].clientY; } }, { passive: true });
  document.addEventListener("touchend", (e) => {
    if (!book || tx === null) return;
    const dx = e.changedTouches[0].clientX - tx, dy = e.changedTouches[0].clientY - ty;
    if (Math.abs(dx) > 50 && Math.abs(dx) > 1.5 * Math.abs(dy)) show(at + (dx < 0 ? step() : -step()));
    tx = null;
  });
  document.addEventListener("click", (e) => {
    const a = e.target.closest('a[href^="#p="]');
    if (a && book) { e.preventDefault(); show(parseInt(a.getAttribute("href").slice(3), 10) - 1); }
  });
  let rt;
  addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { if (book) paginate(); }, 200); });
  (document.fonts ? document.fonts.ready : Promise.resolve()).then(apply);
})();

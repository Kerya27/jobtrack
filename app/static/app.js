// Small progressive enhancements on top of HTMX. Everything also works without this file
// (forms post normally, the "Today" chip is a link, the board has a status select per card).
(function () {
  "use strict";

  // ---------- bulk selection ----------
  var lastClicked = null;

  function rowBoxes() {
    return Array.prototype.slice.call(document.querySelectorAll('input[type="checkbox"][form="bulk"]'));
  }

  function updateBulk() {
    var bar = document.getElementById("bulk");
    if (!bar) return;
    var boxes = rowBoxes();
    var count = boxes.filter(function (b) { return b.checked; }).length;
    bar.hidden = count === 0;
    var label = bar.querySelector("[data-bulk-count]");
    if (label) label.textContent = label.dataset.template.replace("{count}", count);
    boxes.forEach(function (b) {
      var row = b.closest(".app-row");
      if (row) row.classList.toggle("selected", b.checked);
    });
  }

  document.addEventListener("click", function (e) {
    var target = e.target;
    if (!(target instanceof Element)) return;

    // Shift+click selects a range of rows, like in a mail client.
    if (target.matches('input[type="checkbox"][form="bulk"]')) {
      var boxes = rowBoxes();
      if (e.shiftKey && lastClicked && boxes.indexOf(lastClicked) !== -1) {
        var a = boxes.indexOf(lastClicked), b = boxes.indexOf(target);
        boxes.slice(Math.min(a, b), Math.max(a, b) + 1).forEach(function (box) {
          box.checked = target.checked;
        });
      }
      lastClicked = target;
      updateBulk();
      return;
    }
    if (target.closest("[data-bulk-all]")) {
      rowBoxes().forEach(function (box) { box.checked = true; });
      updateBulk();
      return;
    }
    if (target.closest("[data-bulk-clear]")) {
      rowBoxes().forEach(function (box) { box.checked = false; });
      updateBulk();
      return;
    }

    // The "Today 3/5" chip toggles the "Applied: today" filter instead of reloading the page.
    var chip = target.closest("[data-sent-toggle]");
    var sent = document.getElementById("f-sent");
    if (chip && sent && window.htmx) {
      e.preventDefault();
      sent.value = sent.value === "today" ? "" : "today";
      sent.dispatchEvent(new Event("change", { bubbles: true }));
    }
  });

  // "All" also clears the "Today" filter. Capture phase: runs before HTMX reads the form,
  // so the select in the Filters panel stays in sync with what the server gets.
  document.addEventListener("change", function (e) {
    var target = e.target;
    var sent = document.getElementById("f-sent");
    if (target instanceof Element && target.matches("[data-all-chip]") && sent && sent.value === "today") {
      sent.value = "";
    }
  }, true);

  document.addEventListener("htmx:afterSwap", updateBulk);
  document.addEventListener("htmx:afterRequest", function (e) {
    var form = e.detail && e.detail.elt;
    if (form && form.id === "bulk" && e.detail.successful) {
      var text = form.querySelector('[name="bulk_text"]');
      if (text) text.value = "";
    }
  });
  document.addEventListener("DOMContentLoaded", updateBulk);

  // ---------- board: drag & drop between status columns ----------
  var dragged = null;

  function columnAt(node) {
    return node instanceof Element ? node.closest("[data-board] .board-col") : null;
  }

  document.addEventListener("dragstart", function (e) {
    var card = e.target instanceof Element ? e.target.closest('.board-card[draggable="true"]') : null;
    if (!card) return;
    dragged = card;
    card.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", card.dataset.id);
  });

  document.addEventListener("dragend", function () {
    if (dragged) dragged.classList.remove("dragging");
    dragged = null;
    document.querySelectorAll(".board-col.drop").forEach(function (c) { c.classList.remove("drop"); });
  });

  document.addEventListener("dragover", function (e) {
    var col = columnAt(e.target);
    if (!col || !dragged) return;
    e.preventDefault();
    col.classList.add("drop");
  });

  document.addEventListener("dragleave", function (e) {
    var col = columnAt(e.target);
    if (col && !col.contains(e.relatedTarget)) col.classList.remove("drop");
  });

  document.addEventListener("drop", function (e) {
    var col = columnAt(e.target);
    if (!col || !dragged || !window.htmx) return;
    e.preventDefault();
    col.classList.remove("drop");
    var card = dragged;
    if (card.closest(".board-col") === col) return;
    col.querySelector(".board-cards").prepend(card); // optimistic; the response re-renders the board
    var values = {};
    var filters = document.getElementById("filters");
    if (filters) new FormData(filters).forEach(function (v, k) { values[k] = v; });
    values.view = "board";
    values.new_status = col.dataset.status;
    window.htmx.ajax("POST", "/applications/" + card.dataset.id + "/status", {
      target: "#results", source: card, values: values
    });
  });

  // ---------- "Save from page": the window opened by the browser button ----------
  function startCapture() {
    var box = document.getElementById("capture");
    if (!box) return;
    var status = document.getElementById("capture-status");
    var target = document.getElementById("new-form");
    var token = document.querySelector('meta[name="csrf-token"]').content;
    var done = false;

    function show(html) {
      target.innerHTML = html;
      if (window.htmx) window.htmx.process(target);
      status.hidden = true;
    }

    function fallback() {
      if (done) return;
      done = true;
      status.textContent = box.dataset.fallback;
      status.className = "flash flash-warning";
      status.removeAttribute("aria-busy");
      fetch("/applications/new?mode=text&url=" + encodeURIComponent(box.dataset.url || ""), {
        headers: { "HX-Request": "true" }
      }).then(function (r) { return r.text(); }).then(function (html) {
        target.innerHTML = html;
        if (window.htmx) window.htmx.process(target);
        var area = target.querySelector("textarea");
        if (area) area.focus();
      });
    }

    window.addEventListener("message", function (e) {
      // Only the window that opened us (the vacancy page) may send the page, once.
      if (done || e.source !== window.opener || !e.data || e.data.type !== "jobtrack-page") return;
      done = true;
      status.textContent = box.dataset.reading;
      fetch("/applications/capture", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": token, "HX-Request": "true" },
        body: JSON.stringify({
          url: String(e.data.url || ""), html: String(e.data.html || ""), text: String(e.data.text || "")
        })
      }).then(function (r) {
        if (!r.ok && r.status !== 422) throw new Error("HTTP " + r.status);
        return r.text();
      }).then(show).catch(function () { done = false; fallback(); });
    });

    if (window.opener) {
      window.opener.postMessage({ type: "jobtrack-ready" }, "*"); // just a ping, carries no data
      setTimeout(fallback, 4000);
    } else {
      fallback(); // opened directly, or the site cut the link to its pop-ups (COOP)
    }
  }
  document.addEventListener("DOMContentLoaded", startCapture);

  // Color theme: stored locally, does not go to the server.
  function currentTheme() {
    var stored = null;
    try { stored = localStorage.getItem("jt-theme"); } catch (e) {}
    if (stored === "light" || stored === "dark") return stored;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  document.addEventListener("click", function (e) {
    var btn = e.target instanceof Element ? e.target.closest("[data-theme-toggle]") : null;
    if (!btn) return;
    var next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("jt-theme", next); } catch (err) {}
  });

  // If a status change fails, reload so the board never shows a state that wasn't saved.
  document.addEventListener("htmx:responseError", function (e) {
    var target = e.detail && e.detail.target;
    if (target && target.id === "results") window.location.reload();
  });
})();

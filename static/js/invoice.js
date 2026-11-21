/* Billing screen. All figures here are a PREVIEW; the server recalculates everything on save. */
(function () {
  "use strict";
  var init = JSON.parse(document.getElementById("init-data").textContent);
  var cfg = { override: !!init.canOverride };
  var tbody = document.querySelector("#lines tbody");
  var empty = document.getElementById("no-lines");
  var search = document.getElementById("product-search");
  var results = document.getElementById("search-results");
  var payRows = document.getElementById("pay-rows");
  var active = -1, timer = null, current = [], searchVersion = 0;
  var METHODS = JSON.parse(document.getElementById("pay-methods").textContent);

  function money(n) { return (Math.round((n + Number.EPSILON) * 100) / 100).toFixed(2); }
  function num(v) { var n = parseFloat(v); return isNaN(n) ? 0 : n; }
  function r2(n) { return Math.round((n + Number.EPSILON) * 100) / 100; }

  function addLine(p) {
    var existing = tbody.querySelector('tr[data-id="' + p.id + '"]');
    if (existing) { var q = existing.querySelector(".qty"); q.value = num(q.value) + 1; recalc(); return; }
    var tr = document.createElement("tr");
    tr.dataset.id = p.id; tr.dataset.tax = p.tax_rate || "0";
    var priceCell = cfg.override
      ? '<input class="price" name="unit_price" type="number" min="0" step="0.01" aria-label="Unit price">'
      : '<span class="price-text"></span>';
    tr.innerHTML =
      '<td><input type="hidden" name="product_id"><strong class="nm"></strong><br><small class="stk"></small></td>' +
      '<td class="num"><input class="qty" name="quantity" type="number" min="1" step="1" aria-label="Quantity" style="width:80px"></td>' +
      '<td class="money">' + priceCell + '</td>' +
      '<td class="money"><input class="disc" name="discount" type="number" min="0" step="0.01" aria-label="Line discount" style="width:90px"></td>' +
      '<td class="money tax"><span class="rate"></span><br><small class="tax-amount"></small></td>' +
      '<td class="money amt">0.00</td>' +
      '<td><button type="button" class="btn small rm" aria-label="Remove line">✕</button></td>';
    tr.querySelector('[name=product_id]').value = p.id;
    tr.querySelector(".nm").textContent = p.name;
    tr.querySelector(".stk").textContent = "In stock: " + (p.stock != null ? p.stock : "?");
    tr.querySelector(".rate").textContent = (p.tax_rate || "0") + "%";
    tr.dataset.price = p.price;
    tr.querySelector(".qty").value = p.quantity || 1;
    tr.querySelector(".disc").value = p.discount || 0;
    if (cfg.override) { tr.querySelector(".price").value = p.price; } else { tr.querySelector(".price-text").textContent = money(num(p.price)); }
    tbody.appendChild(tr); recalc();
  }

  function recalc() {
    var rows = tbody.querySelectorAll("tr"), sub = 0, nets = [];
    empty.hidden = rows.length > 0;
    rows.forEach(function (tr) {
      var q = num(tr.querySelector(".qty").value);
      var price = cfg.override ? num(tr.querySelector(".price").value) : num(tr.dataset.price);
      var net = Math.max(r2(q * price) - num(tr.querySelector(".disc").value), 0);
      nets.push([net, num(tr.dataset.tax), tr]); sub += net;
    });
    sub = r2(sub);
    var disc = r2(sub * num(document.getElementById("discount_percent").value) / 100), tax = 0;
    var remaining = disc, remainingNet = sub;
    nets.forEach(function (n, index) {
      var share = index === nets.length - 1 ? remaining : (sub ? Math.min(r2(disc * n[0] / sub), remaining) : 0);
      remainingNet = r2(remainingNet - n[0]);
      share = Math.max(Math.min(share, n[0]), r2(remaining - remainingNet));
      remaining = r2(remaining - share);
      var lineTax = r2((n[0] - share) * n[1] / 100);
      tax += lineTax;
      n[2].querySelector(".tax-amount").textContent = money(lineTax);
      n[2].querySelector(".amt").textContent = money(r2(n[0] - share + lineTax));
    });
    var ship = num(document.getElementById("shipping_charge").value);
    var total = r2(sub - disc + tax + ship);
    document.getElementById("t-sub").textContent = money(sub); document.getElementById("t-disc").textContent = money(disc);
    document.getElementById("t-tax").textContent = money(tax); document.getElementById("t-ship").textContent = money(ship);
    document.getElementById("t-total").textContent = money(total);
    var paid = 0; payRows.querySelectorAll(".pay-amt").forEach(function (i) { paid += num(i.value); });
    document.getElementById("t-paid").textContent = money(paid); document.getElementById("t-due").textContent = money(total - paid);
    window.__total = total;
  }

  function addPay(p) {
    var d = document.createElement("div"); d.className = "pay-row";
    var sel = '<select name="pay_method" aria-label="Payment method">' + METHODS.map(function (m) { return '<option value="' + m[0] + '">' + m[1] + '</option>'; }).join("") + '</select>';
    d.innerHTML = sel + '<input class="pay-amt" name="pay_amount" type="number" min="0" step="0.01" placeholder="Amount" aria-label="Payment amount">' +
      '<input name="pay_reference" maxlength="100" placeholder="Reference" aria-label="Payment reference"><button type="button" class="btn small rmp" aria-label="Remove payment">✕</button>';
    if (p) { d.querySelector("select").value = p.method; d.querySelector(".pay-amt").value = p.amount; d.querySelector('[name=pay_reference]').value = p.reference || ""; }
    payRows.appendChild(d); recalc();
  }

  function doSearch(selectFirst) {
    var q = search.value.trim();
    var version = ++searchVersion;
    if (!q) { results.hidden = true; search.setAttribute("aria-expanded", "false"); return; }
    fetch(search.dataset.url + "?q=" + encodeURIComponent(q), { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) { if (!r.ok) { throw new Error("Search failed"); } return r.json(); }).then(function (data) {
        if (version !== searchVersion || search.value.trim() !== q) { return; }
        current = data.products; active = current.length ? 0 : -1; results.innerHTML = "";
        current.forEach(function (p, i) {
          var li = document.createElement("li"); li.setAttribute("role", "option"); li.setAttribute("aria-selected", i === 0 ? "true" : "false");
          var a = document.createElement("span"); a.textContent = p.name + " (" + p.sku + ")";
          var b = document.createElement("span"); b.textContent = money(num(p.price)) + " · stock " + p.stock;
          li.appendChild(a); li.appendChild(b);
          li.addEventListener("mousedown", function (e) { e.preventDefault(); pick(i); });
          results.appendChild(li);
        });
        if (!current.length) { var li = document.createElement("li"); li.textContent = "No matching products"; results.appendChild(li); }
        results.hidden = false; search.setAttribute("aria-expanded", "true");
        if (selectFirst && current.length) { pick(0); }
      }).catch(function () {
        if (version !== searchVersion) { return; }
        current = []; active = -1;
        results.textContent = "Product search failed. Please try again.";
        results.hidden = false; search.setAttribute("aria-expanded", "true");
      });
  }
  function pick(i) { if (current[i]) { addLine(current[i]); searchVersion++; current = []; active = -1; search.value = ""; results.hidden = true; search.setAttribute("aria-expanded", "false"); search.focus(); } }
  function mark() { Array.prototype.forEach.call(results.children, function (li, i) { li.setAttribute("aria-selected", i === active ? "true" : "false"); }); }

  search.dataset.url = document.getElementById("product-search").getAttribute("data-url");
  search.addEventListener("input", function () {
    searchVersion++; current = []; active = -1; results.hidden = true;
    search.setAttribute("aria-expanded", "false");
    window.clearTimeout(timer); timer = window.setTimeout(function () { doSearch(false); }, 180);
  });
  search.addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, current.length - 1); mark(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); mark(); }
    else if (e.key === "Enter") {
      e.preventDefault(); window.clearTimeout(timer);
      if (active >= 0) { pick(active); } else if (search.value.trim()) { doSearch(true); }
    }
    else if (e.key === "Escape") { results.hidden = true; }
  });
  search.addEventListener("blur", function () { window.setTimeout(function () { results.hidden = true; }, 120); });

  document.addEventListener("keydown", function (e) {
    if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) { e.preventDefault(); search.focus(); }
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); document.getElementById("sale-form").requestSubmit(document.getElementById("save-sale")); }
  });
  tbody.addEventListener("input", recalc);
  tbody.addEventListener("click", function (e) { if (e.target.closest(".rm")) { e.target.closest("tr").remove(); recalc(); } });
  payRows.addEventListener("input", recalc);
  payRows.addEventListener("click", function (e) { if (e.target.closest(".rmp")) { e.target.closest(".pay-row").remove(); recalc(); } });
  ["discount_percent", "shipping_charge"].forEach(function (id) { document.getElementById(id).addEventListener("input", recalc); });
  document.getElementById("add-pay").addEventListener("click", function () { addPay(); });
  document.getElementById("pay-full").addEventListener("click", function () {
    payRows.innerHTML = ""; addPay({ method: "cash", amount: money(window.__total || 0) });
  });

  var mobile = document.getElementById("mobile");
  mobile.addEventListener("blur", function () {
    var v = mobile.value.trim(); if (!v) { return; }
    fetch(mobile.dataset.url + "?mobile=" + encodeURIComponent(v), { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (c) {
      if (mobile.value.trim() !== v) { return; }
      var info = document.getElementById("cust-info");
      if (c.exists) {
        document.getElementById("name").value = c.name; document.getElementById("address").value = c.address || ""; document.getElementById("email").value = c.mail || "";
        info.textContent = "Existing customer. Outstanding balance: " + c.outstanding + (c.credit_limit ? " (credit limit " + c.credit_limit + ")" : "");
      } else { info.textContent = "New customer - will be created with this sale."; }
    }).catch(function () {});
  });

  (init.lines || []).forEach(addLine);
  (init.payments || []).forEach(addPay);
  recalc();
})();

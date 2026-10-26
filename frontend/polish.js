/* Vòng tiến độ cho thẻ số liệu: đọc chữ đã được app.js điền ("35 / 75", "3.46") rồi đặt --pct.
   Cải tiến tuần tự: lỗi/thiếu phần tử thì thẻ vẫn hiển thị bình thường. */
(function () {
  var rings = [
    { id: 'statCredits', pct: function (t) { var m = t.match(/([\d.,]+)\s*\/\s*([\d.,]+)/); return m ? (parseFloat(m[1].replace(',', '.')) / parseFloat(m[2].replace(',', '.'))) * 100 : null; } },
    { id: 'statGpa4',    pct: function (t) { var v = parseFloat(t.replace(',', '.')); return isNaN(v) ? null : (v / 4) * 100; } }
  ];
  function update(r) {
    var el = document.getElementById(r.id);
    if (!el) return;
    var card = el.closest('.stat-card');
    var p = r.pct((el.textContent || '').trim());
    if (!card || p === null || !isFinite(p)) { if (card) card.removeAttribute('data-ring'); return; }
    p = Math.max(0, Math.min(100, p));
    card.setAttribute('data-ring', '1');
    requestAnimationFrame(function () { requestAnimationFrame(function () { card.style.setProperty('--pct', p.toFixed(1)); }); });
  }
  function init() {
    rings.forEach(function (r) {
      var el = document.getElementById(r.id);
      if (!el) return;
      new MutationObserver(function () { update(r); }).observe(el, { childList: true, characterData: true, subtree: true });
      update(r);
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();

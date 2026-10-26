(() => {
  "use strict";

  const state = {
    roadmap: null,
    currentFilter: "DANG_KY_KY_NAY",
    courseSearch: "",
    activeSection: "overviewSection",
    remoteBrowserEnabled: false,
    studyPace: "moderate",
    gpaTarget: null,       // chưa chọn mục tiêu -> chưa hiện công cụ lập kế hoạch
    customGpa: null,
    step: "result",        // màn đang mở: result | improve | next | roadmap
    showMoreImprove: false,
    improveOpen: false,     // danh sách môn học lại: mặc định thu gọn
    showMorePlan: false,
    reseedPlan: false,     // đổi cường độ học -> chọn lại môn kỳ tới theo gợi ý mới
    advisor: null,         // kết quả /api/advisor gần nhất
    improve: {},           // {ma_hp: điểm chữ mới} — học cải thiện
    whatIfPlan: {},        // {ma_hp: điểm chữ dự kiến} — môn mới kỳ tới
    updatedAt: null,
    // Điều 27.7 + thời gian tốt nghiệp
    rankAck: false,        // đã xác nhận tiếp tục sau cảnh báo hạng bằng
    planMode: null,        // null | "on_time" | "improve"
    progressSel: null,     // chỉ số kỳ đang mở chi tiết ở "Tiến trình hiện tại"
  };

  async function api(path, options = {}) {
    const headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});
    const res = await fetch(path, Object.assign({}, options, { headers }));
    let body;
    try {
      body = await res.json();
    } catch (_) {
      throw new Error(`Server trả về dữ liệu không phải JSON (status ${res.status}).`);
    }
    return { status: res.status, body };
  }

  async function loadRuntimeConfig() {
    try {
      const { status, body } = await api("/api/runtime");
      state.remoteBrowserEnabled = status === 200 && Boolean(body.remote_browser_enabled);
    } catch (_) {
      state.remoteBrowserEnabled = false;
    }
  }

  const STATUS_LABEL = {
    HOAN_THANH: { text: "Đã hoàn thành", dot: "hoan-thanh" },
    DANG_KY_KY_NAY: { text: "Đang học kỳ này", dot: "dang-ky" },
    CO_THE_DANG_KY: { text: "Có thể đăng ký", dot: "co-the" },
    BI_KHOA: { text: "Bị khóa", dot: "bi-khoa" },
    KHONG_BAT_BUOC: { text: "Tự chọn (đã đủ)", dot: "khong-bb" },
    CAN_HOC_LAI: { text: "Cần học lại", dot: "hoc-lai" },
  };

  const SECTION_TITLES = {
    overviewSection: ["Tổng quan", "Theo dõi tiến độ và môn đang học"],
    progressSection: ["Quá trình học tập", "Các kỳ đã học, bấm vào kỳ để xem môn và điểm"],
    assessmentSection: ["Đánh giá tiến độ", "Vì sao chậm và cần ưu tiên môn nào"],
    simulatorSection: ["Mô phỏng tốt nghiệp", "Thử, kiểm tra và lưu nhiều phương án học tập"],
    graduationSection: ["Điều kiện tốt nghiệp", "Kiểm tra ước tính theo Điều 26 Quy chế đào tạo"],
    supportSection: ["Hỗ trợ học tập", "Chọn mục tiêu GPA và xem kế hoạch phù hợp"],
    scheduleSection: ["Lịch học", "Thời khóa biểu theo tuần của các lớp đang học"],
    coursesSection: ["Môn học", "Tra cứu và lọc theo trạng thái học phần"],
    syncSection: ["Cập nhật dữ liệu QLĐT", "Đồng bộ điểm số, chương trình đào tạo và dữ liệu sinh viên"],
  };

  function setRoadmapUiState(hasData) {
    document.body.classList.toggle("no-roadmap", !hasData);
    document.body.classList.toggle("has-roadmap", hasData);
    renderSyncInfo(hasData);
  }

  function renderSyncInfo(hasData) {
    const when = state.updatedAt ? new Date(state.updatedAt * 1000) : null;
    const text = when && !Number.isNaN(when.getTime())
      ? when.toLocaleString("vi-VN", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit", year: "numeric" })
      : null;
    const syncState = document.getElementById("syncState");
    if (syncState) syncState.textContent = hasData ? (text ? `Cập nhật lần cuối: ${text}` : "Dữ liệu từ lần cập nhật gần nhất") : "Chưa có dữ liệu";
    const last = document.getElementById("syncLastUpdated");
    if (last) last.textContent = text || (hasData ? "Không rõ thời điểm" : "Chưa có dữ liệu");
  }

  async function loadDashboard() {
    const { status, body } = await api("/api/roadmap");
    if (status === 404) {
      state.roadmap = null;
      setRoadmapUiState(false);
      document.getElementById("emptyState").classList.remove("hidden");
      document.getElementById("dataWrap").classList.add("hidden");
      return;
    }
    if (!body.success) return;

    state.roadmap = body.data;
    state.updatedAt = body.updated_at || null;
    setRoadmapUiState(true);
    document.getElementById("emptyState").classList.add("hidden");
    document.getElementById("dataWrap").classList.remove("hidden");

    renderSummary();
    renderCourses();
    renderTargetPicker();
    await refreshAdvisor();
    setActiveSection(state.activeSection || "overviewSection", false);
  }

  function getGroupRows() {
    const summary = state.roadmap?.khoi_summary || {};
    const monHoc = state.roadmap?.mon_hoc || [];
    return Object.keys(summary).sort().flatMap((ma) => {
      const g = summary[ma];
      if (g.bo_qua || ma === "F1") return [];

      const required = Number(g.tin_bat_buoc ?? g.tong_tin ?? 0) || 0;
      const accumulated = Number(g.da_tich_luy ?? 0) || 0;
      const monTrongKhoi = monHoc.filter((c) => c.khoi_kt === ma);
      const coMonHoanThanh = monTrongKhoi.some((c) => c.trang_thai === "HOAN_THANH");
      const conditionDone = required <= 0 && (accumulated > 0 || coMonHoanThanh);
      const done = Boolean(g.da_du_yeu_cau) || conditionDone || (required > 0 && accumulated >= required);
      const fractionText = conditionDone ? "Đã hoàn thành" : `${accumulated} / ${required} TC`;
      const pct = conditionDone ? 100 : required > 0 ? Math.min(100, (Math.min(accumulated, required) / required) * 100) : 0;

      return [{ ma, g, required, accumulated, done, fractionText, pct }];
    });
  }

  function renderSummary() {
    const d = state.roadmap;
    const gpa = d.gpa || {};
    const doneCredits = Number(d.tin_chi_da_hoc || 0);
    const totalCredits = Number(d.tin_chi_toan_khoa || 0);
    const remainCredits = Math.max(0, totalCredits - doneCredits);
    const pct = totalCredits > 0 ? Math.min(100, (doneCredits / totalCredits) * 100) : 0;

    document.getElementById("statCredits").textContent = `${doneCredits} / ${totalCredits}`;
    document.getElementById("statCreditsRemain").textContent = remainCredits > 0 ? `Còn ${remainCredits} tín chỉ` : "Đã đủ tín chỉ";
    document.getElementById("statGpa4").textContent = gpa.gpa_4 != null ? `${gpa.gpa_4}` : "—";
    document.getElementById("statGpa10").textContent = `Hệ 10: ${gpa.gpa_10 ?? "—"}${gpa.xep_loai ? ` · ${gpa.xep_loai}` : ""}`;
    document.getElementById("statCompleted").textContent = d.so_mon_hoan_thanh ?? "—";
    document.getElementById("statEligible").textContent = d.so_mon_co_the_dang_ky ?? "—";
    document.getElementById("overallProgressFill").style.width = `${pct}%`;
    document.getElementById("overallProgressText").textContent = `${Math.round(pct)}%`;

    const groupRows = getGroupRows();
    const pending = groupRows.filter((x) => !x.done).slice(0, 3);
    const pendingWrap = document.getElementById("overviewPendingGroups");
    pendingWrap.innerHTML = pending.length
      ? pending.map((x) => `
          <div class="overview-group-mini">
            <span>[${x.ma}] ${x.g.ten_khoi || ""}</span>
            <strong class="mono">${x.fractionText}</strong>
          </div>`).join("")
      : `<p class="muted-copy">Các khối kiến thức chính đã đáp ứng yêu cầu hiện tại.</p>`;

    const current = (d.mon_hoc || []).filter((c) => c.trang_thai === "DANG_KY_KY_NAY");
    const currentCredits = current.reduce((sum, c) => sum + (Number(c.so_tin) || 0), 0);
    document.getElementById("currentSemesterCount").textContent = current.length;
    document.getElementById("currentSemesterCredits").textContent = currentCredits;
    document.getElementById("currentSemesterNote").textContent = current.length
      ? `Đang theo học ${current.length} học phần trong dữ liệu đăng ký hiện tại.`
      : "Chưa ghi nhận học phần đang đăng ký trong kỳ hiện tại.";

    renderRetakeBar(d);
    renderProgress(d);
    renderAssessment(d);
  }

  // Thanh "đã trượt X / Y TC": Y là ngưỡng 5% tổng tín chỉ (Điều 27.7). Vượt Y thì hạng
  // Giỏi/Xuất sắc bị hạ một mức. Chỉ hiện khi sinh viên thật sự có môn trượt.
  function assessmentSemesterOrder(value) {
    const m = String(value ?? "").match(/\(([\d,]+)\)\s*$/);
    if (!m) return null;
    const nums = m[1].split(",").map(Number).filter((n) => Number.isFinite(n) && n > 0);
    return nums.length ? Math.min(...nums) : null;
  }

  function renderAssessment(d) {
    const summary = $("assessmentSummary");
    const content = $("assessmentContent");
    if (!summary || !content) return;
    const courses = (d.mon_hoc || []).filter((c) => c.trang_thai !== "KHONG_BAT_BUOC");
    const nameByCode = new Map((d.mon_hoc || []).map((c) => [String(c.ma_hp), c.ten_hp || "Học phần chưa rõ tên"]));
    const statusDone = (c) => c.trang_thai === "HOAN_THANH";
    const completed = new Set(courses.filter(statusDone).map((c) => String(c.ma_hp)));
    const actualOrders = courses.map((c) => assessmentSemesterOrder(c.hoc_ky_thuc_te)).filter((n) => n != null);
    const currentOrder = actualOrders.length ? Math.max(...actualOrders) : null;
    const hasActual = currentOrder != null;

    // Khối tự chọn (vd D2.x): chỉ cần đủ số tín chỉ của khối, không phải học hết từng môn,
    // nên không tính từng môn là "chậm"; thay vào đó báo số tín chỉ tự chọn còn thiếu.
    const blockSummary = d.khoi_summary || {};
    const isElectiveBlock = (ma) => {
      const g = blockSummary[ma];
      if (!g || g.bo_qua) return false;
      const req = Number(g.tin_bat_buoc ?? g.tong_tin ?? 0) || 0;
      const tot = Number(g.tong_tin ?? 0) || 0;
      return String(ma).toUpperCase().startsWith("D2") || (req > 0 && tot > req);
    };
    const isElective = (c) => isElectiveBlock(c.khoi_kt);
    const electiveNeeds = Object.keys(blockSummary).sort().filter(isElectiveBlock).map((ma) => {
      const g = blockSummary[ma];
      const req = Number(g.tin_bat_buoc ?? g.tong_tin ?? 0) || 0;
      const acc = Number(g.da_tich_luy ?? 0) || 0;
      const ten = String(ma).toUpperCase().startsWith("D2")
        ? "Chuyên ngành tự chọn"
        : String(g.ten_khoi || ma).replace(/^Khối kiến thức\s*/i, "").replace(/^./, (ch) => ch.toUpperCase());
      const list = (d.mon_hoc || []).filter((c) => c.khoi_kt === ma)
        .sort((a, b) => String(a.ten_hp || "").localeCompare(String(b.ten_hp || ""), "vi"));
      return { ma, ten, con: Math.max(0, req - acc), list };
    }).filter((x) => x.con > 0);

    const retakes = courses.filter((c) => c.trang_thai === "CAN_HOC_LAI");
    const overdue = !hasActual ? [] : courses.filter((c) => {
      const expected = assessmentSemesterOrder(c.hoc_ky_du_kien);
      return expected != null && expected < currentOrder && !statusDone(c) && c.trang_thai !== "DANG_KY_KY_NAY" && !isElective(c);
    });
    const overdueCodes = new Set(overdue.map((c) => String(c.ma_hp)));
    const blockedByOverdue = courses.filter((c) => !statusDone(c) && !isElective(c) && (c.tien_quyet || []).some((p) => overdueCodes.has(String(p))));
    const locked = courses.filter((c) => c.trang_thai === "BI_KHOA" && !isElective(c) && (c.tien_quyet || []).length);

    const urgentMap = new Map();
    retakes.concat(overdue).forEach((c) => urgentMap.set(String(c.ma_hp), c));
    const waitingMap = new Map();
    blockedByOverdue.concat(locked).forEach((c) => {
      if (!urgentMap.has(String(c.ma_hp))) waitingMap.set(String(c.ma_hp), c);
    });
    const urgent = [...urgentMap.values()];
    const waiting = [...waitingMap.values()];

    const creditDone = Number(d.tin_chi_da_hoc) || 0;
    const creditTotal = Number(d.tin_chi_toan_khoa) || 0;
    const percent = creditTotal > 0 ? Math.min(100, Math.round(creditDone / creditTotal * 100)) : 0;
    const total = urgent.length + waiting.length;

    summary.innerHTML = `<section class="gx-card">
        <div class="gx-row">
          <div class="gx-main"><strong>Tín chỉ đã tích lũy</strong><span>${percent}%${hasActual ? ` · đến kỳ ${currentOrder}` : ""} · ${total ? `${total} môn cần chú ý` : "chưa có môn cần chú ý"}</span></div>
          <span class="gx-big gx-big-ink">${escapeHtml(creditDone)} / ${escapeHtml(creditTotal || "—")}</span>
        </div>
        <div class="gx-bar-track"><i style="width:${percent}%"></i></div>
      </section>`;

    const missingNames = (c) => (c.tien_quyet || []).filter((p) => !completed.has(String(p)))
      .map((p) => nameByCode.get(String(p)) || "môn tiên quyết");
    const termOf = (c) => { const m = String(c.hoc_ky_du_kien || "").match(/\((\d+)\)/); return m ? m[1] : ""; };
    const MAX_SHOWN = 8;

    const row = (c, tag) => {
      const parts = [];
      if (overdueCodes.has(String(c.ma_hp)) && termOf(c)) parts.push(`Dự kiến kỳ ${escapeHtml(termOf(c))}`);
      const miss = missingNames(c);
      if (miss.length) parts.push(`Cần hoàn thành trước: ${escapeHtml(miss.join(", "))}`);
      return `<div class="gx-todo"><div class="gx-row">
          <div class="gx-main"><strong>${escapeHtml(c.ten_hp || "Học phần chưa rõ tên")}</strong>${parts.length ? `<span>${parts.join(" · ")}</span>` : ""}</div>
          ${tag}</div></div>`;
    };
    const urgentTag = (c) => c.trang_thai === "CAN_HOC_LAI"
      ? '<span class="gx-chip is-bad">Cần học lại</span>'
      : '<span class="gx-chip is-warn">Chậm tiến độ</span>';
    const more = (arr) => arr.length > MAX_SHOWN ? `<p class="gx-note">Và ${arr.length - MAX_SHOWN} môn khác.</p>` : "";

    const parts = [];
    if (!hasActual) parts.push('<p class="gx-note">Chưa có học kỳ thực tế nên chưa xác định được môn chậm. Hãy cập nhật dữ liệu QLĐT.</p>');
    if (urgent.length) {
      parts.push(`<section class="gx-card"><p class="gx-kicker">Cần xử lý trước (${urgent.length})</p>
        ${urgent.slice(0, MAX_SHOWN).map((c) => row(c, urgentTag(c))).join("")}${more(urgent)}</section>`);
    }
    if (waiting.length) {
      parts.push(`<details class="gx-card gx-details"><summary class="gx-done-head"><span class="gx-done-title">Đang chờ môn tiên quyết</span><span class="gx-muted">${waiting.length} môn</span><span class="gx-chev" aria-hidden="true">⌄</span></summary>
        <div style="margin-top:.5rem">${waiting.slice(0, MAX_SHOWN).map((c) => row(c, '<span class="gx-chip">Chưa đủ điều kiện</span>')).join("")}${more(waiting)}</div></details>`);
    }
    if (electiveNeeds.length) {
      const electiveChip = (c) => {
        const label = STATUS_LABEL[c.trang_thai]?.text || "";
        const cls = c.trang_thai === "HOAN_THANH" ? "is-ok" : c.trang_thai === "BI_KHOA" ? "is-warn" : "";
        return label ? `<span class="gx-chip ${cls}">${escapeHtml(c.trang_thai === "BI_KHOA" ? "Chưa đủ điều kiện" : label)}</span>` : "";
      };
      parts.push(`<section class="gx-card"><p class="gx-kicker">Tự chọn còn thiếu</p>
        ${electiveNeeds.map((x) => `<details class="gx-details gx-todo">
          <summary class="gx-done-head"><span class="gx-main"><strong>${escapeHtml(x.ten)}</strong><span>Chọn các môn bất kỳ trong khối cho đủ số tín chỉ · ${x.list.length} môn</span></span>
            <span class="gx-chip is-warn">Còn ${escapeHtml(formatPlannerCredits(x.con))} TC</span><span class="gx-chev" aria-hidden="true">⌄</span></summary>
          <div class="gx-courses" style="margin-left:0">${x.list.map((c) => `<div class="gx-course"><span>${escapeHtml(c.ten_hp || "Học phần chưa rõ tên")}</span><span class="gx-muted">${escapeHtml(formatPlannerCredits(c.so_tin))} TC</span>${electiveChip(c)}</div>`).join("") || '<p class="gx-note">Không có môn nào trong dữ liệu.</p>'}</div>
        </details>`).join("")}</section>`);
    }
    if (!urgent.length && !waiting.length && !electiveNeeds.length) {
      parts.push('<section class="gx-card"><p class="gx-empty">Chưa phát hiện môn nào chậm hoặc bị chặn theo dữ liệu hiện có.</p></section>');
    }
    parts.push('<p class="gx-note">Cảnh báo tham khảo, không phải xác nhận chính thức về tiến độ tốt nghiệp.</p>');
    content.innerHTML = parts.join("");
  }

  function renderRetakeBar(d) {
    const card = document.getElementById("retakeCard");
    const hv = d.hoc_vu || {};
    const hl = hv.co_du_lieu ? hv.hoc_lai : null;
    const used = hl ? Number(hl.tin_chi) || 0 : 0;
    const limit = hl ? Number(hl.nguong_tin_chi) || 0 : 0;
    if (!hl || used <= 0 || limit <= 0) { card.classList.add("hidden"); return; }

    const pct = Math.min(100, (used / limit) * 100);
    const over = used > limit + 1e-9;
    const fill = document.getElementById("retakeFill");
    fill.style.width = `${pct}%`;
    fill.classList.toggle("is-over", over);
    fill.classList.toggle("is-warn", !over && pct >= 70);
    document.getElementById("retakeText").textContent =
      `${formatPlannerCredits(used)} / ${formatPlannerCredits(limit)} TC`;

    document.getElementById("retakeNote").textContent = over
      ? `Bạn đã trượt vượt ngưỡng: hạng tốt nghiệp Giỏi/Xuất sắc sẽ bị hạ một mức.`
      : `Bạn đã trượt ${formatPlannerCredits(used)} trên ${formatPlannerCredits(limit)} TC. Còn ${formatPlannerCredits(limit - used)} TC nữa là chạm ngưỡng; vượt ngưỡng này hạng Giỏi/Xuất sắc sẽ bị hạ một mức.`;
    card.classList.remove("hidden");
  }

  // ---------- Tiến trình hiện tại ----------
  // Kỳ đã học: sáng. Kỳ đang học: viền vàng. Kỳ chưa tới: mờ.
  // Bấm vào kỳ đã học / đang học để xem môn và điểm.
  const PROGRESS_TERMS_ON_TIME = 12;   // 4 năm × 3 kỳ
  const TERMS_PER_YEAR_UI = 3;

  function progressTerms(d) {
    const done = (d.hoc_vu && d.hoc_vu.co_du_lieu && d.hoc_vu.hoc_ky) || [];
    if (!done.length) return [];
    const idxOf = (nam, ky) => parseInt(nam, 10) * TERMS_PER_YEAR_UI + (Number(ky) - 1);
    const byIdx = new Map(done.map((s) => [idxOf(s.nam_hoc, s.ky), s]));
    const startIdx = idxOf(done[0].nam_hoc, done[0].ky);
    const lastIdx = Math.max(...byIdx.keys());
    const currentCourses = (d.mon_hoc || []).filter((c) => c.trang_thai === "DANG_KY_KY_NAY");
    const total = Math.max(PROGRESS_TERMS_ON_TIME, lastIdx - startIdx + 2);
    const terms = [];
    for (let i = 0; i < total; i += 1) {
      const idx = startIdx + i;
      const year = Math.floor(idx / TERMS_PER_YEAR_UI);
      const ky = (idx % TERMS_PER_YEAR_UI) + 1;
      const sem = byIdx.get(idx) || null;
      let kind = "future";
      if (sem) kind = "done";
      else if (idx === lastIdx + 1 && currentCourses.length) kind = "current";
      terms.push({ i, idx, year, ky, kind, sem, label: `${year}-${year + 1} · HK${ky}`,
        courses: kind === "current" ? currentCourses : null });
    }
    return terms;
  }

  function renderProgress(d) {
    const card = document.getElementById("progressCard");
    if (!card) return;
    const terms = progressTerms(d);
    if (!terms.length) {
      document.getElementById("progressSummary").textContent = "";
      document.getElementById("progressGrid").innerHTML =
        `<p class="muted-copy">Chưa có bảng điểm theo học kỳ. Hãy cập nhật dữ liệu QLĐT để xem tiến trình.</p>`;
      document.getElementById("progressDetail").classList.add("hidden");
      return;
    }

    const nDone = terms.filter((t) => t.kind === "done").length;
    document.getElementById("progressSummary").textContent =
      `${nDone} / ${terms.length} kỳ đã học`;

    const years = [];
    terms.forEach((t) => {
      const y = Math.floor(t.i / TERMS_PER_YEAR_UI);
      (years[y] = years[y] || []).push(t);
    });
    document.getElementById("progressGrid").innerHTML = years.map((row, y) => `
      <div class="pg-year">
        <span class="pg-year-label"><b>Năm ${y + 1}</b><small>${row[0].year}-${row[0].year + 1}</small></span>
        <div class="pg-cells">${row.map((t) => {
          const clickable = t.kind !== "future";
          const sub = t.kind === "done"
            ? (t.sem.gpa_ky != null ? `GPA ${fmt(t.sem.gpa_ky)}` : "—")
            : t.kind === "current" ? "Đang học" : "";
          return `<button type="button" class="pg-cell pg-${t.kind}${state.progressSel === t.idx ? " pg-sel" : ""}"
            ${clickable ? `data-pg-idx="${t.idx}" aria-pressed="${state.progressSel === t.idx}"` : "disabled"}>
            <strong>HK${t.ky}</strong><span>${escapeHtml(sub)}</span></button>`;
        }).join("")}</div>
      </div>`).join("");

    renderProgressDetail(terms);
  }

  function renderProgressDetail(terms) {
    const box = document.getElementById("progressDetail");
    const t = terms.find((x) => x.idx === state.progressSel && x.kind !== "future");
    if (!t) { box.classList.add("hidden"); box.innerHTML = ""; return; }

    let head;
    let rows;
    if (t.kind === "done") {
      const s = t.sem;
      head = `<h3>${escapeHtml(t.label)}</h3>
        <p>${s.gpa_ky != null ? `GPA kỳ ${fmt(s.gpa_ky)} · ` : ""}${formatPlannerCredits(s.tin_chi_dat)} / ${formatPlannerCredits(s.tin_chi_dang_ky)} TC đạt</p>`;
      const mon = s.mon || [];
      rows = mon.length ? mon.map((m) => `
        <tr class="${m.truot ? "pg-fail" : ""}">
          <td>${escapeHtml(m.ten_hp)}${m.lan_hoc > 1 ? ` <small>(học lần ${m.lan_hoc})</small>` : ""}</td>
          <td class="num">${formatPlannerCredits(m.so_tin)}</td>
          <td class="num">${m.diem_10 != null ? fmt(m.diem_10, 1) : "—"}</td>
          <td class="num"><b>${escapeHtml(m.diem_chu || "—")}</b></td>
        </tr>`).join("")
        : `<tr><td colspan="4">Chưa có chi tiết môn. Hãy cập nhật dữ liệu QLĐT để xem điểm từng môn.</td></tr>`;
    } else {
      const credits = t.courses.reduce((sum, c) => sum + (Number(c.so_tin) || 0), 0);
      head = `<h3>${escapeHtml(t.label)}</h3><p>Đang học ${t.courses.length} môn · ${formatPlannerCredits(credits)} TC · chưa có điểm</p>`;
      rows = t.courses.map((c) => `
        <tr><td>${escapeHtml(c.ten_hp)}</td><td class="num">${formatPlannerCredits(c.so_tin)}</td>
        <td class="num" colspan="2"><small>đang học</small></td></tr>`).join("");
    }
    box.innerHTML = `${head}
      <div class="pg-table-wrap"><table class="pg-table">
        <thead><tr><th>Môn</th><th class="num">TC</th><th class="num">Điểm 10</th><th class="num">Điểm chữ</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`;
    box.classList.remove("hidden");
  }

  document.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-pg-idx]");
    if (!btn || !state.roadmap) return;
    const idx = Number(btn.dataset.pgIdx);
    state.progressSel = state.progressSel === idx ? null : idx;
    renderProgress(state.roadmap);
  });

  // =====================================================================
  // Hỗ trợ học tập: Bước 1 chọn mục tiêu -> Bước 2 phân tích -> Bước 3 công cụ.
  // Mọi tính toán nằm ở server (/api/advisor); frontend chỉ giữ lựa chọn của
  // người dùng và vẽ lại kết quả, nên GPA / tín chỉ / lộ trình luôn đồng bộ.
  // =====================================================================
  const PACE_KEYS = ["moderate", "heavy", "fast"];
  const TARGET_KEYS = ["kha", "gioi", "xuat_sac", "custom"];
  const PLANNABLE = ["CO_THE_DANG_KY", "CAN_HOC_LAI"];
  
  const $ = (id) => document.getElementById(id);

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function fmt(value, digits = 2) {
    const n = Number(value);
    return value == null || !Number.isFinite(n) ? "—" : n.toFixed(digits);
  }

  function formatPlannerCredits(value) {
    const n = Number(value || 0);
    return Number.isInteger(n) ? `${n}` : n.toFixed(1);
  }

  function readJsonPref(key) {
    try {
      const parsed = JSON.parse(localStorage.getItem(key) || "{}");
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    } catch (_) {
      return {};
    }
  }

  function writePref(key, value) {
    try { localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value)); } catch (_) {}
  }

  // Danh sách môn gợi ý phụ thuộc mục tiêu thật (vd. Giỏi + học lại > 5% => 3.60) và dữ liệu.
  // Nếu lựa chọn đã lưu trong trình duyệt được tạo từ gợi ý cũ thì tích lại theo gợi ý mới,
  // nếu không thì người dùng sẽ thấy "23 môn" nhưng chỉ vài môn cũ được tích.
  function improveSignature(a) {
    const t = a.target || {};
    const codes = ((a.recommend || {}).courses || []).map((c) => String(c.ma_hp)).sort().join(",");
    return `${t.key}|${t.effective_gpa != null ? t.effective_gpa : t.gpa}|${codes}`;
  }

  function seedImproveIfStale(a) {
    const rec = a && a.recommend && a.recommend.courses;
    if (!rec || !rec.length) return false;
    const sig = improveSignature(a);
    let saved = null;
    try { saved = localStorage.getItem("paImproveSig"); } catch (_) {}
    if (Object.keys(state.improve).length && saved === sig) return false;
    state.improve = {};
    rec.forEach((c) => { state.improve[c.ma_hp] = c.new_letter; });
    writePref("paImprovePlan", state.improve);
    writePref("paImproveSig", sig);
    return true;
  }

  function loadPlannerPreferences() {
    try {
      const pace = localStorage.getItem("paStudyPace");
      const target = localStorage.getItem("paGpaTarget");
      const custom = Number(localStorage.getItem("paCustomGpa"));
      if (PACE_KEYS.includes(pace)) state.studyPace = pace;
      if (Number.isFinite(custom) && custom >= 2 && custom <= 4) state.customGpa = custom;
      if (TARGET_KEYS.includes(target) && (target !== "custom" || state.customGpa != null)) state.gpaTarget = target;
    } catch (_) {}
    state.whatIfPlan = readJsonPref("paWhatIfPlan");
    state.improve = readJsonPref("paImprovePlan");
    setPaceUi();
    renderTargetPicker();
  }

  // ---------- Bước 1: mục tiêu (thanh kéo GPA hoặc chọn xếp loại) ----------
  const RANKS = [
    { key: "kha", label: "Khá", gpa: 2.5 },
    { key: "gioi", label: "Giỏi", gpa: 3.2 },
    { key: "xuat_sac", label: "Xuất sắc", gpa: 3.6 },
  ];
  const DEFAULT_IMPROVE_LETTER = "A";
  const COMFORT_REQUIRED = 3.5; // trùng advisor.COMFORT_REQUIRED: trên mức này coi là "rất sát"
  const STEPS = ["result", "improve", "next", "roadmap"];
  const PANES = { result: "wzResult", improve: "wzImprove", next: "wzNext", roadmap: "wzRoadmap" };

  function rankLabelFor(gpa) {
    if (gpa >= 3.6 - 1e-9) return "Xuất sắc";
    if (gpa >= 3.2 - 1e-9) return "Giỏi";
    if (gpa >= 2.5 - 1e-9) return "Khá";
    return "Trung bình";
  }

  function currentTargetGpa() {
    if (state.gpaTarget === "custom") return state.customGpa;
    const rank = RANKS.find((r) => r.key === state.gpaTarget);
    return rank ? rank.gpa : null;
  }

  function goalText(gpa) {
    const rank = RANKS.find((r) => Math.abs(r.gpa - gpa) < 1e-6);
    return `${gpa.toFixed(2)} · ${rank ? rank.label : rankLabelFor(gpa)}`;
  }

  // "loại Giỏi" hoặc "GPA 3.40" — dùng trong câu kết luận
  function targetPhrase(target) {
    return target.key === "custom" ? `GPA ${fmt(target.gpa)}` : `loại ${target.label}`;
  }
  function targetShort(target) {
    return target.key === "custom" ? `GPA ${fmt(target.gpa)}` : target.label;
  }

  function renderTargetPicker() {
    // Chỉ còn 3 lựa chọn: Khá / Giỏi / Xuất sắc.
    if (state.gpaTarget === "custom") state.gpaTarget = null;
    document.querySelectorAll(".wz-pick").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.rank === state.gpaTarget);
    });
    $("goalHint")?.classList.toggle("hidden", state.gpaTarget != null);
  }

  function setTargetGpa(gpa) {
    const value = Math.round(gpa * 100) / 100;
    if (!Number.isFinite(value) || value < 2 || value > 4) return;
    const current = currentTargetGpa();
    if (current != null && Math.abs(current - value) < 1e-6) { renderTargetPicker(); return; }
    const rank = RANKS.find((r) => Math.abs(r.gpa - value) < 1e-6);
    if (rank) {
      state.gpaTarget = rank.key;
    } else {
      state.gpaTarget = "custom";
      state.customGpa = value;
      writePref("paCustomGpa", String(value));
    }
    writePref("paGpaTarget", state.gpaTarget);
    // Môn học lại được gợi ý theo từng mục tiêu nên chọn lại từ đầu khi đổi mục tiêu.
    state.improve = {};
    writePref("paImprovePlan", state.improve);
    state.step = "result";
    state.showMoreImprove = false;
    state.rankAck = false;
    state.planMode = null;
    renderTargetPicker();
    refreshAdvisor();
  }

  // ---------- gọi server ----------
  let advisorSeq = 0;
  let advisorTimer = null;

  function refreshAdvisor(immediate = true) {
    clearTimeout(advisorTimer);
    if (immediate) return runAdvisor();
    advisorTimer = setTimeout(runAdvisor, 180);
    return Promise.resolve();
  }

  async function runAdvisor() {
    const body$ = $("supportBody");
    if (!state.roadmap || !state.gpaTarget) {
      body$?.classList.add("hidden");
      return;
    }
    const seq = ++advisorSeq;
    const payload = {
      pace: state.studyPace,
      target: state.gpaTarget,
      custom_gpa: state.gpaTarget === "custom" ? state.customGpa : undefined,
      improvements: state.improve,
      plan: state.whatIfPlan,
    };
    try {
      const { status, body } = await api("/api/advisor", { method: "POST", body: JSON.stringify(payload) });
      if (seq !== advisorSeq) return; // đã có yêu cầu mới hơn
      if (status !== 200 || !body.success) {
        showAdvisorError(body.error || "Chưa tính được kế hoạch.");
        return;
      }
      state.advisor = body.data;
      pruneSelections();
      if (state.reseedPlan && state.step === "next") {
        // Đổi cường độ học: lấy lại kế hoạch theo gợi ý mới rồi tính lại một lần.
        state.reseedPlan = false;
        seedPlanFromSuggestions(state.advisor);
        return runAdvisor();
      }
      renderSupport();
    } catch (err) {
      if (seq === advisorSeq) showAdvisorError(err.message);
    }
  }

  function showAdvisorError(message) {
    $("supportBody")?.classList.remove("hidden");
    $("wzTop")?.classList.add("hidden");
    Object.values(PANES).forEach((id) => $(id).classList.toggle("hidden", id !== PANES.result));
    $("wzVerdict").innerHTML = `<p class="wz-error">Lỗi: ${escapeHtml(message)}</p>`;
  }

  // Bỏ lựa chọn cũ không còn hợp lệ sau khi dữ liệu thay đổi (vd sau khi crawl lại).
  function pruneSelections() {
    const a = state.advisor;
    if (!a || !state.roadmap) return;
    const improvable = new Set(a.improvement.candidates.map((c) => String(c.ma_hp)));
    const plannable = new Set(getWhatIfCandidates().map((c) => String(c.ma_hp)));
    let improveChanged = false;
    let planChanged = false;
    Object.keys(state.improve).forEach((code) => {
      if (!improvable.has(code)) { delete state.improve[code]; improveChanged = true; }
    });
    Object.keys(state.whatIfPlan).forEach((code) => {
      if (!plannable.has(code)) { delete state.whatIfPlan[code]; planChanged = true; }
    });
    if (improveChanged) writePref("paImprovePlan", state.improve);
    if (planChanged) writePref("paWhatIfPlan", state.whatIfPlan);
  }

  function getWhatIfCandidates() {
    return (state.roadmap?.mon_hoc || [])
      .filter((c) => c && c.ma_hp && PLANNABLE.includes(c.trang_thai))
      .sort((a, b) => {
        if (a.trang_thai !== b.trang_thai) return a.trang_thai === "CAN_HOC_LAI" ? -1 : 1;
        return String(a.ten_hp || "").localeCompare(String(b.ten_hp || ""), "vi");
      });
  }

  function seedPlanFromSuggestions(a) {
    const plan = {};
    (a?.suggestions?.courses || [])
      .filter((c) => PLANNABLE.includes(c.trang_thai))
      .forEach((c) => { plan[c.ma_hp] = "B"; });
    state.whatIfPlan = plan;
    writePref("paWhatIfPlan", plan);
  }

  // ---------- các bước ----------
  function baselineNeedsImprove(a) {
    const g = a?.baseline?.gpa;
    return Boolean(g && !g.feasible && g.feasible_with_improvements);
  }

  function goStep(step) {
    if (!STEPS.includes(step)) return;
    const a = state.advisor;
    let needRefresh = false;

    if (step === "improve" && seedImproveIfStale(a)) {
      needRefresh = true;   // đã tích sẵn lại các môn gợi ý theo mục tiêu hiện tại
    }
    if (step === "next" && a && !Object.keys(state.whatIfPlan).length) {
      seedPlanFromSuggestions(a);
      needRefresh = true;
    }

    state.step = step;
    renderSupport();
    if (needRefresh) refreshAdvisor();
    $("supportSection")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderSupport() {
    const a = state.advisor;
    if (!a) return;
    $("supportBody").classList.remove("hidden");
    Object.entries(PANES).forEach(([key, id]) => $(id).classList.toggle("hidden", key !== state.step));
    renderTop(a);
    ({ result: renderResult, improve: renderImprove, next: renderNext, roadmap: renderRoadmap })[state.step](a);
  }

  // Dòng cố định phía trên từ màn 3 trở đi: GPA dự kiến / mục tiêu
  function renderTop(a) {
    const top = $("wzTop");
    if (state.step !== "improve") { top.classList.add("hidden"); top.innerHTML = ""; return; }
    let projected = a.gpa.projected_final;
    // Chưa đạt thì không để làm tròn khiến "3.60 / 3.60" trông như đã đạt.
    if (projected != null && !a.gpa.feasible && projected >= goalGpa(a) - 1e-9) projected = goalGpa(a) - 0.01;
    top.classList.remove("hidden");
    top.innerHTML = `
      <span class="wz-label">GPA dự kiến / mục tiêu</span>
      <strong class="wz-top-value">${projected != null ? fmt(projected) : "—"} / ${fmt(goalGpa(a))}</strong>
      <small>Nếu phần còn lại đạt điểm gợi ý</small>`;
  }

  // GPA mục tiêu thật dùng cho lộ trình (Giỏi + học lại > 5% => 3.60, Điều 27.7).
  function goalGpa(a) {
    const t = a.target || {};
    return t.effective_gpa != null ? t.effective_gpa : t.gpa;
  }

  // ---------- Màn 2: kết luận ----------
  function requiredCell(gpa) {
    const req = gpa.required_remaining_average;
    if (req == null) return { value: "—", help: "Không còn tín chỉ cần điểm", bad: false };
    if (req > 4.0 + 1e-6) return { value: fmt(req), help: "Vượt 4.0 · không khả thi", bad: true };
    return { value: fmt(req), help: gpa.required_letter ? `≈ ${gpa.required_letter} trở lên` : "", bad: false };
  }

  // Điều 27.7: học lại vượt 5% tổng tín chỉ -> hạng Giỏi/Xuất sắc bị giảm một mức.
  function retakeWarningHtml(a) {
    const r = a.retake || {};
    if (!r.downgrade_risk_for_target) return "";
    const t = a.target || {};
    const rc = a.roadmap_check || {};
    const used = formatPlannerCredits(r.detected_credits);
    const limit = formatPlannerCredits(r.threshold_credits);
    // Giỏi + học lại > 5%: lộ trình đã được xếp để GPA đạt 3.60 nên bằng vẫn ghi Giỏi -> chỉ là ghi chú.
    if (t.raised_by_retake && rc.meets_goal) {
      return `<section class="retake-warn retake-info" role="note">`
        + `<h4 class="rw-title">Lộ trình đã xếp để giữ bằng Giỏi</h4>`
        + `<p class="rw-why">Bạn đã học lại ${used} TC, vượt ngưỡng 5% (${limit} TC), `
        + `nên theo quy chế phải đạt GPA toàn khóa từ ${fmt(rc.goal_gpa)} thì bằng mới ghi Giỏi. `
        + `Lộ trình bên dưới đã tính theo mức này (dự kiến ${fmt(rc.final_gpa)}).</p>`
        + `</section>`;
    }
    // Giỏi + học lại > 5% nhưng lộ trình hiện chọn chưa đủ 3.60.
    if (t.raised_by_retake) {
      return `<section class="retake-warn" role="alert">`
        + `<h4 class="rw-title">Lộ trình hiện chưa đủ để giữ bằng Giỏi</h4>`
        + `<p class="rw-why">Bạn đã học lại ${used} TC, vượt ngưỡng 5% (${limit} TC), nên cần GPA toàn khóa từ `
        + `${fmt(rc.goal_gpa != null ? rc.goal_gpa : t.effective_gpa)}`
        + `${rc.final_gpa != null ? `, trong khi lộ trình hiện chỉ đạt ${fmt(rc.final_gpa)}` : ""}. `
        + `Hãy chọn thêm môn học cải thiện.</p>`
        + `</section>`;
    }
    const short = escapeHtml(targetShort(a.target));
    const down = escapeHtml(r.downgraded_to || "một mức");
    return `<section class="retake-warn" role="alert">`
      + `<h4 class="rw-title">Hạng ${short} có thể bị hạ xuống ${down}</h4>`
      + `<p class="rw-why">Bạn đã học lại ${formatPlannerCredits(r.detected_credits)} TC, `
      + `vượt 5% tổng số tín chỉ. `
      + `Hãy hỏi Phòng Đào tạo để chắc chắn.</p>`
      + `</section>`;
  }

  function renderResult(a) {
    const g = a.baseline.gpa;
    const req = g.required_remaining_average;
    const phrase = targetPhrase(a.target);
    const short = targetShort(a.target);
    let title;
    let detail = "";
    let actions = "";

    if (g.feasible) {
      if (req == null) {
        title = `Bạn đã đạt mục tiêu ${short}.`;
      } else if (req <= 0) {
        title = `Mục tiêu ${short} đã đảm bảo.`;
        detail = "Chỉ cần đạt điểm qua môn ở phần còn lại.";
        actions = `<button type="button" class="wz-btn" data-wz-go="next">Xem môn học kỳ tới</button>`;
      } else {
        title = req > COMFORT_REQUIRED ? `Đạt ${phrase} được, nhưng rất sát.` : `Đạt ${phrase} được nếu bạn học đều.`;
        detail = `Phần còn lại cần trung bình khoảng ${g.required_letter || fmt(req)} trở lên.`;
        actions = `<button type="button" class="wz-btn" data-wz-go="next">Xem môn học kỳ tới</button>`;
      }
    } else if (g.feasible_with_improvements) {
      title = `Chỉ học môn mới thì chưa đủ ${short}. Học cải thiện thì được.`;
      actions = `<button type="button" class="wz-btn" data-wz-go="improve">Xem môn nên học lại</button>`;
    } else {
      const max = g.max_final_with_all_improvements;
      title = `Mục tiêu ${short} chưa khả thi.`;
      detail = max != null ? `Dù học cải thiện tối đa, GPA cao nhất bạn đạt được là ${fmt(max)}.` : "";
      const lower = [...RANKS].reverse().find((r) => r.gpa < goalGpa(a) - 1e-9 && max != null && r.gpa <= max + 1e-9);
      actions = `${lower ? `<button type="button" class="wz-btn" data-wz-target="${lower.key}">Chọn ${lower.label} ${lower.gpa.toFixed(2)}</button>` : ""}
        <button type="button" class="wz-link" data-wz-focus-goal>Chọn mục tiêu khác</button>`;
    }

    // Ước tính số kỳ còn lại (Phenikaa: 3 kỳ / năm).
    const gr = a.graduation || {};
    let gradLine = "";
    if (gr.semesters_total > 0) {
      gradLine = `Ước tính còn ${gr.semesters_total} kỳ${gr.has_current ? " (tính cả kỳ đang học)" : ""}, khoảng ${gr.years} năm với ${gr.terms_per_year} kỳ/năm`
        + `${gr.finish_label ? `, xong vào ${gr.finish_label}` : ""}${gr.pace_label ? ` · cường độ ${gr.pace_label}` : ""}.`
        + `${gr.incomplete ? " Còn tín chỉ chưa xếp được nên số kỳ này chưa đầy đủ." : ""}`;
    }

    $("wzVerdict").innerHTML = `<p class="wz-error">Lỗi: ${escapeHtml(message)}</p>`;
  }

  // Bỏ lựa chọn cũ không còn hợp lệ sau khi dữ liệu thay đổi (vd sau khi crawl lại).
  function pruneSelections() {
    const a = state.advisor;
    if (!a || !state.roadmap) return;
    const improvable = new Set(a.improvement.candidates.map((c) => String(c.ma_hp)));
    const plannable = new Set(getWhatIfCandidates().map((c) => String(c.ma_hp)));
    let improveChanged = false;
    let planChanged = false;
    Object.keys(state.improve).forEach((code) => {
      if (!improvable.has(code)) { delete state.improve[code]; improveChanged = true; }
    });
    Object.keys(state.whatIfPlan).forEach((code) => {
      if (!plannable.has(code)) { delete state.whatIfPlan[code]; planChanged = true; }
    });
    if (improveChanged) writePref("paImprovePlan", state.improve);
    if (planChanged) writePref("paWhatIfPlan", state.whatIfPlan);
  }

  function getWhatIfCandidates() {
    return (state.roadmap?.mon_hoc || [])
      .filter((c) => c && c.ma_hp && PLANNABLE.includes(c.trang_thai))
      .sort((a, b) => {
        if (a.trang_thai !== b.trang_thai) return a.trang_thai === "CAN_HOC_LAI" ? -1 : 1;
        return String(a.ten_hp || "").localeCompare(String(b.ten_hp || ""), "vi");
      });
  }

  function seedPlanFromSuggestions(a) {
    const plan = {};
    (a?.suggestions?.courses || [])
      .filter((c) => PLANNABLE.includes(c.trang_thai))
      .forEach((c) => { plan[c.ma_hp] = "B"; });
    state.whatIfPlan = plan;
    writePref("paWhatIfPlan", plan);
  }

  // ---------- các bước ----------
  function baselineNeedsImprove(a) {
    const g = a?.baseline?.gpa;
    return Boolean(g && !g.feasible && g.feasible_with_improvements);
  }

  function goStep(step) {
    if (!STEPS.includes(step)) return;
    const a = state.advisor;
    let needRefresh = false;

    if (step === "improve" && seedImproveIfStale(a)) {
      needRefresh = true;   // đã tích sẵn lại các môn gợi ý theo mục tiêu hiện tại
    }
    if (step === "next" && a && !Object.keys(state.whatIfPlan).length) {
      seedPlanFromSuggestions(a);
      needRefresh = true;
    }

    state.step = step;
    renderSupport();
    if (needRefresh) refreshAdvisor();
    $("supportSection")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderSupport() {
    const a = state.advisor;
    if (!a) return;
    $("supportBody").classList.remove("hidden");
    Object.entries(PANES).forEach(([key, id]) => $(id).classList.toggle("hidden", key !== state.step));
    renderTop(a);
    ({ result: renderResult, improve: renderImprove, next: renderNext, roadmap: renderRoadmap })[state.step](a);
  }

  // Dòng cố định phía trên từ màn 3 trở đi: GPA dự kiến / mục tiêu
  function renderTop(a) {
    const top = $("wzTop");
    if (state.step !== "improve") { top.classList.add("hidden"); top.innerHTML = ""; return; }
    let projected = a.gpa.projected_final;
    // Chưa đạt thì không để làm tròn khiến "3.60 / 3.60" trông như đã đạt.
    if (projected != null && !a.gpa.feasible && projected >= goalGpa(a) - 1e-9) projected = goalGpa(a) - 0.01;
    const pct = projected != null && goalGpa(a) > 0
      ? Math.max(0, Math.min(100, (projected / goalGpa(a)) * 100)) : 0;
    const ok = !!a.gpa.feasible;
    top.classList.remove("hidden");
    top.innerHTML = `
      <strong class="wz-gpa-value">GPA ${projected != null ? fmt(projected) : "—"} / ${fmt(goalGpa(a))}</strong>
      <div class="wz-gpa-bar" role="img" aria-label="GPA dự kiến so với mục tiêu"><i style="width:${pct.toFixed(0)}%"></i></div>
      <span class="wz-gpa-status${ok ? "" : " wz-gpa-bad"}">${ok ? "đạt mục tiêu" : "chưa đạt"}</span>`;
  }

  // ---------- Màn 2: kết luận ----------
  function requiredCell(gpa) {
    const req = gpa.required_remaining_average;
    if (req == null) return { value: "—", help: "Không còn tín chỉ cần điểm", bad: false };
    if (req > 4.0 + 1e-6) return { value: fmt(req), help: "Vượt 4.0 · không khả thi", bad: true };
    return { value: fmt(req), help: gpa.required_letter ? `≈ ${gpa.required_letter} trở lên` : "", bad: false };
  }

  // Điều 27.7: học lại vượt 5% tổng tín chỉ -> hạng Giỏi/Xuất sắc bị giảm một mức.
  function retakeWarningHtml(a) {
    const r = a.retake || {};
    if (!r.downgrade_risk_for_target) return "";
    const t = a.target || {};
    const rc = a.roadmap_check || {};
    const used = formatPlannerCredits(r.detected_credits);
    const limit = formatPlannerCredits(r.threshold_credits);
    // Giỏi + học lại > 5%: lộ trình đã được xếp để GPA đạt 3.60 nên bằng vẫn ghi Giỏi -> chỉ là ghi chú.
    if (t.raised_by_retake && rc.meets_goal) {
      return `<section class="retake-warn retake-info" role="note">`
        + `<h4 class="rw-title">Lộ trình đã xếp để giữ bằng Giỏi</h4>`
        + `<p class="rw-why">Bạn đã học lại ${used} TC, vượt ngưỡng 5% (${limit} TC), `
        + `nên theo quy chế phải đạt GPA toàn khóa từ ${fmt(rc.goal_gpa)} thì bằng mới ghi Giỏi. `
        + `Lộ trình bên dưới đã tính theo mức này (dự kiến ${fmt(rc.final_gpa)}).</p>`
        + `</section>`;
    }
    // Giỏi + học lại > 5% nhưng lộ trình hiện chọn chưa đủ 3.60.
    if (t.raised_by_retake) {
      return `<section class="retake-warn" role="alert">`
        + `<h4 class="rw-title">Lộ trình hiện chưa đủ để giữ bằng Giỏi</h4>`
        + `<p class="rw-why">Bạn đã học lại ${used} TC, vượt ngưỡng 5% (${limit} TC), nên cần GPA toàn khóa từ `
        + `${fmt(rc.goal_gpa != null ? rc.goal_gpa : t.effective_gpa)}`
        + `${rc.final_gpa != null ? `, trong khi lộ trình hiện chỉ đạt ${fmt(rc.final_gpa)}` : ""}. `
        + `Hãy chọn thêm môn học cải thiện.</p>`
        + `</section>`;
    }
    const short = escapeHtml(targetShort(a.target));
    const down = escapeHtml(r.downgraded_to || "một mức");
    return `<section class="retake-warn" role="alert">`
      + `<h4 class="rw-title">Hạng ${short} có thể bị hạ xuống ${down}</h4>`
      + `<p class="rw-why">Bạn đã học lại ${formatPlannerCredits(r.detected_credits)} TC, `
      + `vượt 5% tổng số tín chỉ. `
      + `Hãy hỏi Phòng Đào tạo để chắc chắn.</p>`
      + `</section>`;
  }

  // Kết luận cho mục tiêu đang chọn — chỉ nói khi thật sự cần:
  //  - đạt được, kịp thời gian  -> không nói gì
  //  - đạt được nhưng phải ra trường muộn / học cải thiện -> 1 dòng ngắn
  //  - không thể đạt -> báo đỏ ngay
  function renderResult(a) {
    const g = a.baseline.gpa;
    const req = g.required_remaining_average;
    const short = targetShort(a.target);
    const dp = a.diploma_plan || {};
    const rtk = a.retake || {};
    const tl = a.timeline || {};
    const maxImp = g.max_final_with_all_improvements;
    const need = dp.gpa_needed_for_selected_diploma;
    const retakeHitsRank = !!(rtk.over_limit && dp.needs_ack);   // Giỏi bị hạ do học lại > 5%

    const nextBtn = `<button type="button" class="wz-btn" data-wz-go="next">Xem môn học kỳ tới</button>`;
    const improveBtn = `<button type="button" class="wz-btn" data-wz-go="improve">Xem môn nên học lại</button>`;

    let bad = "";     // không thể đạt (đỏ)
    let note = "";    // đạt được nhưng có điều kiện (chữ thường)
    let actions = "";

    if (dp.selected_diploma_unreachable) {
      bad = "Không thể đạt Xuất sắc: bạn đã học lại vượt 5% tín chỉ, hạng cao nhất trên bằng là Giỏi.";
    } else if (retakeHitsRank && need != null && !dp.true_rank_reachable_with_improve) {
      bad = `Không thể có bằng ${dp.selected_rank}: bạn đã học lại vượt 5% tín chỉ nên cần GPA từ ${fmt(need)}`
        + (maxImp != null ? `, nhưng cao nhất bạn đạt được là ${fmt(maxImp)}.` : ".");
    } else if (!g.feasible && !g.feasible_with_improvements) {
      bad = `Không thể đạt ${short}`
        + (maxImp != null ? `: dù cải thiện tối đa, GPA cao nhất bạn đạt được là ${fmt(maxImp)}.` : ".");
    } else if ((!g.feasible && g.feasible_with_improvements) || (retakeHitsRank && !dp.true_rank_reachable_plain)) {
      note = "Có thể đạt được, nhưng phải học cải thiện nên có thể ra trường muộn hơn.";
      actions = improveBtn;
    } else if (tl.likely_late_vs_on_time === true) {
      note = "Có thể đạt được nhưng phải ra trường muộn hơn.";
      actions = nextBtn;
    } else if (retakeHitsRank && need != null) {
      // Giỏi vẫn đạt được, nhưng ngưỡng thật là 3.60 chứ không phải 3.20 -> cần nói để khỏi hiểu nhầm.
      note = `Cần GPA từ ${fmt(need)} để còn bằng ${dp.selected_rank} (đã học lại vượt 5%).`;
      actions = nextBtn;
    } else if (req != null) {
      actions = nextBtn;   // đạt được, kịp thời gian: không nói gì thêm
    }

    $("wzVerdict").innerHTML = `
      ${bad ? `<p class="wz-warn wz-warn-box" role="alert">${escapeHtml(bad)}</p>` : ""}
      ${note ? `<p class="wz-p">${escapeHtml(note)}</p>` : ""}
      ${actions ? `<div class="wz-actions">${actions}</div>` : ""}`;

    // Ước tính số kỳ: chuyển xuống "Xem chi tiết" thay vì chiếm chỗ ở trên.
    const gr = a.graduation || {};
    let gradLine = "";
    if (gr.semesters_total > 0) {
      gradLine = `${gr.semesters_total} kỳ${gr.has_current ? " (tính cả kỳ này)" : ""}`
        + `${gr.finish_label ? `, dự kiến xong ${gr.finish_label}` : ""}`;
    }

    const b = a.baseline;
    const r = requiredCell(b.gpa);
    const rows = [
      ["GPA hiện tại", fmt(b.gpa.current)],
      ["Còn thiếu để tốt nghiệp", `${formatPlannerCredits(b.credits.shortfall)} TC`],
      ["Cần đạt trung bình", `${r.value}${r.help ? " · " + r.help : ""}`],
    ];
    const rc = a.roadmap_check;
    if (rc && rc.final_gpa != null) {
      rows.push(["GPA theo lộ trình", `${fmt(rc.final_gpa)} / ${fmt(rc.goal_gpa)} · ${rc.meets_goal ? "đạt" : "chưa đạt mục tiêu"}`]);
    }
    if (gradLine) rows.push(["Dự kiến tốt nghiệp", gr.finish_label || gradLine]);
    // Các thông tin phụ: chỉ hiện khi thật sự đáng chú ý.
    const rt = a.retake || {};
    if (rt.history_complete && rt.over_limit) {
      rows.push(["Học lại", `${formatPlannerCredits(rt.detected_credits)} / ${formatPlannerCredits(rt.threshold_credits)} TC · vượt ngưỡng 5%`]);
    }
    const ac = a.academic || {};
    if (ac.available) {
      if (ac.warned_before) rows.push(["Cảnh báo học vụ", `đã bị ${ac.warned_semesters.length} kỳ`]);
      if (ac.debt_credits > 0 && ac.debt_limit && ac.debt_credits / ac.debt_limit >= 0.75) {
        rows.push(["Nợ đọng", `${formatPlannerCredits(ac.debt_credits)} / ${ac.debt_limit} TC`]);
      }
    }
    $("wzDetails").innerHTML = rows.map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd></div>`).join("");

    const warn = $("analysisWarnings");
    if (a.warnings && a.warnings.length) {
      warn.innerHTML = a.warnings.map((w) => `<span>${escapeHtml(w)}</span>`).join("");
      warn.classList.remove("hidden");
    } else {
      warn.classList.add("hidden");
      warn.innerHTML = "";
    }
  }

  // ---------- Màn 3: môn nên học lại ----------
  function renderImprove(a) {
    const rec = a.recommend || {};
    const imp = a.improvement;
    const cands = imp.candidates;
    const body = $("wzImproveBody");
    if (!cands.length) {
      body.innerHTML = `<h2 class="wz-h">Không có môn nào để học lại.</h2><p class="wz-p">Không có môn nào điểm từ ${imp.max_point >= 3 ? "B" : "C+"} trở xuống.</p>`;
      return;
    }
    const recCodes = new Set((rec.courses || []).map((c) => String(c.ma_hp)));
    const recList = cands.filter((c) => recCodes.has(String(c.ma_hp)));
    const others = cands.filter((c) => !recCodes.has(String(c.ma_hp)));
    const letter = rec.letter || DEFAULT_IMPROVE_LETTER;
    const title = rec.reachable && recList.length
      ? `Học lại ${recList.length} môn này là đạt ${targetShort(a.target)}.`
      : "Chọn môn muốn học lại.";

    const row = (c) => {
      const code = escapeHtml(String(c.ma_hp));
      const checked = Boolean(state.improve[String(c.ma_hp)]);
      return `
        <div class="wz-row">
          <input id="imp-${code}" type="checkbox" data-improve-code="${code}" ${checked ? "checked" : ""}>
          <label for="imp-${code}">${escapeHtml(c.ten_hp)}</label>
          <span class="wz-muted">Điểm hiện tại ${escapeHtml(c.diem_chu || "—")}${c.diem != null ? ` · ${escapeHtml(c.diem)}` : ""}</span>
        </div>`;
    };

    const first = recList.length ? recList : others.slice(0, 6);
    const rest = recList.length ? others : others.slice(6);
    const open = state.improveOpen;
    const candCodes = new Set(cands.map((c) => String(c.ma_hp)));
    const selectedCount = Object.keys(state.improve).filter((k) => candCodes.has(String(k))).length;
    const totalCredits = cands.filter((c) => state.improve[String(c.ma_hp)])
      .reduce((sum, c) => sum + (Number(c.so_tin) || 0), 0);
    const summary = `${selectedCount} môn đã chọn${totalCredits ? ` · ${formatPlannerCredits(totalCredits)} TC` : ""}`;
    body.innerHTML = `
      <h2 class="wz-h">${escapeHtml(title)}</h2>
      <button type="button" class="wz-fold" data-wz-toggle-improve aria-expanded="${open ? "true" : "false"}">
        <span>${escapeHtml(summary)}</span>
        <span class="wz-fold-act">${open ? "Thu gọn ▴" : "Xem danh sách ▾"}</span>
      </button>
      ${open ? `
        <div class="wz-list wz-scroll">${first.map(row).join("")}</div>
        ${rest.length ? `
          <button type="button" class="wz-link" data-wz-more-improve>${state.showMoreImprove ? "Ẩn bớt" : "Thêm môn khác"}</button>
          ${state.showMoreImprove ? `<div class="wz-list wz-scroll">${rest.map(row).join("")}</div>` : ""}` : ""}` : ""}
      ${a.gpa.feasible ? "" : `<p class="wz-warn">Với các môn đang chọn, bạn chưa đủ ${escapeHtml(targetShort(a.target))}. Hãy chọn thêm môn học lại.</p>`}
      <p class="wz-note">Tính theo giả định mỗi môn học lại đạt ${escapeHtml(letter)}. Điểm mới thay điểm cũ, không cộng thêm tín chỉ. Hãy đối chiếu điều kiện học cải thiện với quy chế đào tạo của Phenikaa.</p>
      <div class="wz-actions"><button type="button" class="wz-btn" data-wz-go="next">Tiếp tục</button></div>`;
  }

  // ---------- Màn 4: môn học kỳ tới ----------
  function setPaceUi() {
    document.querySelectorAll("#paceOpts .wz-opt").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.pace === state.studyPace);
    });
  }

  function setStudyPace(pace) {
    if (!PACE_KEYS.includes(pace) || pace === state.studyPace) return;
    state.studyPace = pace;
    writePref("paStudyPace", pace);
    setPaceUi();
    state.reseedPlan = true;
    refreshAdvisor();
  }

  function renderNext(a) {
    setPaceUi();
    const s = a.suggestions;
    const c = a.credits;
    const inPlan = (code) => Object.prototype.hasOwnProperty.call(state.whatIfPlan, String(code));
    const body = $("wzNextBody");

    const row = (course, { disabled = false } = {}) => {
      const code = escapeHtml(String(course.ma_hp));
      return `
        <div class="wz-row ${disabled ? "is-disabled" : ""}">
          <input id="pl-${code}" type="checkbox" data-plan-code="${code}" ${inPlan(course.ma_hp) ? "checked" : ""} ${disabled ? "disabled" : ""}>
          <label for="pl-${code}">${escapeHtml(course.ten_hp)}</label>
          <span class="wz-muted">${formatPlannerCredits(course.so_tin)} TC</span>
        </div>`;
    };

    if (!s.courses.length) {
      body.innerHTML = `<p class="wz-p">${escapeHtml(s.message || "Hiện chưa có môn nào phù hợp để gợi ý.")}</p>
        <div class="wz-actions"><button type="button" class="wz-btn" data-wz-go="roadmap">Xem lộ trình</button></div>`;
      return;
    }

    const sugCodes = new Set(s.courses.map((x) => String(x.ma_hp)));
    const others = getWhatIfCandidates().filter((x) => !sugCodes.has(String(x.ma_hp)));
    const waiting = s.courses.filter((x) => !PLANNABLE.includes(x.trang_thai)).length;
    const newTotal = Number(c.planned_next_total || 0);
    const improveLoad = Number(c.improve_load || 0);

    body.innerHTML = `
      <div class="wz-list">
        ${s.courses.map((x) => row(x, { disabled: !PLANNABLE.includes(x.trang_thai) })).join("")}
        <div class="wz-row wz-total"><strong>Tổng</strong><strong>${formatPlannerCredits(c.next_load)} TC</strong></div>
      </div>
      ${improveLoad > 0 ? `<p class="wz-note">Gồm ${formatPlannerCredits(newTotal)} TC môn mới và ${formatPlannerCredits(improveLoad)} TC học lại.</p>` : ""}
      ${waiting ? `<p class="wz-note">${waiting} môn chưa thêm được vì phụ thuộc môn đang học.</p>` : ""}
      ${others.length ? `
        <button type="button" class="wz-link" data-wz-more-plan>${state.showMorePlan ? "Ẩn bớt" : "Thêm môn khác"}</button>
        ${state.showMorePlan ? `<div class="wz-list wz-scroll">${others.map((x) => row(x)).join("")}</div>` : ""}` : ""}
      <div class="wz-actions"><button type="button" class="wz-btn" data-wz-go="roadmap">Xem lộ trình</button></div>`;
  }

  // ---------- Màn 5: lộ trình ----------
  function wzSemesterHtml(semester) {
    const courses = semester.courses || [];
    const improvements = semester.improvements || [];
    if (!courses.length && !improvements.length) return "";
    const rows = courses.map((c) => {
      const grade = c.grade_target;
      return `<div class="wz-sem-row"><span>${escapeHtml(c.ten_hp)}</span><span class="wz-muted">${formatPlannerCredits(c.so_tin)} TC${grade ? ` · ${escapeHtml(grade.letter)}` : ""}</span></div>`;
    }).join("");
    const impRows = improvements.map((i) =>
      `<div class="wz-sem-row"><span>${escapeHtml(i.ten_hp)} <em>(học lại)</em></span><span class="wz-muted">${formatPlannerCredits(i.so_tin)} TC · ${escapeHtml(i.from)} → ${escapeHtml(i.to)}</span></div>`).join("");
    return `
      <details class="wz-sem">
        <summary><span>${escapeHtml(semester.label)}</span><span class="wz-muted">${formatPlannerCredits(semester.total_credits)} TC ›</span></summary>
        <div class="wz-sem-list">${rows}${impRows}</div>
      </details>`;
  }

  // Nhãn học kỳ thật (HK2 2026-2027) cho kỳ thứ `offset` sau học kỳ có điểm gần nhất.
  function termLabelAfterLast(offset) {
    const hk = state.roadmap?.hoc_vu?.hoc_ky || [];
    if (!hk.length) return "";
    const last = hk[hk.length - 1];
    const idx = parseInt(last.nam_hoc, 10) * TERMS_PER_YEAR_UI + (Number(last.ky) - 1) + offset;
    if (Number.isNaN(idx)) return "";
    const year = Math.floor(idx / TERMS_PER_YEAR_UI);
    return `HK${(idx % TERMS_PER_YEAR_UI) + 1} ${year}-${year + 1}`;
  }

  function wzTimelineItemHtml(semester, term, { isNow, isEnd, maxCredits, label }) {
    const courses = semester.courses || [];
    const improvements = semester.improvements || [];
    const rows = courses.map((c) => {
      const grade = c.grade_target;
      return `<div class="wz-sem-row"><span>${escapeHtml(c.ten_hp)}</span><span class="wz-muted">${formatPlannerCredits(c.so_tin)} TC${grade ? ` · ${escapeHtml(grade.letter)}` : ""}</span></div>`;
    }).join("");
    const impRows = improvements.map((i) =>
      `<div class="wz-sem-row"><span>${escapeHtml(i.ten_hp)} <em>(học lại)</em></span><span class="wz-muted">${formatPlannerCredits(i.so_tin)} TC · ${escapeHtml(i.from)} → ${escapeHtml(i.to)}</span></div>`).join("");
    const credits = Number(semester.total_credits) || 0;
    const pct = maxCredits > 0 ? Math.min(100, (credits / maxCredits) * 100) : 0;
    return `
      <details class="wz-tl-item${isNow ? " wz-tl-now" : ""}${isEnd ? " wz-tl-end" : ""}">
        <summary>
          <span class="wz-tl-name"><b>${escapeHtml(label || semester.label)}</b>${term ? `<small>${escapeHtml(term)}</small>` : ""}</span>
          ${isEnd ? `<span class="wz-tl-tag">Tốt nghiệp</span>` : ""}
          <span class="wz-tl-bar" aria-hidden="true"><i style="width:${pct.toFixed(0)}%"></i></span>
          <span class="wz-tl-tc">${formatPlannerCredits(credits)} TC</span>
          <span class="wz-tl-chev" aria-hidden="true">›</span>
        </summary>
        <div class="wz-sem-list">${rows}${impRows}</div>
      </details>`;
  }

  function renderRoadmap(a) {
    const rm = a.roadmap;
    const c = a.credits;
    const shown = rm.semesters
      .map((sem, i) => ({ sem, term: termLabelAfterLast(i + 1) }))
      .filter(({ sem }) => (sem.courses || []).length || (sem.improvements || []).length);
    // Đánh số lại theo thứ tự thật để nhãn không bao giờ nhảy cóc (Kỳ +6 -> Kỳ +8).
    let futureN = 0;
    shown.forEach((item) => {
      if (item.sem.kind === "current") { item.label = "Kỳ hiện tại"; return; }
      futureN += 1;
      item.label = futureN === 1 ? "Kỳ tới" : `Kỳ +${futureN}`;
    });
    const maxCredits = Math.max(16, ...shown.map(({ sem }) => Number(sem.total_credits) || 0));
    const semesters = shown.length
      ? `<div class="wz-tl">${shown.map(({ sem, term, label }, i) => wzTimelineItemHtml(sem, term, {
          isNow: sem.kind === "current", isEnd: i === shown.length - 1, maxCredits, label,
        })).join("")}</div>`
      : "";
    const lastShown = shown[shown.length - 1];
    const totalCredits = shown.reduce((sum, { sem }) => sum + (Number(sem.total_credits) || 0), 0);
    const summary = lastShown
      ? `<p class="wz-p wz-tl-sum">Dự kiến tốt nghiệp</p><p class="wz-big">${escapeHtml(lastShown.term || `sau ${lastShown.label || lastShown.sem.label}`)}</p>`
      : "";
    const unscheduled = Number(c.unscheduled || 0);

    const risk = retakeWarningHtml(a);

    $("wzRoadmapBody").innerHTML = `
      <div class="print-only wz-print-head">
        <h1>Lộ trình tốt nghiệp</h1>
        <p>Mục tiêu: ${escapeHtml(targetShort(a.target))} · Ngày xuất: ${new Date().toLocaleDateString("vi-VN")}</p>
      </div>
      ${summary}
      ${semesters || `<p class="wz-p">Không còn học phần nào để lập lộ trình.</p>`}
      ${unscheduled > 0 ? `<p class="wz-warn">Còn ${formatPlannerCredits(unscheduled)} TC chưa xếp được vì thiếu môn khả dụng hoặc môn tiên quyết.</p>` : ""}
      ${risk}
      ${shown.length ? `<div class="wz-actions wz-noprint"><button type="button" class="wz-btn" data-wz-export-pdf>Xuất PDF</button></div>` : ""}`;
  }

  // Xuất PDF: dùng hộp thoại in của trình duyệt (chọn "Lưu dưới dạng PDF").
  // Chỉ in phần lộ trình, mở sẵn mọi kỳ để PDF có đủ danh sách môn.
  function exportRoadmapPdf() {
    const target = $("wzRoadmapBody");
    if (!target) return;
    const details = Array.from(target.querySelectorAll("details"));
    const wasOpen = details.map((d) => d.open);
    details.forEach((d) => { d.open = true; });

    const hidden = [];
    for (let node = target; node && node !== document.body; node = node.parentElement) {
      Array.from(node.parentElement.children).forEach((sib) => {
        if (sib !== node && sib.tagName !== "SCRIPT") { sib.classList.add("print-hide"); hidden.push(sib); }
      });
    }
    const oldTitle = document.title;
    document.title = "Lo-trinh-tot-nghiep";
    document.body.classList.add("print-roadmap");

    const restore = () => {
      document.body.classList.remove("print-roadmap");
      hidden.forEach((el) => el.classList.remove("print-hide"));
      details.forEach((d, i) => { d.open = wasOpen[i]; });
      document.title = oldTitle;
    };
    window.addEventListener("afterprint", restore, { once: true });
    window.print();
  }

  // ---------- sự kiện ----------
  $("supportSection")?.addEventListener("click", (event) => {
    const t = event.target;
    if (t.closest("[data-wz-rank-ack]")) {
      state.rankAck = true;
      state.planMode = null;
      renderSupport();
      return;
    }
    const modeBtn = t.closest("[data-wz-plan-mode]");
    if (modeBtn) {
      state.planMode = modeBtn.dataset.wzPlanMode || null;
      // Đúng hạn: bỏ lựa chọn học cải thiện để không ép môn học lại.
      if (state.planMode === "on_time") {
        state.improve = {};
        writePref("paImprovePlan", state.improve);
        refreshAdvisor(false);
        return;
      }
      renderSupport();
      return;
    }
    const go = t.closest("[data-wz-go]");
    if (go) { goStep(go.dataset.wzGo); return; }
    if (t.closest("[data-wz-export-pdf]")) { exportRoadmapPdf(); return; }
    if (t.closest("[data-wz-back-from-next]")) {
      goStep(baselineNeedsImprove(state.advisor) && state.planMode !== "on_time" ? "improve" : "result");
      return;
    }
    const pick = t.closest("[data-wz-target]");
    if (pick) {
      const rank = RANKS.find((r) => r.key === pick.dataset.wzTarget);
      if (rank) setTargetGpa(rank.gpa);
      return;
    }
    if (t.closest("[data-wz-focus-goal]")) {
      $("goalRanks")?.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    const rankBtn = t.closest(".wz-pick");
    if (rankBtn) {
      const rank = RANKS.find((r) => r.key === rankBtn.dataset.rank);
      if (rank) setTargetGpa(rank.gpa);
      return;
    }
    const pace = t.closest("#paceOpts .wz-opt");
    if (pace) { setStudyPace(pace.dataset.pace); return; }
    if (t.closest("[data-wz-toggle-improve]")) { state.improveOpen = !state.improveOpen; renderSupport(); return; }
    if (t.closest("[data-wz-more-improve]")) { state.showMoreImprove = !state.showMoreImprove; renderSupport(); return; }
    if (t.closest("[data-wz-more-plan]")) { state.showMorePlan = !state.showMorePlan; renderSupport(); }
  });

  $("supportSection")?.addEventListener("change", (event) => {
    const imp = event.target.closest("input[data-improve-code]");
    if (imp) {
      const code = String(imp.dataset.improveCode || "");
      if (!code) return;
      if (imp.checked) state.improve[code] = state.advisor?.recommend?.letter || DEFAULT_IMPROVE_LETTER;
      else delete state.improve[code];
      writePref("paImprovePlan", state.improve);
      refreshAdvisor(false);
      return;
    }
    const plan = event.target.closest("input[data-plan-code]");
    if (plan) {
      const code = String(plan.dataset.planCode || "");
      if (!code) return;
      if (plan.checked) { if (!state.whatIfPlan[code]) state.whatIfPlan[code] = "B"; }
      else delete state.whatIfPlan[code];
      writePref("paWhatIfPlan", state.whatIfPlan);
      refreshAdvisor(false);
    }
  });

  function normalizeSearch(value) {
    return (value || "").toString().normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
  }

  function gradeCell(c) {
    if (!c.diem_chu && c.diem == null) return "—";
    return `<b>${escapeHtml(c.diem_chu || "")}</b>${c.diem != null ? ` · ${escapeHtml(c.diem)}` : ""}`;
  }

  const EMPTY_COURSES = {
    CAN_HOC_LAI: "Chưa có môn nào phải học lại (môn trượt). Muốn nâng điểm môn đã đạt? Dùng Học cải thiện trong Hỗ trợ học tập.",
    HOAN_THANH: "Chưa có môn nào đã hoàn thành.",
  };

  // Tên dễ đọc của khối kiến thức (D1/D2 dùng chung tên "Chuyên ngành" nên tách bắt buộc/tự chọn).
  function blockDisplayName(ma) {
    const code = String(ma || "").toUpperCase();
    if (code === "D1" || code.startsWith("D1.")) return "Chuyên ngành bắt buộc";
    if (code === "D2" || code.startsWith("D2.")) return "Chuyên ngành tự chọn";
    const ten = String(state.roadmap?.khoi_summary?.[ma]?.ten_khoi || "").replace(/^Khối kiến thức\s*/i, "").trim();
    return ten ? ten.charAt(0).toUpperCase() + ten.slice(1) : (ma || "—");
  }

  // Tab "Chương trình học": mỗi khối kiến thức một thẻ riêng, liệt kê các môn kèm trạng thái.
  function renderCurriculum(all) {
    const summary = state.roadmap?.khoi_summary || {};
    const byBlock = new Map();
    all.forEach((c) => {
      const key = c.khoi_kt || "—";
      if (!byBlock.has(key)) byBlock.set(key, []);
      byBlock.get(key).push(c);
    });
    const keys = [...byBlock.keys()].filter((k) => !summary[k]?.bo_qua).sort();
    document.getElementById("coursesResultMeta").textContent = `${keys.length} khối · ${all.length} học phần`;
    const wrap = document.getElementById("coursesTableWrap");
    if (!keys.length) {
      wrap.innerHTML = `<div class="empty-state-small">Chưa có dữ liệu chương trình đào tạo.</div>`;
      return;
    }
    const chipOf = (c) => {
      const s = c.trang_thai;
      const text = s === "BI_KHOA" ? "Chưa đủ điều kiện" : (STATUS_LABEL[s]?.text || s || "");
      const cls = s === "HOAN_THANH" ? "is-ok" : s === "CAN_HOC_LAI" ? "is-bad" : s === "BI_KHOA" ? "is-warn" : "";
      return text ? `<span class="gx-chip ${cls}">${escapeHtml(text)}</span>` : "";
    };
    if (!keys.includes(state.curBlock)) state.curBlock = keys[0];
    const tabs = keys.map((ma) => `<button type="button" class="cur-tab${ma === state.curBlock ? " active" : ""}" data-cur-block="${escapeHtml(ma)}" aria-pressed="${ma === state.curBlock}"><b>${escapeHtml(ma)}</b> ${escapeHtml(blockDisplayName(ma))}</button>`).join("");
    wrap.innerHTML = `<div class="cur-tabs">${tabs}</div><div class="cur-grid">${keys.filter((ma) => ma === state.curBlock).map((ma) => {
      const list = byBlock.get(ma).slice().sort((a, b) => String(a.ten_hp || "").localeCompare(String(b.ten_hp || ""), "vi"));
      const g = summary[ma] || {};
      const required = Number(g.tin_bat_buoc ?? g.tong_tin ?? 0) || 0;
      const accumulated = Number(g.da_tich_luy ?? 0) || 0;
      const progress = required > 0 ? `${formatPlannerCredits(accumulated)} / ${formatPlannerCredits(required)} TC` : `${list.length} môn`;
      const done = required > 0 && accumulated >= required;
      return `<section class="cur-card${done ? " is-done" : ""}">
        <header><div><h3>${escapeHtml(blockDisplayName(ma))}</h3><span class="group-chip">${escapeHtml(ma)}</span></div><strong class="mono">${escapeHtml(progress)}</strong></header>
        <div class="cur-list">${list.map((c) => `<div class="cur-row"><span class="cur-name">${escapeHtml(c.ten_hp || "Học phần chưa rõ tên")}</span><span class="gx-muted">${escapeHtml(formatPlannerCredits(c.so_tin))} TC</span>${c.diem_chu ? `<b class="mono">${escapeHtml(c.diem_chu)}</b>` : ""}${chipOf(c)}</div>`).join("")}</div>
      </section>`;
    }).join("")}</div>`;
  }

  function renderCourses() {
    const all = state.roadmap?.mon_hoc || [];
    const query = normalizeSearch(state.courseSearch);
    const activeStatus = state.currentFilter || "DANG_KY_KY_NAY";
    const searching = Boolean(query);

    if (!searching && activeStatus === "CHUONG_TRINH") { renderCurriculum(all); return; }

    // Có từ khóa -> tra cứu trên mọi nhóm; không có -> lọc theo tab.
    const filtered = searching
      ? all.filter((c) => normalizeSearch(`${c.ten_hp} ${c.khoi_kt}`).includes(query))
      : all.filter((c) => c.trang_thai === activeStatus);

    document.getElementById("coursesResultMeta").textContent = searching
      ? `${filtered.length} kết quả trên mọi nhóm`
      : `${filtered.length} học phần`;

    const wrap = document.getElementById("coursesTableWrap");
    if (!filtered.length) {
      wrap.innerHTML = `<div class="empty-state-small">${escapeHtml(searching ? "Không tìm thấy học phần phù hợp." : (EMPTY_COURSES[activeStatus] || "Không có học phần trong nhóm này."))}</div>`;
      return;
    }

    const nameCell = (c) => `
        <td>
          <strong class="course-name-cell">${escapeHtml(c.ten_hp)}</strong>
        </td>`;

    if (!searching && activeStatus === "BI_KHOA") {
      const courseNameByCode = new Map(all.map((course) => [String(course.ma_hp || "").trim(), course.ten_hp || "Học phần chưa rõ tên"]));
      const rows = filtered.map((c) => {
        const missingNames = (c.mon_con_thieu || []).map((code) => courseNameByCode.get(String(code || "").trim()) || "Học phần chưa rõ tên");
        const missingHtml = missingNames.length
          ? missingNames.map((name) => `<span class="missing-course-name">${escapeHtml(name)}</span>`).join("")
          : `<span class="muted-copy">Chưa xác định điều kiện còn thiếu</span>`;
        return `<tr>${nameCell(c)}<td class="mono">${c.so_tin ?? ""}</td><td class="course-note-cell">${missingHtml}</td></tr>`;
      }).join("");
      wrap.innerHTML = `
        <table class="courses-table courses-table-compact">
          <thead><tr><th>Học phần</th><th>TC</th><th>Cần hoàn thành trước</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>`;
      return;
    }

    // Cột thứ hai: trạng thái khi tra cứu, khối kiến thức khi lọc theo tab.
    const showGrade = searching || activeStatus === "HOAN_THANH";
    const second = searching ? "Trạng thái" : "Thuộc khối";
    const rows = filtered.map((c) => {
      const secondCell = searching
        ? `<span class="status-pill"><i class="status-dot ${(STATUS_LABEL[c.trang_thai]?.dot) || "khong-bb"}"></i>${escapeHtml(STATUS_LABEL[c.trang_thai]?.text || c.trang_thai)}</span>`
        : `<span class="block-tag">${escapeHtml(blockDisplayName(c.khoi_kt))}</span>`;
      return `<tr>${nameCell(c)}<td>${secondCell}</td><td class="mono">${c.so_tin ?? ""}</td>${showGrade ? `<td class="mono">${gradeCell(c)}</td>` : ""}</tr>`;
    }).join("");
    wrap.innerHTML = `
      <table class="courses-table courses-table-compact">
        <thead><tr><th>Học phần</th><th>${second}</th><th>TC</th>${showGrade ? "<th>Điểm</th>" : ""}</tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  function setCourseFilter(status) {
    state.currentFilter = status || "DANG_KY_KY_NAY";
    document.querySelectorAll(".filter-tab").forEach((t) => t.classList.toggle("active", t.dataset.status === state.currentFilter));
    renderCourses();
  }

  document.getElementById("filterTabs")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".filter-tab");
    if (!btn) return;
    setCourseFilter(btn.dataset.status);
  });

  document.getElementById("courseSearch")?.addEventListener("input", (e) => {
    state.courseSearch = e.target.value;
    renderCourses();
  });

  // ---------- Academic Simulator: plans persisted by the backend session ----------
  let simSemesters = [{ label: "Học kỳ dự kiến 1", course_ids: [] }];
  let simSavedPlans = [];
  let simCurrentPlanId = null;
  let simCurrentPlanName = "Kế hoạch của tôi";

  function simEscape(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }

  function simCoursePool() {
    const courses = Array.isArray(state.roadmap?.mon_hoc) ? state.roadmap.mon_hoc : [];
    return courses.filter((c) => {
      const code = String(c.ma_hp || "").trim();
      const status = String(c.trang_thai || "").trim().toUpperCase();
      return code && !["HOAN_THANH", "DANG_KY_KY_NAY", "KHONG_BAT_BUOC"].includes(status)
        && !c.khong_tinh_tin_chi && !c.khong_tinh_tin_chi_hoc_vu;
    });
  }

  function simPrerequisites(course) {
    const raw = course?.tien_quyet;
    const values = Array.isArray(raw) ? raw : (typeof raw === 'string' ? raw.split(/[;,\n]/) : []);
    const missingFromSource = Array.isArray(course?.mon_con_thieu) ? course.mon_con_thieu : [];
    return [...new Set([...values, ...missingFromSource].map((v) => String(v || '').trim()).filter(Boolean))];
  }

  function simEligibility(course, semesterIndex) {
    const all = state.roadmap?.mon_hoc || [];
    const passed = new Set(all.filter((c) => c.trang_thai === 'HOAN_THANH').map((c) => String(c.ma_hp)));
    const earlier = new Set();
    simSemesters.slice(0, semesterIndex).forEach((sem) => (sem.course_ids || []).forEach((code) => earlier.add(String(code))));
    const missing = simPrerequisites(course).filter((code) => !passed.has(code) && !earlier.has(code));
    // If QLĐT marks a course as locked but does not expose its prerequisite codes,
    // do not silently unlock it in the planner.
    const unknownLock = course.trang_thai === 'BI_KHOA' && missing.length === 0 && simPrerequisites(course).length === 0;
    return { eligible: missing.length === 0 && !unknownLock, missing: unknownLock ? ['Điều kiện khóa chưa có trong dữ liệu'] : missing };
  }

  function renderSimSemesters() {
    const wrap = document.getElementById("simSemesters");
    if (!wrap) return;
    const pool = simCoursePool();
    if (!pool.length) {
      wrap.innerHTML = `<div class="sim-empty-state">Chưa có danh sách môn học để lập kế hoạch. Hãy vào <strong>Cập nhật dữ liệu QLĐT</strong> để đồng bộ chương trình đào tạo và bảng điểm.</div>`;
      updateSimPlanStatus();
      return;
    }
    wrap.innerHTML = simSemesters.map((sem, index) => {
      const selected = new Set((sem.course_ids || []).map(String));
      // A course can appear in only one planned semester. Hide it from every other semester.
      const selectedElsewhere = new Set(simSemesters
        .filter((other, otherIndex) => otherIndex !== index)
        .flatMap((other) => (other.course_ids || []).map(String)));
      const eligibility = new Map(pool.map((c) => [String(c.ma_hp), simEligibility(c, index)]));
      const available = pool.filter((c) => !selectedElsewhere.has(String(c.ma_hp)) && (eligibility.get(String(c.ma_hp)).eligible || selected.has(String(c.ma_hp))));
      const locked = pool.filter((c) => !selectedElsewhere.has(String(c.ma_hp)) && !eligibility.get(String(c.ma_hp)).eligible && !selected.has(String(c.ma_hp)));
      const credits = pool.filter((c) => selected.has(String(c.ma_hp))).reduce((sum, c) => sum + (Number(c.so_tin) || 0), 0);
      const rows = available.map((c) => `<label class="sim-course-row"><input type="checkbox" class="sim-course-check" value="${simEscape(c.ma_hp)}" data-credits="${Number(c.so_tin) || 0}" ${selected.has(String(c.ma_hp)) ? "checked" : ""}><span class="sim-course-row-copy"><strong>${simEscape(c.ten_hp || "Học phần chưa rõ tên")}</strong><small>${Number(c.so_tin) || 0} TC</small></span></label>`).join("");
      const lockedDetails = locked.length ? `<details class="sim-locked-courses"><summary>Xem ${locked.length} môn chưa đủ điều kiện</summary><ul>${locked.map((c) => {
        const missing = eligibility.get(String(c.ma_hp)).missing;
        const names = new Map(pool.map((item) => [String(item.ma_hp), item.ten_hp || "Học phần chưa rõ tên"]));
        return `<li><strong>${simEscape(c.ten_hp || "Học phần chưa rõ tên")}</strong> — cần hoàn thành trước: ${missing.map((code) => simEscape(names.get(code) || "môn tiên quyết")).join(', ')}</li>`;
      }).join("")}</ul></details>` : "";
      return `<article class="sim-semester" data-sim-index="${index}">
        <div class="sim-semester-head"><div class="sim-semester-title"><strong>Học kỳ ${index + 1}</strong><span class="sim-credit-pill">${credits} TC</span></div><button class="text-action sim-remove-semester" type="button" data-index="${index}" ${simSemesters.length <= 1 ? "disabled" : ""} aria-label="Xóa học kỳ ${index + 1}">Xóa</button></div>
        <input class="sim-course-search" type="search" placeholder="Tìm môn học..." aria-label="Tìm môn học">
        <div class="sim-course-list">${rows || `<p class="muted-copy">Không có môn nào đủ điều kiện ở học kỳ này.</p>`}</div>
        ${lockedDetails}
      </article>`;
    }).join("");
    wrap.querySelectorAll(".sim-course-check").forEach((checkbox) => checkbox.addEventListener("change", () => {
      const index = Number(checkbox.closest("[data-sim-index]").dataset.simIndex);
      const ids = new Set((simSemesters[index].course_ids || []).map(String));
      const poolMap = new Map(pool.map((course) => [String(course.ma_hp), Number(course.so_tin) || 0]));
      const currentCredits = [...ids].reduce((sum, code) => sum + (poolMap.get(code) || 0), 0);
      if (checkbox.checked && currentCredits + (Number(checkbox.dataset.credits) || 0) > 16) {
        checkbox.checked = false;
        const status = document.getElementById("simPlanStatus");
        if (status) status.textContent = "Mỗi học kỳ tối đa 16 tín chỉ. Bỏ chọn một môn trước khi thêm môn khác.";
        return;
      }
      if (checkbox.checked) ids.add(checkbox.value); else ids.delete(checkbox.value);
      simSemesters[index].course_ids = [...ids];
      renderSimSemesters();
    }));
    wrap.querySelectorAll(".sim-course-search").forEach((input) => input.addEventListener("input", () => {
      const query = input.value.trim().toLocaleLowerCase('vi');
      input.closest(".sim-semester").querySelectorAll(".sim-course-row").forEach((row) => { row.hidden = !row.textContent.toLocaleLowerCase('vi').includes(query); });
    }));
    wrap.querySelectorAll(".sim-remove-semester").forEach((button) => button.addEventListener("click", () => {
      simSemesters.splice(Number(button.dataset.index), 1); renderSimSemesters();
    }));
    updateSimPlanStatus();
  }

  function updateSimPlanStatus() {
    const target = document.getElementById("simPlanStatus");
    if (!target) return;
    const pool = simCoursePool();
    const summaries = simSemesters.map((sem) => {
      const selected = new Set((sem.course_ids || []).map(String));
      return pool.filter((course) => selected.has(String(course.ma_hp))).reduce((sum, course) => sum + (Number(course.so_tin) || 0), 0);
    });
    const total = summaries.reduce((sum, credits) => sum + credits, 0);
    const invalid = summaries.filter((credits) => credits > 16).length;
    target.textContent = `${total} tín chỉ đã chọn · Mỗi kỳ 8–16 TC (kỳ còn dưới 8 TC cuối chương trình được miễn mức tối thiểu)${invalid ? ` · ${invalid} kỳ vượt 16 TC` : ""}`;
    target.classList.toggle("is-invalid", invalid > 0);
  }

  function currentSimPlan() {
    return { name: simCurrentPlanName, semesters: simSemesters.map((s, i) => ({ label: (s.label || `Học kỳ ${i + 1}`).trim(), course_ids: [...new Set(s.course_ids || [])] })) };
  }

  function renderSimValidation(validation) {
    const target = document.getElementById("simResults");
    if (!target || !validation) return;
    const errors = validation.errors || [], warnings = validation.warnings || [], summary = validation.summary || {};
    target.innerHTML = `<div class="sim-result-summary"><div><span>Học kỳ</span><strong>${Number(summary.semester_count) || 0}</strong></div><div><span>Môn đã xếp</span><strong>${Number(summary.planned_courses) || 0}</strong></div><div><span>Tín chỉ dự kiến</span><strong>${Number(summary.planned_credits) || 0}</strong></div><div><span>Môn bắt buộc chưa xếp*</span><strong>${Number(summary.remaining_mandatory_courses_in_source) || 0}</strong></div></div>
      <p class="sim-result-note">*Đếm theo các môn trong dữ liệu nguồn; chưa phải kết luận chính thức về điều kiện tốt nghiệp.</p>
      ${errors.length ? `<div class="sim-message sim-error"><strong>Cần sửa ${errors.length} lỗi</strong><ul>${errors.map((x) => `<li>${simEscape(x)}</li>`).join("")}</ul></div>` : `<div class="sim-message sim-success"><strong>Không phát hiện lỗi tiên quyết trong kế hoạch theo dữ liệu hiện có.</strong></div>`}
      ${warnings.length ? `<div class="sim-message sim-warning"><strong>Lưu ý</strong><ul>${warnings.map((x) => `<li>${simEscape(x)}</li>`).join("")}</ul></div>` : ""}
      <div class="sim-result-semesters">${(validation.semesters || []).map((s) => `<div class="sim-result-term"><strong>${simEscape(s.label)}</strong><span>${s.total_credits} TC</span><p>${s.courses.map((c) => simEscape(c.ten_hp || "Học phần")).join(", ") || "Chưa chọn môn"}</p></div>`).join("")}</div>`;
  }

  async function validateSimPlan() {
    const { status, body } = await api("/api/study-plans/simulate", { method: "POST", body: JSON.stringify(currentSimPlan()) });
    if (status >= 400 || !body.success) throw new Error(body.error || "Không thể kiểm tra kế hoạch.");
    renderSimValidation(body.data);
    return body.data;
  }

  async function loadSimSavedPlans() {
    const wrap = document.getElementById("simSavedPlans");
    if (!wrap) return;
    try {
      const { status, body } = await api("/api/study-plans");
      if (status >= 400 || !body.success) throw new Error(body.error || "Không tải được kế hoạch.");
      simSavedPlans = body.data || [];
      wrap.innerHTML = simSavedPlans.length ? simSavedPlans.map((plan) => `<article class="sim-saved-plan"><label class="sim-compare-choice"><input type="checkbox" class="sim-compare-check" value="${simEscape(plan.id)}" aria-label="Chọn ${simEscape(plan.name)} để so sánh"><span></span></label><div class="sim-saved-plan-copy"><strong>${simEscape(plan.name)}</strong><p>${(plan.semesters || []).length} học kỳ · Cập nhật ${simEscape((plan.updated_at || "").slice(0, 10))}</p></div><div class="sim-saved-actions"><button type="button" class="btn-ghost sim-load-plan" data-id="${simEscape(plan.id)}">Mở</button><button type="button" class="btn-ghost sim-delete-plan" data-id="${simEscape(plan.id)}">Xóa</button></div></article>`).join("") : `<p class="muted-copy">Chưa có kế hoạch nào được lưu. Tạo một kế hoạch rồi bấm “Lưu kế hoạch”.</p>`;
      wrap.querySelectorAll(".sim-load-plan").forEach((button) => button.addEventListener("click", () => {
        const plan = simSavedPlans.find((p) => p.id === button.dataset.id); if (!plan) return;
        simCurrentPlanId = plan.id;
        simCurrentPlanName = plan.name || "Kế hoạch của tôi";
        simSemesters = (plan.semesters || []).map((s) => ({ label: s.label || "Học kỳ", course_ids: s.course_ids || [] }));
        if (!simSemesters.length) simSemesters = [{ label: "Học kỳ dự kiến 1", course_ids: [] }];
        renderSimSemesters(); validateSimPlan().catch((e) => showSimError(e.message));
      }));
      wrap.querySelectorAll(".sim-delete-plan").forEach((button) => button.addEventListener("click", async () => {
        // Xác nhận ngay trên nút (bấm lần hai để xóa), không dùng hộp thoại của trình duyệt.
        if (button.dataset.armed !== "1") {
          button.dataset.armed = "1";
          const label = button.textContent;
          button.textContent = "Bấm lại để xóa";
          setTimeout(() => { button.dataset.armed = ""; button.textContent = label; }, 3000);
          return;
        }
        try { const res = await api(`/api/study-plans/${encodeURIComponent(button.dataset.id)}`, { method: "DELETE" }); if (res.status >= 400 || !res.body.success) throw new Error(res.body.error || "Không xóa được kế hoạch."); await loadSimSavedPlans(); }
        catch (e) { showSimError(e.message); }
      }));
    } catch (error) { wrap.innerHTML = `<p class="sim-message sim-error">${simEscape(error.message)}</p>`; }
  }

  function showSimError(message) {
    const target = document.getElementById("simResults");
    if (target) target.innerHTML = `<div class="sim-message sim-error">${simEscape(message)}</div>`;
  }


  // ---------- Kiểm tra điều kiện tốt nghiệp (Điều 26 Quy chế) ----------
  const GRAD_VERDICT = {
    du_dieu_kien: { title: "Đủ điều kiện tốt nghiệp", cls: "is-pass" },
    cho_xac_nhan: { title: "Chưa thể kết luận", cls: "is-pending" },
    chua_du: { title: "Chưa đủ điều kiện tốt nghiệp", cls: "is-fail" },
  };
  const GRAD_SHORT = {
    tin_chi: "Đủ tín chỉ", hoc_lai: "Không còn môn học lại", gpa: "GPA từ 2,00",
    gdqp: "GDQP-AN", gdtc: "Giáo dục thể chất", ngoai_ngu: "Ngoại ngữ", tin_hoc: "Tin học",
    nghia_vu_tai_chinh: "Nghĩa vụ tài chính", khong_ky_luat_dinh_chi: "Không bị kỷ luật/đình chỉ",
  };
  let gradDeclared = {};
  let gradBlocksOpen = false;
  let gradOpenBlock = null;
  const GRAD_COURSE_STATUS = {
    CAN_HOC_LAI: ["Cần học lại", "is-bad"], DANG_KY_KY_NAY: ["Đang học", "is-ok"],
    CO_THE_DANG_KY: ["Có thể đăng ký", ""], BI_KHOA: ["Bị khóa", "is-warn"],
  };

  function gradBlockLabel(b) {
    const ma = String(b.ma || "").toUpperCase();
    let label;
    if (ma === "D1" || ma.startsWith("D1.")) {
      label = "Chuyên ngành bắt buộc";
    } else if (ma === "D2" || ma.startsWith("D2.")) {
      label = "Chuyên ngành tự chọn";
    } else {
      const name = String(b.ten || "").replace(/^Khối kiến thức\s*/i, "").trim();
      label = name ? name.charAt(0).toUpperCase() + name.slice(1) : "Khối kiến thức";
      // Fallback khi backend đánh dấu tự chọn/bắt buộc nhưng tên vẫn chung
      if (/chuyên ngành/i.test(label)) {
        label = b.tu_chon ? "Chuyên ngành tự chọn" : "Chuyên ngành bắt buộc";
      }
    }
    return label;
  }

  function gradSeg(item) {
    const cur = gradDeclared[item.key];
    const btn = (val, text, on, cls = "") =>
      `<button type="button" class="${on ? "on" : ""} ${cls}" data-grad-set="${escapeHtml(item.key)}" data-grad-val="${val}">${text}</button>`;
    return `<div class="gx-seg">${btn("", "Chưa rõ", cur === undefined)}${btn("true", "Đã đạt", cur === true)}${btn("false", "Chưa đạt", cur === false, "is-no")}</div>`;
  }

  function gradTodoRow(item, index) {
    const num = `<span class="gx-num">${index + 1}</span>`;
    const ex = item.extra || {};
    if (item.key === "tin_chi" && ex.total != null) {
      const blocks = (ex.blocks || []).slice().sort((x, y) => y.con - x.con);
      const chipHtml = (b) => {
        const open = gradOpenBlock === b.ma;
        return `<button type="button" class="gx-chip gx-chip-btn ${open ? "is-open" : ""}" data-grad-block="${escapeHtml(b.ma)}" aria-expanded="${open}">${escapeHtml(gradBlockLabel(b))}<span class="gx-chev-sm" aria-hidden="true">⌄</span></button>`;
      };
      const panelHtml = (b) => {
        const list = (b.courses || []).map((c) => {
          const [text, cls] = GRAD_COURSE_STATUS[c.trang_thai] || ["", ""];
          return `<div class="gx-course"><span>${escapeHtml(c.ten_hp)}</span><span class="gx-muted">${formatPlannerCredits(c.so_tin)} TC</span>${text ? `<span class="gx-chip ${cls}">${text}</span>` : ""}</div>`;
        }).join("");
        const hint = b.tu_chon ? `<p class="gx-note">Khối tự chọn: chọn các môn bất kỳ cho đủ số tín chỉ còn thiếu.</p>` : "";
        const missing = `<p class="gx-note"><b>Còn thiếu ${formatPlannerCredits(b.con)} tín chỉ</b></p>`;
        return `<div class="gx-courses"><p class="gx-kicker">${escapeHtml(gradBlockLabel(b))}</p>${missing}${hint}${list || '<p class="gx-note">Không có môn nào trong dữ liệu.</p>'}</div>`;
      };
      const openBlock = blocks.find((b) => b.ma === gradOpenBlock);
      return `<div class="gx-todo"><div class="gx-row">${num}
          <div class="gx-main"><strong>Còn thiếu tín chỉ</strong><span>${item.status === "pending" ? "Sẽ đủ nếu môn đang học đều đạt" : "Sau khi tính môn đang học"}</span></div>
          <span class="gx-big">${Number(ex.total)} TC</span></div>
          <div class="gx-row" style="margin-left:36px"><button type="button" class="gx-link" data-grad-blocks>${gradBlocksOpen ? "Ẩn các khối" : "Xem theo khối"}</button>${gradBlocksOpen ? '<span class="gx-muted">Bấm vào khối để xem môn</span>' : ""}</div>
          ${gradBlocksOpen ? `<div class="gx-blocks">${blocks.map(chipHtml).join("")}</div>${openBlock ? panelHtml(openBlock) : ""}` : ""}</div>`;
    }
    if (item.key === "hoc_lai" && Array.isArray(ex.courses)) {
      const names = ex.courses.map((c) => c.ten_hp);
      const shown = names.slice(0, 3).join(", ") + (names.length > 3 ? ` và ${names.length - 3} môn khác` : "");
      return `<div class="gx-todo"><div class="gx-row">${num}
          <div class="gx-main"><strong>Học lại ${names.length} học phần bắt buộc</strong><span>${escapeHtml(shown)}</span></div>
          <button type="button" class="gx-link" data-go-courses-grad="CAN_HOC_LAI">Xem môn</button></div></div>`;
    }
    const sub = item.source === "self" ? "Bạn tự xác nhận" : item.detail || "";
    return `<div class="gx-todo"><div class="gx-row">${num}
        <div class="gx-main"><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(sub)}</span></div>
        ${item.source === "self" ? gradSeg(item) : ""}</div></div>`;
  }

  function renderGraduation(result, selfDeclared) {
    gradDeclared = { ...(selfDeclared || {}) };
    const v = GRAD_VERDICT[result.verdict] || GRAD_VERDICT.cho_xac_nhan;
    const checks = (result.checks || []).filter((x) => x.required !== false);
    const todo = checks.filter((x) => x.status !== "pass");
    const done = checks.filter((x) => x.status === "pass");
    const total = checks.length || 1;
    const sub = todo.length ? `Còn ${todo.length} việc cần làm` : "Bạn đã đáp ứng các điều kiện kiểm tra được";

    $("gradVerdict").className = `gx-verdict ${v.cls}`;
    $("gradVerdict").innerHTML = `<h2>${escapeHtml(v.title)}</h2><p>${escapeHtml(sub)}</p>
      <div class="gx-bar-track"><i style="width:${Math.round((done.length / total) * 100)}%"></i></div>
      <div class="gx-bar-label">${done.length} trên ${checks.length} điều kiện đã đạt</div>`;

    $("gradTodo").innerHTML = todo.length
      ? todo.map(gradTodoRow).join("")
      : '<p class="gx-empty">Không còn việc nào cần làm.</p>';

    $("gradDoneCard").classList.toggle("hidden", !done.length);
    $("gradDoneCount").textContent = `${done.length} điều kiện`;
    $("gradDoneList").innerHTML = done.map((x) => {
      const text = escapeHtml(GRAD_SHORT[x.key] || x.label);
      return x.source === "self"
        ? `<button type="button" class="gx-chip is-ok" title="Bấm để bỏ xác nhận" data-grad-set="${escapeHtml(x.key)}" data-grad-val="">${text}</button>`
        : `<span class="gx-chip is-ok">${text}</span>`;
    }).join("");

    const r = result.rank || {};
    const gpa = r.gpa == null ? "—" : Number(r.gpa).toFixed(2);
    const retake = r.retake_over_limit == null ? "" : ` · Học lại ${escapeHtml(r.retake_credits ?? "—")}/${escapeHtml(r.retake_threshold_credits ?? "—")} TC${r.retake_over_limit ? " (đã vượt 5%)" : ""}`;
    $("gradFooter").innerHTML = `Hạng dự kiến: <b>${escapeHtml(r.final_rank || "—")}</b> · GPA ${escapeHtml(gpa)}${retake}`;
    $("gradDisclaimer").textContent = "Ước tính từ dữ liệu QLĐT, không phải xét duyệt chính thức.";
  }

  async function loadGraduation(extraOptions) {
    const target = $("gradVerdict");
    if (!target) return;
    try {
      const { status, body } = await api("/api/graduation-check", extraOptions || {});
      if (status >= 400 || !body.success) throw new Error(body.error || "Không kiểm tra được điều kiện tốt nghiệp.");
      renderGraduation(body.data, body.self_declared);
      return true;
    } catch (e) {
      target.className = "gx-verdict";
      target.innerHTML = `<div class="sim-message sim-error">${escapeHtml(e.message)}</div>`;
      return false;
    }
  }

  function initGraduation() {
    $("gradRefresh")?.addEventListener("click", () => loadGraduation());
    $("gradDoneToggle")?.addEventListener("click", () => {
      const list = $("gradDoneList");
      const open = list.classList.toggle("hidden") === false;
      $("gradDoneToggle").setAttribute("aria-expanded", String(open));
    });
    $("graduationSection")?.addEventListener("click", async (event) => {
      const set = event.target.closest("[data-grad-set]");
      if (set) {
        const next = { ...gradDeclared };
        const val = set.dataset.gradVal;
        if (val === "true") next[set.dataset.gradSet] = true;
        else if (val === "false") next[set.dataset.gradSet] = false;
        else delete next[set.dataset.gradSet];
        await loadGraduation({ method: "POST", body: JSON.stringify({ self_declared: next }) });
        return;
      }
      const blk = event.target.closest("[data-grad-block]");
      if (blk) {
        const code = blk.dataset.gradBlock;
        gradOpenBlock = gradOpenBlock === code ? null : code;
        await loadGraduation();
        return;
      }
      if (event.target.closest("[data-grad-blocks]")) {
        gradBlocksOpen = !gradBlocksOpen;
        if (!gradBlocksOpen) gradOpenBlock = null;
        await loadGraduation();
        return;
      }
      const go = event.target.closest("[data-go-courses-grad]");
      if (go) {
        setCourseFilter(go.dataset.goCoursesGrad);
        setActiveSection("coursesSection", true);
      }
    });
  }

  function initSimulator() {
    renderSimSemesters();
    document.getElementById("simNewPlan")?.addEventListener("click", () => {
      simCurrentPlanId = null;
      simCurrentPlanName = "Kế hoạch của tôi";
      simSemesters = [{ label: "Học kỳ dự kiến 1", course_ids: [] }];
      renderSimSemesters();
      document.getElementById("simResults").innerHTML = "";
    });
    document.getElementById("simAddSemester")?.addEventListener("click", () => {
      if (simSemesters.length >= 18) return showSimError("Một kế hoạch tối đa 18 học kỳ.");
      simSemesters.push({ label: `Học kỳ dự kiến ${simSemesters.length + 1}`, course_ids: [] }); renderSimSemesters();
    });
    document.getElementById("simSave")?.addEventListener("click", async () => {
      try {
        const pool = simCoursePool();
        const creditsByCode = new Map(pool.map((course) => [String(course.ma_hp), Number(course.so_tin) || 0]));
        const invalidTerms = simSemesters.map((semester, index) => ({ index: index + 1, credits: [...new Set(semester.course_ids || [])].reduce((sum, code) => sum + (creditsByCode.get(String(code)) || 0), 0) })).filter((term) => term.credits > 16);
        if (!pool.length) throw new Error("Chưa có dữ liệu môn học. Hãy cập nhật dữ liệu QLĐT trước.");
        if (invalidTerms.length) throw new Error(`Học kỳ ${invalidTerms.map((term) => `${term.index} (${term.credits} TC)`).join(", ")} vượt giới hạn 16 tín chỉ.`);
        const validation = await validateSimPlan();
        const endpoint = simCurrentPlanId ? `/api/study-plans/${encodeURIComponent(simCurrentPlanId)}` : "/api/study-plans";
        const method = simCurrentPlanId ? "PUT" : "POST";
        const { status, body } = await api(endpoint, { method, body: JSON.stringify(currentSimPlan()) });
        if (status >= 400 || !body.success) throw new Error(body.error || "Không lưu được kế hoạch.");
        simCurrentPlanId = body.data.id;
        await loadSimSavedPlans();
        const target = document.getElementById("simResults");
        if (target) target.insertAdjacentHTML("afterbegin", `<div class="sim-message sim-success">Đã lưu kế hoạch “${simEscape(body.data.name)}”.</div>`);
      } catch (e) { showSimError(e.message); }
    });
    document.getElementById("simReload")?.addEventListener("click", loadSimSavedPlans);
    document.getElementById("simCompare")?.addEventListener("click", async () => {
      const ids = Array.from(document.querySelectorAll(".sim-compare-check:checked")).map((input) => input.value);
      const target = document.getElementById("simCompareResults");
      if (ids.length < 2) { target.innerHTML = `<div class="sim-message sim-warning">Chọn ít nhất hai kế hoạch để so sánh.</div>`; return; }
      try {
        const comparisons = await Promise.all(ids.map(async (id) => {
          const plan = simSavedPlans.find((p) => p.id === id);
          const response = await api("/api/study-plans/simulate", { method: "POST", body: JSON.stringify(plan) });
          if (response.status >= 400 || !response.body.success) throw new Error(response.body.error || "Không thể so sánh kế hoạch.");
          return { plan, result: response.body.data };
        }));
        target.innerHTML = `<div class="table-scroll"><table class="courses-table"><thead><tr><th>Phương án</th><th>Học kỳ</th><th>Môn đã xếp</th><th>Tín chỉ</th><th>Lỗi điều kiện</th><th>Môn bắt buộc chưa xếp*</th></tr></thead><tbody>${comparisons.map(({plan, result}) => `<tr><td>${simEscape(plan.name)}</td><td>${result.summary.semester_count}</td><td>${result.summary.planned_courses}</td><td>${result.summary.planned_credits}</td><td>${result.errors.length}</td><td>${result.summary.remaining_mandatory_courses_in_source}</td></tr>`).join("")}</tbody></table></div><p class="sim-result-note">*Số môn còn lại được ước tính từ dữ liệu nguồn; không phải xác nhận chính thức về tốt nghiệp.</p>`;
      } catch (error) { target.innerHTML = `<div class="sim-message sim-error">${simEscape(error.message)}</div>`; }
    });
    loadSimSavedPlans();
  }

  // ===================== Lịch học (lich_hoc_phan.json) =====================
  const SCH_COLORS = ["#5B7DB1", "#2C6E5E", "#B4720F", "#A6402A", "#7A5C99", "#3E8A9A", "#8A7A2C", "#B35A7A"];
  const SCH_MIN_PERIODS = 12;
  // Giờ bắt đầu/kết thúc của từng tiết (theo bảng giờ của trường).
  const SCH_PERIOD_TIMES = {
    1: ["06:45", "07:35"], 2: ["07:40", "08:30"], 3: ["08:35", "09:25"],
    4: ["09:30", "10:20"], 5: ["10:25", "11:15"], 6: ["11:20", "12:10"],
    7: ["13:00", "13:50"], 8: ["13:55", "14:45"], 9: ["14:50", "15:40"],
    10: ["15:45", "16:35"], 11: ["16:40", "17:30"], 12: ["17:35", "18:25"],
    13: ["18:30", "19:20"], 14: ["19:20", "20:10"], 15: ["20:10", "21:00"],
  };
  // Tiết 4–6 -> "09:25–11:40"; tiết chưa có trong bảng thì quay về "Tiết x–y".
  function schTimeRange(from, to) {
    const a = SCH_PERIOD_TIMES[from];
    const b = SCH_PERIOD_TIMES[to];
    return a && b ? `${a[0]}–${b[1]}` : `Tiết ${from}–${to}`;
  }
  const schState = { data: null, sessions: [], weekStart: null };

  function schParseDate(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
    return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null;
  }
  function schIso(d) {
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  }
  function schAddDays(d, n) {
    const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    x.setDate(x.getDate() + n);
    return x;
  }
  function schMonday(d) {
    return schAddDays(d, -((d.getDay() + 6) % 7));
  }
  function schShort(d) {
    return `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}`;
  }
  function schFull(d) {
    return `${schShort(d)}/${d.getFullYear()}`;
  }
  function schDayName(thu) {
    return Number(thu) === 8 ? "CN" : `Thứ ${thu}`;
  }
  function schToday() {
    const n = new Date();
    return new Date(n.getFullYear(), n.getMonth(), n.getDate());
  }

  // Gộp mọi buổi của mọi lớp thành một danh sách phẳng; bỏ buổi thiếu ngày/tiết.
  function schFlatten(data) {
    const out = [];
    (data.lop || []).forEach((cls, classIdx) => {
      (cls.buoi || []).forEach((b) => {
        const date = schParseDate(b.ngay_hoc);
        const from = Number(b.tiet_bat_dau);
        const to = Number(b.tiet_ket_thuc);
        const thu = Number(b.thu);
        if (!date || !Number.isFinite(from) || !Number.isFinite(to) || from < 1 || to < from) return;
        out.push({ ...b, date, from, to, thu: thu >= 2 && thu <= 8 ? thu : ((date.getDay() + 6) % 7) + 2, classIdx, ten_hp: cls.ten_hp, lop: cls.lop });
      });
    });
    return out;
  }

  // Tuần mở mặc định: tuần hiện tại nếu có buổi học, nếu không thì tuần có buổi gần hôm nay nhất.
  function schPickWeek(sessions) {
    const today = schToday();
    const thisWeek = schMonday(today);
    if (!sessions.length) return thisWeek;
    const weeks = [...new Set(sessions.map((s) => schIso(schMonday(s.date))))];
    if (weeks.includes(schIso(thisWeek))) return thisWeek;
    let best = null;
    let bestDiff = Infinity;
    weeks.forEach((w) => {
      const diff = Math.abs(schParseDate(w) - thisWeek);
      if (diff < bestDiff) { bestDiff = diff; best = w; }
    });
    return schParseDate(best);
  }

  // Chia làn khi hai buổi trong cùng ngày trùng tiết, để cả hai cùng hiện.
  function schAssignLanes(daySessions) {
    const sorted = [...daySessions].sort((a, b) => a.from - b.from || a.to - b.to);
    const laneEnds = [];
    let conflict = false;
    sorted.forEach((s) => {
      let lane = laneEnds.findIndex((end) => end < s.from);
      if (lane === -1) { lane = laneEnds.length; laneEnds.push(s.to); } else { laneEnds[lane] = s.to; }
      s.lane = lane;
      if (lane > 0) conflict = true;
    });
    sorted.forEach((s) => { s.lanes = laneEnds.length; });
    return conflict;
  }

  function schRenderGrid() {
    const weekStart = schState.weekStart;
    const days = Array.from({ length: 7 }, (_, i) => schAddDays(weekStart, i));
    const weekEnd = days[6];
    const todayIso = schIso(schToday());
    const inWeek = schState.sessions.filter((s) => s.date >= weekStart && s.date <= weekEnd);
    // Chỉ hiện những tiết có buổi học trong tuần này; tiết trống thì ẩn đi.
    const usedPeriods = new Set();
    inWeek.forEach((s) => { for (let p = s.from; p <= s.to; p++) usedPeriods.add(p); });
    const visible = [...usedPeriods].sort((a, b) => a - b);
    const rowOf = {};
    visible.forEach((p, i) => { rowOf[p] = i + 2; });

    let conflicts = 0;
    for (let thu = 2; thu <= 8; thu++) {
      if (schAssignLanes(inWeek.filter((s) => s.thu === thu))) conflicts++;
    }

    let html = `<div class="sch-corner"></div>`;
    days.forEach((d, i) => {
      const isToday = schIso(d) === todayIso;
      html += `<div class="sch-dayhead${isToday ? " is-today" : ""}" style="grid-column:${i + 2};grid-row:1"><b>${schDayName(i + 2)}</b><span>${schShort(d)}</span></div>`;
    });
    visible.forEach((p) => {
      html += `<div class="sch-period" title="Tiết ${p}" style="grid-column:1;grid-row:${rowOf[p]}">${SCH_PERIOD_TIMES[p] ? SCH_PERIOD_TIMES[p][0] : "Tiết " + p}</div>`;
      for (let c = 0; c < 7; c++) {
        const isToday = schIso(days[c]) === todayIso;
        html += `<div class="sch-cell${isToday ? " is-today" : ""}" style="grid-column:${c + 2};grid-row:${rowOf[p]}"></div>`;
      }
    });
    if (!visible.length) html += `<div class="sch-empty-week" style="grid-column:1 / -1;grid-row:2">Tuần này không có buổi học.</div>`;
    inWeek.forEach((s) => {
      const color = SCH_COLORS[s.classIdx % SCH_COLORS.length];
      const width = 100 / s.lanes;
      const room = s.phong ? `<em class="sch-room">${escapeHtml(s.phong)}</em>` : "";
      const att = schAttendanceByDate(schState.data.lop[s.classIdx])[schIso(s.date)];
      const mark = att && att.ket_qua !== "other"
        ? `<i class="sch-mark sch-mark-${att.ket_qua}" title="${escapeHtml(att.trang_thai || "")}">${att.ket_qua === "present" ? "✓ Có mặt" : att.ket_qua === "excused" ? "Vắng có phép" : "✗ Vắng"}</i>` : "";
      const tip = `${s.ten_hp} (${s.lop})\n${schDayName(s.thu)}, ${schFull(s.date)} · ${schTimeRange(s.from, s.to)} (tiết ${s.from}–${s.to})${s.phong ? " · " + s.phong : ""}${s.giang_vien ? "\n" + s.giang_vien : ""}`;
      html += `<div class="sch-block" role="button" data-sch-class="${s.classIdx}" tabindex="0" title="${escapeHtml(tip)}" style="grid-column:${s.thu};grid-row:${rowOf[s.from]} / ${rowOf[s.to] + 1};--c:${color};width:${width}%;margin-left:${width * s.lane}%"><strong>${escapeHtml(s.ten_hp)}</strong><span>${schTimeRange(s.from, s.to)}</span>${room}${mark}</div>`;
    });

    const grid = $("schGrid");
    grid.style.gridTemplateRows = `auto repeat(${Math.max(visible.length, 1)}, var(--sch-row))`;
    grid.innerHTML = html;

    $("schWeekLabel").textContent = `${schFull(weekStart)} – ${schFull(weekEnd)}`;
    const meta = inWeek.length ? `${inWeek.length} buổi trong tuần` : "Tuần này không có buổi học";
    $("schWeekMeta").innerHTML = escapeHtml(meta) + (conflicts ? ` · <span class="sch-warn">Có buổi trùng tiết</span>` : "");
  }

  // Điểm danh theo ngày học (YYYY-MM-DD) của một lớp.
  function schAttendanceByDate(cls) {
    const map = {};
    ((cls && cls.diem_danh) || []).forEach((a) => { if (a.ngay_hoc) map[a.ngay_hoc] = a; });
    return map;
  }

  const SCH_ATT_LABEL = { present: "Có mặt", absent: "Vắng", excused: "Vắng có phép", other: "" };

  function schOpenAttendance(classIdx) {
    const cls = (schState.data && schState.data.lop || [])[classIdx];
    const dlg = $("schAttDialog");
    if (!cls || !dlg) return;
    const list = cls.diem_danh || [];
    const count = (k) => list.filter((a) => a.ket_qua === k).length;
    let body;
    if (cls.diem_danh_loi) {
      body = `<p class="sch-note sch-note-warn">Không đọc được điểm danh của lớp này: ${escapeHtml(cls.diem_danh_loi)} Hãy cập nhật dữ liệu QLĐT lại.</p>`;
    } else if (!list.length) {
      body = `<p class="sch-note">Chưa có dữ liệu điểm danh. Nếu bạn vừa cập nhật code, hãy bấm cập nhật dữ liệu QLĐT để lấy điểm danh.</p>`;
    } else {
      const rows = list.map((a) => {
        const d = schParseDate(a.ngay_hoc);
        const when = d ? `${schDayName(((d.getDay() + 6) % 7) + 2)}, ${schFull(d)}` : escapeHtml(a.ngay_hoc || "—");
        const time = a.tiet_bat_dau ? schTimeRange(a.tiet_bat_dau, a.tiet_ket_thuc || a.tiet_bat_dau) : "—";
        return `<tr><td class="num">${escapeHtml(a.stt ?? "")}</td><td>${when}</td><td>${escapeHtml(time)}</td><td><span class="sch-att sch-att-${a.ket_qua}">${escapeHtml(SCH_ATT_LABEL[a.ket_qua] || a.trang_thai || "—")}</span></td></tr>`;
      }).join("");
      body = `<div class="sch-att-summary">
          <span class="sch-att sch-att-present">Có mặt ${count("present")}</span>
          <span class="sch-att sch-att-absent">Vắng ${count("absent")}</span>
          ${count("excused") ? `<span class="sch-att sch-att-excused">Vắng có phép ${count("excused")}</span>` : ""}
          <span class="sch-att-total">/ ${list.length} buổi đã điểm danh</span>
        </div>
        <div class="table-scroll"><table class="courses-table sch-table"><thead><tr><th>STT</th><th>Ngày học</th><th>Giờ học</th><th>Trạng thái</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }
    dlg.innerHTML = `<header><div><h4 id="schAttTitle">${escapeHtml(cls.ten_hp || "Học phần")}</h4><span>${escapeHtml(cls.lop || "")}</span></div><button type="button" class="sch-btn" data-sch-close aria-label="Đóng">×</button></header>${body}`;
    if (typeof dlg.showModal === "function") { if (!dlg.open) dlg.showModal(); } else { dlg.setAttribute("open", ""); }
  }

  function schRenderNext() {
    const el = $("schNextClass");
    const today = schToday();
    const upcoming = schState.sessions
      .filter((s) => s.date >= today)
      .sort((a, b) => a.date - b.date || a.from - b.from)[0];
    if (!upcoming) { el.classList.add("hidden"); return; }
    const isToday = schIso(upcoming.date) === schIso(today);
    const when = isToday ? "Hôm nay" : `${schDayName(upcoming.thu)}, ${schFull(upcoming.date)}`;
    el.classList.remove("hidden");
    el.innerHTML = `Buổi tiếp theo: <b>${escapeHtml(upcoming.ten_hp)}</b> · ${escapeHtml(when)} · ${schTimeRange(upcoming.from, upcoming.to)}${upcoming.phong ? " · " + escapeHtml(upcoming.phong) : ""} <button class="text-action" type="button" data-sch-jump="${schIso(upcoming.date)}">Xem tuần đó</button>`;
  }

  function schRenderLegend() {
    const data = schState.data;
    const chips = (data.lop || []).map((cls, i) =>
      `<span class="sch-chip"><i style="background:${SCH_COLORS[i % SCH_COLORS.length]}"></i>${escapeHtml(cls.ten_hp || cls.lop || "Lớp")}</span>`).join("");
    let updated = "";
    if (data.cap_nhat) {
      const d = new Date(data.cap_nhat);
      if (!Number.isNaN(d.getTime())) updated = `<span class="sch-updated">Cập nhật ${escapeHtml(d.toLocaleString("vi-VN"))}</span>`;
    }
    $("schLegend").innerHTML = chips + updated;
  }

  function schRenderClasses() {
    const today = schToday();
    const html = (schState.data.lop || []).map((cls, i) => {
      const color = SCH_COLORS[i % SCH_COLORS.length];
      const buoi = (cls.buoi || []).map((b) => ({ ...b, date: schParseDate(b.ngay_hoc) }));
      const done = buoi.filter((b) => b.date && b.date < today).length;
      const days = (cls.thu_trong_tuan || []).map(schDayName).join(", ");
      const range = cls.tu_ngay && cls.den_ngay ? `${schFull(schParseDate(cls.tu_ngay) || new Date(NaN))} – ${schFull(schParseDate(cls.den_ngay) || new Date(NaN))}` : "";
      const facts = [
        cls.loai ? escapeHtml(cls.loai) : "",
        cls.da_dang_ky != null && cls.tong_so != null ? `Sĩ số ${escapeHtml(cls.da_dang_ky)}/${escapeHtml(cls.tong_so)}` : "",
        days ? escapeHtml(days) : "",
        range && !range.includes("NaN") ? escapeHtml(range) : "",
      ].filter(Boolean).join(" · ");

      let body;
      if (cls.loi) {
        body = `<p class="sch-note sch-note-warn">Không đọc được lịch của lớp này: ${escapeHtml(cls.loi)} Hãy cập nhật dữ liệu QLĐT lại.</p>`;
      } else if (!buoi.length) {
        body = `<p class="sch-note">Trường chưa xếp lịch chi tiết cho lớp này.</p>`;
      } else {
        const rows = buoi.map((b) => {
          const past = b.date && b.date < today;
          return `<tr class="${past ? "is-past" : ""}"><td class="num">${escapeHtml(b.stt ?? "")}</td><td>${b.date ? `${schDayName(b.thu)}, ${schFull(b.date)}` : "—"}</td><td class="num" title="Tiết ${escapeHtml(b.tiet_bat_dau ?? "")}–${escapeHtml(b.tiet_ket_thuc ?? "")}">${escapeHtml(schTimeRange(Number(b.tiet_bat_dau), Number(b.tiet_ket_thuc)))}</td><td>${escapeHtml(b.phong || "—")}</td><td>${escapeHtml(b.giang_vien || "—")}</td></tr>`;
        }).join("");
        body = `<details class="sch-details"><summary>Xem tất cả ${buoi.length} buổi <span>(đã qua ${done}, còn ${buoi.length - done})</span></summary>
          <div class="table-scroll"><table class="courses-table sch-table"><thead><tr><th>STT</th><th>Ngày</th><th>Giờ học</th><th>Phòng</th><th>Giảng viên</th></tr></thead><tbody>${rows}</tbody></table></div></details>`;
      }
      return `<article class="sch-class" style="--c:${color}"><header><h4>${escapeHtml(cls.ten_hp || "Học phần chưa rõ tên")}</h4><span>${escapeHtml(cls.lop || "")}</span></header><p class="sch-facts">${facts}</p>${body}</article>`;
    }).join("");
    $("schClasses").innerHTML = html;
  }

  function schShowMessage(text, isError) {
    const el = $("schMessage");
    el.textContent = text;
    el.classList.toggle("sch-message-error", Boolean(isError));
    el.classList.remove("hidden");
    $("schBoard").classList.add("hidden");
  }

  async function loadSchedule() {
    if (!$("schBoard")) return;
    try {
      const { status, body } = await api("/api/class-schedule");
      if (status === 404) { schShowMessage(body.error || "Chưa có lịch học.", false); return; }
      if (status >= 400 || !body.success) throw new Error(body.error || "Không tải được lịch học.");
      const hadWeek = schState.weekStart !== null;
      schState.data = body.data;
      schState.sessions = schFlatten(body.data);
      if (!hadWeek) schState.weekStart = schPickWeek(schState.sessions);
      $("schMessage").classList.add("hidden");
      $("schBoard").classList.remove("hidden");
      schRenderLegend();
      schRenderNext();
      schRenderGrid();
    } catch (e) {
      schShowMessage(e.message, true);
    }
  }

  // navigator.clipboard chỉ có trên HTTPS/localhost; trang chạy bằng http://IP-LAN thì dùng execCommand.
  async function schCopyText(text) {
    try {
      if (navigator.clipboard?.writeText && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch (_) {}
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.cssText = "position:fixed;top:0;left:0;opacity:0";
      document.body.appendChild(ta);
      ta.select();
      ta.setSelectionRange(0, ta.value.length);
      const ok = document.execCommand("copy");
      ta.remove();
      return ok;
    } catch (_) {
      return false;
    }
  }

  function schShowSubscribeDialog(url, errorMsg) {
    let dlg = $("schSubDialog");
    if (!dlg) {
      dlg = document.createElement("dialog");
      dlg.id = "schSubDialog";
      dlg.className = "sch-att-dialog";
      document.body.appendChild(dlg);
      dlg.addEventListener("click", async (event) => {
        if (event.target === dlg || event.target.closest("[data-sub-close]")) { dlg.close(); return; }
        const input = $("schSubUrl");
        if (event.target === input) { input.select(); return; }
        if (event.target.closest("#schSubCopy")) {
          const ok = await schCopyText(input.value);
          if (!ok) input.select();
          $("schSubMsg").textContent = ok ? "Đã sao chép link." : "Không tự sao chép được. Link đã được bôi đen, hãy nhấn Ctrl+C.";
        }
      });
    }
    const privateHost = /^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(location.hostname);
    dlg.innerHTML = `<header><div><h4>Thêm vào lịch</h4><span>Lịch tự cập nhật khi dữ liệu thay đổi</span></div><button type="button" class="sch-btn" data-sub-close aria-label="Đóng">×</button></header>
      ${errorMsg ? `<p class="sch-note sch-note-warn">${escapeHtml(errorMsg)}</p>` : `<ol class="sch-sub-steps">
        <li><b>iPhone:</b> Cài đặt → Lịch → Tài khoản → Thêm tài khoản → Khác → Thêm lịch đăng ký, rồi dán link bên dưới.</li>
        <li><b>Mac, Outlook:</b> chọn đăng ký lịch từ web (Calendar → File → New Calendar Subscription), rồi dán link.</li>
        <li><b>Google Calendar:</b> Settings → Add calendar → From URL, rồi dán link.</li>
      </ol>
      <div class="sch-sub-link"><input id="schSubUrl" type="text" readonly value="${escapeHtml(url)}" aria-label="Link lịch"><button type="button" id="schSubCopy" class="sch-btn sch-btn-text">Sao chép</button></div>
      <p id="schSubMsg" class="sch-note" role="status"></p>`}
      ${!errorMsg && privateHost ? '<p class="sch-note sch-note-warn">Link này là địa chỉ mạng nội bộ nên chỉ dùng được khi thiết bị cùng mạng Wi-Fi. Google Calendar sẽ không đọc được.</p>' : ""}`;
    if (typeof dlg.showModal === "function") { if (!dlg.open) dlg.showModal(); } else { dlg.setAttribute("open", ""); }
  }

  function initSchedule() {
    const shift = (days) => {
      if (!schState.weekStart) return;
      schState.weekStart = schAddDays(schState.weekStart, days);
      schRenderGrid();
    };
    $("schPrev")?.addEventListener("click", () => shift(-7));
    $("schNext")?.addEventListener("click", () => shift(7));
    $("schToday")?.addEventListener("click", () => {
      schState.weekStart = schMonday(schToday());
      if (schState.data) schRenderGrid();
    });
    $("schRefresh")?.addEventListener("click", () => loadSchedule());
    $("schSubscribe")?.addEventListener("click", async () => {
      const btn = $("schSubscribe");
      const prev = btn?.textContent;
      try {
        if (btn) { btn.disabled = true; btn.textContent = "Đang tạo link…"; }
        const { status, body } = await api("/api/class-schedule/subscribe-url");
        if (status !== 200 || !body.success) {
          schShowSubscribeDialog("", body.error || "Không tạo được link lịch.");
          return;
        }
        // Không tự mở webcal:// nữa (trình duyệt hỏi "Mở Pick an app?"); người dùng dán link vào app Lịch.
        schShowSubscribeDialog(body.https_url);
      } catch (e) {
        schShowSubscribeDialog("", e.message || "Lỗi tạo link lịch.");
      } finally {
        if (btn) { btn.disabled = false; if (btn.textContent === "Đang tạo link…") btn.textContent = prev || "Thêm vào lịch"; }
      }
    });
    $("schGrid")?.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const blk = event.target.closest("[data-sch-class]");
      if (blk) { event.preventDefault(); schOpenAttendance(Number(blk.dataset.schClass)); }
    });
    $("scheduleSection")?.addEventListener("click", (event) => {
      if (event.target.closest("[data-sch-close]") || event.target === $("schAttDialog")) { $("schAttDialog")?.close?.(); return; }
      const blk = event.target.closest("[data-sch-class]");
      if (blk) { schOpenAttendance(Number(blk.dataset.schClass)); return; }
      const jump = event.target.closest("[data-sch-jump]");
      if (!jump) return;
      const d = schParseDate(jump.dataset.schJump);
      if (d) { schState.weekStart = schMonday(d); schRenderGrid(); }
    });
  }

  function setActiveSection(sectionId, resetScroll = false) {
    if (!SECTION_TITLES[sectionId]) return;
    state.activeSection = sectionId;

    document.querySelectorAll(".sidebar-link[data-nav-target]").forEach((link) => {
      link.classList.toggle("active", link.dataset.navTarget === sectionId);
    });
    document.querySelectorAll(".view-section").forEach((section) => {
      section.classList.toggle("active-view", section.id === sectionId);
    });

    if (sectionId === "graduationSection") loadGraduation();
    if (sectionId === "scheduleSection") loadSchedule();
    const [title, subtitle] = SECTION_TITLES[sectionId];
    document.getElementById("topbarTitle").textContent = title;
    document.getElementById("topbarSubtitle").textContent = subtitle;
    if (resetScroll) window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function closeMobileSidebar() {
    document.body.classList.remove("sidebar-open");
  }

  function initSidebar() {
    $("refreshAssessment")?.addEventListener("click", () => { if (state.roadmap) renderAssessment(state.roadmap); });
    const collapseBtn = document.getElementById("sidebarCollapse");
    const mobileMenuBtn = document.getElementById("mobileMenuBtn");
    const backdrop = document.getElementById("sidebarBackdrop");

    try {
      if (localStorage.getItem("paSidebarCollapsed") === "1" && window.innerWidth > 900) {
        document.body.classList.add("sidebar-collapsed");
      }
    } catch (_) {}

    collapseBtn?.addEventListener("click", () => {
      document.body.classList.toggle("sidebar-collapsed");
      try {
        localStorage.setItem("paSidebarCollapsed", document.body.classList.contains("sidebar-collapsed") ? "1" : "0");
      } catch (_) {}
    });
    mobileMenuBtn?.addEventListener("click", () => document.body.classList.add("sidebar-open"));
    backdrop?.addEventListener("click", closeMobileSidebar);

    document.querySelectorAll(".sidebar-link[data-nav-target]").forEach((link) => {
      link.addEventListener("click", (event) => {
        event.preventDefault();
        if (!state.roadmap) return;
        setActiveSection(link.dataset.navTarget, true);
        closeMobileSidebar();
      });
    });

    document.getElementById("sidebarBrand")?.addEventListener("click", (event) => {
      event.preventDefault();
      if (!state.roadmap) return;
      setActiveSection("overviewSection", true);
      closeMobileSidebar();
    });

    window.addEventListener("resize", () => { if (window.innerWidth > 900) closeMobileSidebar(); });
  }

  document.querySelectorAll("[data-go-section]").forEach((btn) => {
    btn.addEventListener("click", () => setActiveSection(btn.dataset.goSection, true));
  });
  document.querySelectorAll("[data-go-courses]").forEach((btn) => {
    btn.addEventListener("click", () => {
      setCourseFilter(btn.dataset.goCourses);
      setActiveSection("coursesSection", true);
    });
  });

  document.getElementById("coursesTableWrap")?.addEventListener("click", (event) => {
    const blk = event.target.closest("[data-cur-block]");
    if (blk) { state.curBlock = blk.dataset.curBlock; renderCourses(); return; }
    const go = event.target.closest("[data-go-section]");
    if (go) setActiveSection(go.dataset.goSection, true);
  });

  const crawlModal = document.getElementById("crawlModal");
  const crawlModalCard = document.getElementById("crawlModalCard");
  const remoteBrowserWrap = document.getElementById("remoteBrowserWrap");
  const remoteBrowserFrame = document.getElementById("remoteBrowserFrame");
  const crawlInstruction = document.getElementById("crawlInstruction");
  const crawlModalSubtitle = document.getElementById("crawlModalSubtitle");

  function setRemoteBrowser(url = "") {
    const enabled = Boolean(url);
    remoteBrowserWrap?.classList.toggle("hidden", !enabled);
    crawlModalCard?.classList.toggle("remote-active", enabled);
    if (remoteBrowserFrame) remoteBrowserFrame.src = enabled ? url : "about:blank";
    if (crawlInstruction) {
      crawlInstruction.textContent = enabled
        ? "Đăng nhập QLDT ngay trong màn hình Chrome bên dưới. Sau khi đăng nhập thành công, hệ thống sẽ tự crawl và đóng cửa sổ này."
        : "Chrome riêng của Advisor sẽ mở. Đăng nhập QLĐT lần đầu; các lần sau Advisor sẽ dùng lại phiên đăng nhập nếu còn hiệu lực.";
    }
    if (crawlModalSubtitle) {
      crawlModalSubtitle.textContent = enabled
        ? "Phiên Chrome riêng đang chạy trên VPS"
        : "Chrome riêng của Advisor";
    }
  }

  const credForm = document.getElementById("crawlCredForm");
  const credMssv = document.getElementById("credMssv");
  const credPassword = document.getElementById("credPassword");
  const credError = document.getElementById("credError");

  function showCredError(msg) {
    if (!credError) return;
    credError.textContent = msg || "";
    credError.classList.toggle("hidden", !msg);
  }

  // Bước 1: mở cửa sổ và hiện form nhập mã sinh viên + mật khẩu.
  function openCrawlModalAndStart() {
    closeMobileSidebar();
    document.getElementById("crawlModeChoice").classList.add("hidden");
    document.getElementById("crawlProgress").classList.add("hidden");
    document.getElementById("crawlLog").textContent = "";
    document.getElementById("progressFill").style.width = "0%";
    document.getElementById("progressPercent").textContent = "0%";
    document.getElementById("btnCloseCrawlDone").classList.add("hidden");
    setRemoteBrowser("");
    showCredError("");
    if (credPassword) credPassword.value = "";
    credForm?.classList.remove("hidden");
    if (crawlModalSubtitle) crawlModalSubtitle.textContent = "Nhập tài khoản để đăng nhập tự động";
    crawlModal.classList.remove("hidden");
    setTimeout(() => (credMssv?.value ? credPassword : credMssv)?.focus(), 50);
  }

  // Bước 2: bắt đầu crawl, có hoặc không kèm tài khoản.
  async function beginCrawl(creds) {
    credForm?.classList.add("hidden");
    document.getElementById("crawlProgress").classList.remove("hidden");

    // Local: dùng Chrome riêng của Advisor qua CDP, tự khởi chạy nếu cần.
    // VPS: giữ luồng remote desktop để người dùng thấy/hoàn tất xác thực nếu cần.
    const mode = state.remoteBrowserEnabled ? "remote" : "cdp";
    if (crawlModalSubtitle) {
      crawlModalSubtitle.textContent = state.remoteBrowserEnabled
        ? "Đang tạo phiên Chrome riêng trên VPS..."
        : "Đang mở/kết nối Chrome riêng của Advisor...";
    }
    await startCrawl(mode, creds);
  }

  credForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const mssv = (credMssv?.value || "").trim();
    const password = credPassword?.value || "";
    if (!mssv || !password) {
      showCredError("Hãy nhập đủ mã sinh viên và mật khẩu.");
      return;
    }
    showCredError("");
    // Xóa mật khẩu khỏi ô nhập ngay sau khi lấy; không lưu ở bất kỳ đâu.
    if (credPassword) credPassword.value = "";
    await beginCrawl({ mssv, password });
  });
  document.getElementById("btnCredSkip")?.addEventListener("click", () => beginCrawl(null));

  document.getElementById("btnCrawl")?.addEventListener("click", openCrawlModalAndStart);
  document.getElementById("btnCrawlHero")?.addEventListener("click", openCrawlModalAndStart);
  document.getElementById("crawlModalClose")?.addEventListener("click", () => {
    if (credPassword) credPassword.value = "";
    crawlModal.classList.add("hidden");
    setRemoteBrowser("");
  });
  document.getElementById("btnCloseCrawlDone")?.addEventListener("click", async () => {
    crawlModal.classList.add("hidden");
    setRemoteBrowser("");
    state.activeSection = "overviewSection";
    await loadDashboard();
  });

  async function startCrawl(mode, creds) {
    try {
      const payload = { mode };
      if (creds && creds.mssv && creds.password) {
        payload.mssv = creds.mssv;
        payload.password = creds.password;
      }
      const { status, body } = await api("/api/crawl_auto", { method: "POST", body: JSON.stringify(payload) });
      if (!body.success) {
        // Nếu cùng browser đã có job chạy thì nối lại đúng job/Chrome đó.
        if (status === 409 && body.job_id) {
          appendLog({ status: "info", message: "Phiên crawl của bạn đang chạy — đang kết nối lại..." });
          if (body.remote_url) setRemoteBrowser(body.remote_url);
          streamCrawlLogs(body.job_id);
          return;
        }
        // Lỗi nhập liệu (400): quay lại form để sửa thay vì kết thúc.
        if (status === 400 && creds) {
          document.getElementById("crawlProgress").classList.add("hidden");
          credForm?.classList.remove("hidden");
          showCredError(body.error || "Thông tin đăng nhập không hợp lệ.");
          return;
        }
        appendLog({ status: "error", message: body.error || "Không bắt đầu được job crawl." });
        document.getElementById("btnCloseCrawlDone").classList.remove("hidden");
        return;
      }

      if (body.remote_url) setRemoteBrowser(body.remote_url);
      streamCrawlLogs(body.job_id);
    } catch (err) {
      appendLog({ status: "error", message: err.message });
      document.getElementById("btnCloseCrawlDone").classList.remove("hidden");
    }
  }

  function appendLog(entry) {
    const logEl = document.getElementById("crawlLog");
    const prefix = { error: "[LỖI] ", ok: "[OK] ", running: "", info: "" }[entry.status] || "";
    logEl.textContent += prefix + (entry.message || "") + "\n";
    logEl.scrollTop = logEl.scrollHeight;
    if (typeof entry.progress === "number") {
      document.getElementById("progressFill").style.width = entry.progress + "%";
      document.getElementById("progressPercent").textContent = entry.progress + "%";
    }
  }

  async function streamCrawlLogs(jobId) {
    const res = await fetch(`/api/crawl_stream/${jobId}`);
    if (!res.ok || !res.body) {
      appendLog({ status: "error", message: `Không mở được stream (status ${res.status}).` });
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let sepIndex;
      while ((sepIndex = buffer.indexOf("\n\n")) !== -1) {
        const chunk = buffer.slice(0, sepIndex);
        buffer = buffer.slice(sepIndex + 2);
        const line = chunk.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        let entry;
        try { entry = JSON.parse(line.slice(5).trim()); } catch (_) { continue; }
        if (entry.event === "end") {
          if (entry.status === "done") {
            // Crawl đã hoàn tất: nạp roadmap mới và chuyển thẳng vào dashboard.
            // Không bắt người dùng phải bấm nút Đóng thêm một lần nữa.
            document.getElementById("progressFill").style.width = "100%";
            document.getElementById("progressPercent").textContent = "100%";
            state.activeSection = "overviewSection";

            try {
              await loadDashboard();
              setRemoteBrowser("");
              crawlModal.classList.add("hidden");
              window.scrollTo({ top: 0, behavior: "smooth" });
            } catch (err) {
              appendLog({
                status: "error",
                message: `Crawl đã xong nhưng không tải lại được giao diện: ${err.message}`,
              });
              document.getElementById("btnCloseCrawlDone").classList.remove("hidden");
            }
          } else {
            // Nếu job lỗi thì giữ modal lại để người dùng đọc log.
            document.getElementById("btnCloseCrawlDone").classList.remove("hidden");
          }
          return;
        }
        appendLog(entry);
      }
    }
  }

  async function init() {
    loadPlannerPreferences();
    initSidebar();
    await loadRuntimeConfig();
    await loadDashboard();
    initSimulator();
    initGraduation();
    initSchedule();
  }

  init();
})();

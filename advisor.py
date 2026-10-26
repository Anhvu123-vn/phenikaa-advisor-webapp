"""Advisor — NGUỒN DUY NHẤT cho GPA, tín chỉ còn thiếu, học cải thiện, mô phỏng
và lộ trình tốt nghiệp.

Hàm thuần (không Flask/session): nhận dict roadmap + lựa chọn của người dùng,
trả về một bản tính hoàn chỉnh. Mọi thay đổi (mục tiêu, môn cải thiện, môn kỳ
tới, điểm dự kiến) đều đi qua ``analyze()`` nên GPA, tín chỉ và lộ trình luôn
được tính lại đồng bộ.

Khái niệm tín chỉ (tránh nhầm lẫn):
  * ``shortfall``      — tín chỉ còn THIẾU để tốt nghiệp, tính từ các khối trong
                         chương trình đào tạo (bắt buộc / tự chọn). KHÔNG phụ
                         thuộc danh sách môn được đề xuất.
  * ``planned_next``   — tín chỉ DỰ KIẾN học kỳ tới (môn mới người dùng chọn).
  * ``improve_load``   — tín chỉ học cải thiện kỳ tới: chiếm khối lượng học nhưng
                         KHÔNG cộng thêm tín chỉ tích lũy và KHÔNG tăng mẫu số GPA.

Quy tắc học cải thiện: điểm mới THAY điểm cũ của đúng môn đó (cùng số tín chỉ,
cùng mẫu số GPA). Điểm mới không cao hơn điểm cũ bị bỏ qua (giữ điểm cao hơn).
Nếu quy chế đào tạo thực tế của Phenikaa quy định khác (ví dụ chỉ tính lần học
cuối), chỉnh ``IMPROVE_*`` bên dưới.
"""

from planner import PACE_OPTIONS, build_academic_plan, _course_credits, _num, _parse_semester_order

GPA_TARGETS = {
    "kha": {"key": "kha", "label": "Khá", "gpa": 2.50},
    "gioi": {"key": "gioi", "label": "Giỏi", "gpa": 3.20},
    "xuat_sac": {"key": "xuat_sac", "label": "Xuất sắc", "gpa": 3.60},
}
CUSTOM_MIN, CUSTOM_MAX = 2.0, 4.0

# Thang điểm Phenikaa (P/NP không đi vào GPA).
GRADE_SCALE = [
    {"letter": "D",  "point": 1.0, "min_10": 4.0},
    {"letter": "D+", "point": 1.5, "min_10": 5.0},
    {"letter": "C",  "point": 2.0, "min_10": 5.5},
    {"letter": "C+", "point": 2.5, "min_10": 6.5},
    {"letter": "B",  "point": 3.0, "min_10": 7.0},
    {"letter": "B+", "point": 3.5, "min_10": 8.0},
    {"letter": "A",  "point": 3.7, "min_10": 8.5},
    {"letter": "A+", "point": 4.0, "min_10": 9.0},
]
GRADE_POINTS = {g["letter"]: g["point"] for g in GRADE_SCALE}

# Môn đã đạt có điểm <= ngưỡng này được liệt kê là "điểm thấp" để cải thiện.
IMPROVE_MAX_POINT = 3.0
# Điểm mới phải cao hơn điểm cũ mới được tính (xem docstring).
IMPROVE_REQUIRE_HIGHER = True

DEFAULT_PLAN_LETTER = "B"
EPS = 1e-9


# ---------------------------------------------------------------- helpers

def classify(gpa):
    if gpa is None:
        return None
    if gpa >= 3.6 - EPS:
        return "Xuất sắc"
    if gpa >= 3.2 - EPS:
        return "Giỏi"
    if gpa >= 2.5 - EPS:
        return "Khá"
    if gpa >= 2.0 - EPS:
        return "Trung bình"
    return "Dưới trung bình"


def resolve_target(key, custom=None):
    """Trả về dict mục tiêu hoặc None nếu không hợp lệ."""
    if key in GPA_TARGETS:
        return dict(GPA_TARGETS[key])
    if key == "custom":
        try:
            value = float(custom)
        except (TypeError, ValueError):
            return None
        if not (CUSTOM_MIN - EPS <= value <= CUSTOM_MAX + EPS):
            return None
        value = round(value, 2)
        return {"key": "custom", "label": f"Tùy chỉnh {value:.2f}", "gpa": value,
                "classification": classify(value)}
    return None


def letter_for_required(required):
    """Điểm chữ thấp nhất có điểm hệ 4 >= required."""
    if required is None:
        return None
    for g in GRADE_SCALE:
        if g["point"] + EPS >= required:
            return g["letter"]
    return None


def _is_f_block(code):
    return str(code or "").upper().startswith("F")


def _gpa_counted(course):
    return not course.get("khong_tinh_tin_chi", False) and _course_credits(course) > 0


def _course_point(course):
    return GRADE_POINTS.get(str(course.get("diem_chu") or "").strip())


def _block_needs(data):
    """{khối: tín còn thiếu} và thông tin khối, bỏ khối điều kiện F / bo_qua."""
    needs, info = {}, {}
    for code, g in (data.get("khoi_summary") or {}).items():
        if g.get("bo_qua") or _is_f_block(code):
            continue
        required = _num(g.get("tin_bat_buoc", g.get("tong_tin", 0)))
        accumulated = _num(g.get("da_tich_luy", 0))
        needs[code] = max(0.0, required - accumulated)
        info[code] = {
            "ma": code,
            "ten": g.get("ten_khoi") or code,
            "tu_chon": bool(g.get("la_tu_chon")),
            "required": required,
            "accumulated": accumulated,
        }
    return needs, info


def _apply_to_blocks(needs, courses):
    """Trừ tín chỉ của ``courses`` vào phần còn thiếu của từng khối (không vượt
    nhu cầu khối). Sửa ``needs`` tại chỗ; trả về tổng tín chỉ thực sự được tính."""
    counted = 0.0
    for c in courses:
        block = c.get("khoi_kt")
        if block not in needs or not _gpa_counted(c):
            continue
        take = min(_course_credits(c), needs[block])
        if take > EPS:
            needs[block] -= take
            counted += take
    return counted


def _assign_reference_grades(courses, required_average):
    """Điểm chữ tham khảo (hai mức liền kề) sao cho TB có trọng số >= mức cần."""
    grade_courses = [c for c in courses if _gpa_counted(c)]
    total_credits = sum(_course_credits(c) for c in grade_courses)
    if not grade_courses or total_credits <= 0:
        return {}, 0.0

    if required_average > 4.0:
        top = GRADE_SCALE[-1]
        return {c.get("ma_hp"): dict(top) for c in grade_courses if c.get("ma_hp")}, 4.0

    required_average = max(1.0, required_average)
    lower = upper = GRADE_SCALE[0]
    for idx, grade in enumerate(GRADE_SCALE):
        if grade["point"] <= required_average + EPS:
            lower = grade
            upper = GRADE_SCALE[min(idx + 1, len(GRADE_SCALE) - 1)]
        else:
            break

    assignments = {c.get("ma_hp"): dict(lower) for c in grade_courses if c.get("ma_hp")}
    if upper["point"] == lower["point"] or abs(required_average - lower["point"]) < EPS:
        return assignments, lower["point"]

    needed_quality = (required_average - lower["point"]) * total_credits
    gain_per_credit = upper["point"] - lower["point"]
    ordered = sorted(
        grade_courses,
        key=lambda c: (-_course_credits(c), _parse_semester_order(c.get("hoc_ky_du_kien")), str(c.get("ma_hp") or "")),
    )
    gained = 0.0
    for c in ordered:
        if gained + EPS >= needed_quality:
            break
        code = c.get("ma_hp")
        if not code:
            continue
        assignments[code] = dict(upper)
        gained += gain_per_credit * _course_credits(c)

    quality = sum(assignments.get(c.get("ma_hp"), lower)["point"] * _course_credits(c) for c in grade_courses)
    return assignments, quality / total_credits


def _r(value, nd=2):
    return None if value is None else round(float(value), nd)


# ---------------------------------------------------------- improvement

def improvement_candidates(data):
    """Môn đã đạt, tính GPA, có điểm chữ <= ngưỡng — sắp theo mức tăng tối đa."""
    gpa_credits = _num((data.get("gpa") or {}).get("so_tin_tinh_gpa"), 0.0)
    out = []
    for c in data.get("mon_hoc", []) or []:
        if c.get("trang_thai") != "HOAN_THANH" or not _gpa_counted(c):
            continue
        point = _course_point(c)
        if point is None or point > IMPROVE_MAX_POINT + EPS:
            continue
        credits = _course_credits(c)
        gain = (4.0 - point) * credits / gpa_credits if gpa_credits > 0 else 0.0
        out.append({
            "ma_hp": c.get("ma_hp"),
            "ten_hp": c.get("ten_hp"),
            "khoi_kt": c.get("khoi_kt"),
            "so_tin": credits,
            "diem": c.get("diem"),
            "diem_chu": c.get("diem_chu"),
            "point": point,
            "max_gpa_gain": round(gain, 3),
        })
    out.sort(key=lambda x: (-x["max_gpa_gain"], str(x["ten_hp"] or "")))
    return out


def _resolve_improvements(candidates, selection):
    """-> (danh sách áp dụng, danh sách bị bỏ qua)."""
    by_code = {c["ma_hp"]: c for c in candidates}
    applied, ignored = [], []
    for code, letter in (selection or {}).items():
        cand = by_code.get(str(code))
        new_point = GRADE_POINTS.get(str(letter))
        if cand is None or new_point is None:
            ignored.append({"ma_hp": code, "reason": "Môn không thuộc danh sách có thể cải thiện."})
            continue
        if IMPROVE_REQUIRE_HIGHER and new_point <= cand["point"] + EPS:
            ignored.append({"ma_hp": code, "ten_hp": cand["ten_hp"],
                            "reason": "Điểm dự kiến không cao hơn điểm hiện tại nên giữ điểm cũ."})
            continue
        applied.append({
            **cand,
            "new_letter": str(letter),
            "new_point": new_point,
            "delta_quality": round((new_point - cand["point"]) * cand["so_tin"], 4),
        })
    applied.sort(key=lambda x: str(x["ten_hp"] or ""))
    return applied, ignored


def _improvement_item(a):
    """Định dạng một môn học lại để đưa vào lộ trình."""
    return {
        "ma_hp": a["ma_hp"],
        "ten_hp": a["ten_hp"],
        "so_tin": a["so_tin"],
        "from": a["diem_chu"],
        "to": a["new_letter"],
        "point": a.get("point"),
    }


def _distribute_improvements(applied, semester_slots, pace_max):
    """Phân bổ môn học lại vào các kỳ, ưu tiên điểm thấp trước.

    ``semester_slots`` là list các dict đã có ``courses`` (môn mới).
    Mỗi slot sẽ được gắn ``improvements`` và ``total_credits`` = TC mới + TC học lại.
    Môn còn dư sau khi lấp các kỳ hiện có sẽ tạo thêm kỳ chỉ gồm học lại.
    """
    if not applied:
        for slot in semester_slots:
            slot.setdefault("improvements", [])
            new_tc = sum(_course_credits(c) for c in slot.get("courses") or [])
            slot["total_credits"] = round(new_tc, 1)
        return semester_slots

    # Điểm thấp trước → tăng GPA sớm; cùng điểm thì ưu tiên TC lớn (pack tốt hơn).
    pending = sorted(
        applied,
        key=lambda a: (
            a.get("point") if a.get("point") is not None else 99,
            -_num(a.get("so_tin"), 0),
            str(a.get("ten_hp") or ""),
        ),
    )
    queue = list(pending)
    max_load = float(pace_max) if pace_max else 16.0

    for slot in semester_slots:
        new_tc = sum(_course_credits(c) for c in slot.get("courses") or [])
        room = max(0.0, max_load - new_tc)
        chosen = []
        remain = []
        for a in queue:
            tc = _num(a.get("so_tin"), 0)
            if tc <= room + EPS:
                chosen.append(a)
                room -= tc
            else:
                remain.append(a)
        queue = remain
        slot["improvements"] = [_improvement_item(a) for a in chosen]
        slot["total_credits"] = round(
            new_tc + sum(_num(a.get("so_tin"), 0) for a in chosen), 1
        )

    # Còn dư → thêm kỳ chỉ học lại, vẫn tôn trọng pace_max mỗi kỳ.
    extra_idx = 0
    while queue:
        room = max_load
        chosen = []
        remain = []
        for a in queue:
            tc = _num(a.get("so_tin"), 0)
            if tc <= room + EPS:
                chosen.append(a)
                room -= tc
            else:
                remain.append(a)
        # Môn lớn hơn max_load: vẫn xếp 1 môn/kỳ để không kẹt vô hạn.
        if not chosen and remain:
            chosen = [remain.pop(0)]
        queue = remain
        if not chosen:
            break
        extra_idx += 1
        # semester_slots tự dài thêm sau mỗi lần append, nên chỉ cần +1
        # (cộng thêm extra_idx sẽ làm nhãn nhảy cóc: Kỳ +6 -> Kỳ +8).
        base_n = len(semester_slots) + 1
        semester_slots.append({
            "key": f"improve{extra_idx}",
            "label": _semester_label(base_n),
            "kind": "auto",
            "courses": [],
            "improvements": [_improvement_item(a) for a in chosen],
            "total_credits": round(sum(_num(a.get("so_tin"), 0) for a in chosen), 1),
        })

    return semester_slots


# ------------------------------------------------------------- analysis


def _sim_data(data, plan_codes):
    """Bản sao dữ liệu coi các môn người dùng chọn cho kỳ tới là 'đang học' để
    planner xếp các kỳ SAU kỳ đó."""
    if not plan_codes:
        return data
    sim = dict(data)
    sim["mon_hoc"] = [
        dict(c, trang_thai="DANG_KY_KY_NAY") if c.get("ma_hp") in plan_codes else c
        for c in data.get("mon_hoc", []) or []
    ]
    return sim


def _semester_label(n):
    return "Kỳ tới" if n == 1 else f"Kỳ +{n}"


TERMS_PER_YEAR = 3   # Phenikaa: 3 học kỳ / năm học (Điều 7.1 Quy chế)
RANK_ORDER = [("Xuất sắc", 3.6), ("Giỏi", 3.2), ("Khá", 2.5)]


def _semester_after(hv, offset):
    """Nhãn học kỳ cách học kỳ có điểm gần nhất `offset` kỳ (vd 'HK3 2027-2028')."""
    hk = (hv or {}).get("hoc_ky") or []
    if not hk or offset < 1:
        return None
    last = hk[-1]
    idx = int(str(last["nam_hoc"])[:4]) * TERMS_PER_YEAR + (int(last["ky"]) - 1) + offset
    year, term = divmod(idx, TERMS_PER_YEAR)
    return f"HK{term + 1} {year}-{year + 1}"


def _graduation_estimate(roadmap, has_current, unscheduled, hv, pace):
    """Còn bao nhiêu kỳ để học hết chương trình, theo lộ trình đã xếp (3 kỳ/năm)."""
    future = len([r for r in roadmap if r.get("kind") != "current"])
    offset = future + (1 if has_current else 0)
    return {
        "semesters_total": offset,                      # tính cả kỳ đang học
        "semesters_after_current": future,
        "has_current": bool(has_current),
        "years": round(offset / TERMS_PER_YEAR, 1),
        "terms_per_year": TERMS_PER_YEAR,
        "finish_label": _semester_after(hv, offset),
        "incomplete": unscheduled > EPS,                # còn tín chỉ chưa xếp được
        "pace_label": pace.get("label"),
    }


def _rank_outcome(max_plain, max_improve, over_limit):
    """Hạng THỰC TẾ trên bằng cho từng mục tiêu, có tính Điều 27.7 (giảm 1 mức).

    max_plain   : GPA cao nhất nếu chỉ học các môn còn lại (đạt A+ hết).
    max_improve : GPA cao nhất nếu còn học cải thiện mọi môn điểm thấp."""
    names = [n for n, _ in RANK_ORDER]
    out, best = {}, None
    for name, gpa in RANK_ORDER:
        plain = max_plain is not None and max_plain + 1e-6 >= gpa
        improve = (not plain) and max_improve is not None and max_improve + 1e-6 >= gpa
        eff = name
        if over_limit and name in ("Xuất sắc", "Giỏi"):
            eff = names[names.index(name) + 1]
        out[name] = {"gpa": gpa, "reachable": bool(plain), "needs_improvement": bool(improve),
                     "diploma_rank": eff}
        if (plain or improve) and (best is None or names.index(eff) < names.index(best)):
            best = eff
    return {"by_target": out, "best_diploma_rank": best}


def _nominal_rank(gpa):
    """Hạng theo GPA thuần (chưa áp Điều 27.7)."""
    if gpa is None:
        return None
    for name, threshold in RANK_ORDER:
        if gpa + EPS >= threshold:
            return name
    return "Trung bình"


def _apply_27_7(nominal_rank, over_limit):
    """Hạ 1 mức nếu vượt 5% và hạng là Xuất sắc/Giỏi."""
    if not nominal_rank or not over_limit:
        return nominal_rank
    names = [n for n, _ in RANK_ORDER]
    if nominal_rank in ("Xuất sắc", "Giỏi"):
        return names[names.index(nominal_rank) + 1]
    return nominal_rank


def _gpa_for_diploma_rank(desired_diploma, over_limit):
    """GPA tối thiểu để *trên bằng* còn ``desired_diploma`` (đã tính 27.7).

    Ví dụ vượt 5%: muốn bằng Giỏi → cần GPA ≥ 3.60 (vì 3.20 bị hạ xuống Khá).
    Muốn bằng Xuất sắc khi vượt 5% → không thể (trả None).
    """
    if desired_diploma == "Xuất sắc":
        return None if over_limit else 3.6
    if desired_diploma == "Giỏi":
        return 3.6 if over_limit else 3.2
    if desired_diploma == "Khá":
        return 2.5
    return None


def _diploma_plan(target, over_limit, max_plain, max_improve):
    """Kế hoạch hạng trên bằng cho mục tiêu SV đang chọn.

    - selected: hạng nút SV bấm (Xuất sắc/Giỏi/Khá) hoặc custom
    - diploma_if_hit_target_gpa: hạng in bằng nếu chỉ đạt đúng GPA mục tiêu
    - gpa_needed_for_selected_diploma: GPA cần nếu muốn *đúng* hạng đã chọn trên bằng
    - needs_ack: cần hỏi “bạn có muốn tiếp tục?” vì hạng bằng < hạng chọn
    """
    selected = target.get("label") or _nominal_rank(target.get("gpa"))
    target_gpa = target.get("gpa")
    diploma_at_target = _apply_27_7(_nominal_rank(target_gpa), over_limit)

    # Với nút Giỏi/Xuất sắc: GPA cần để *còn đúng hạng đó trên bằng*
    gpa_for_true_rank = _gpa_for_diploma_rank(selected, over_limit) if selected in ("Xuất sắc", "Giỏi", "Khá") else target_gpa

    needs_ack = bool(
        over_limit
        and selected in ("Xuất sắc", "Giỏi")
        and diploma_at_target
        and diploma_at_target != selected
    )

    # Khả thi đạt hạng bằng = selected (nếu có thể)
    true_rank_possible = gpa_for_true_rank is not None
    true_rank_plain = (
        true_rank_possible and max_plain is not None and max_plain + EPS >= gpa_for_true_rank
    )
    true_rank_with_improve = (
        true_rank_possible
        and not true_rank_plain
        and max_improve is not None
        and max_improve + EPS >= gpa_for_true_rank
    )

    return {
        "selected_rank": selected,
        "diploma_if_hit_target_gpa": diploma_at_target,
        "gpa_needed_for_selected_diploma": _r(gpa_for_true_rank) if gpa_for_true_rank is not None else None,
        "selected_diploma_unreachable": selected == "Xuất sắc" and over_limit,
        "needs_ack": needs_ack,
        "true_rank_reachable_plain": bool(true_rank_plain),
        "true_rank_reachable_with_improve": bool(true_rank_with_improve or true_rank_plain),
        "ack_message": (
            f"Bạn đã học lại vượt 5% tổng tín chỉ chương trình. "
            f"Nếu đạt GPA {target_gpa:.2f} ({selected}), trên bằng chỉ còn "
            f"{diploma_at_target}. Bạn có muốn tiếp tục?"
            if needs_ack else None
        ),
    }


def _timeline_info(hv, data, graduation_est, improve_load=0.0, pace_max=16.0):
    """Vị trí khóa học hiện tại vs tốt nghiệp đúng hạn (giả định chương trình 4 năm × 3 kỳ)."""
    PROGRAM_YEARS = 4
    hk = (hv or {}).get("hoc_ky") or []
    current_label = None
    start_label = None
    terms_done = None
    terms_total = PROGRAM_YEARS * TERMS_PER_YEAR
    on_time_remaining = None
    on_time_end_label = None

    if hk:
        first, last = hk[0], hk[-1]
        start_label = f"HK{first.get('ky')} {first.get('nam_hoc')}"
        current_label = f"HK{last.get('ky')} {last.get('nam_hoc')}"
        try:
            start_idx = int(str(first["nam_hoc"])[:4]) * TERMS_PER_YEAR + (int(first["ky"]) - 1)
            cur_idx = int(str(last["nam_hoc"])[:4]) * TERMS_PER_YEAR + (int(last["ky"]) - 1)
            terms_done = cur_idx - start_idx + 1  # tính cả kỳ hiện tại
            on_time_remaining = max(0, terms_total - terms_done)
            end_idx = start_idx + terms_total - 1
            ey, et = divmod(end_idx, TERMS_PER_YEAR)
            on_time_end_label = f"HK{et + 1} {ey}-{ey + 1}"
        except (TypeError, ValueError, KeyError):
            pass

    # Ước số kỳ cần thêm nếu còn xếp improve (ngoài lộ trình môn mới).
    extra_improve_terms = 0
    if improve_load and pace_max:
        # Phần improve đã nằm trong graduation_est.semesters nếu đã phân bổ;
        # chỉ báo thêm khi improve_load lớn so với slot đúng hạn.
        pass

    plan_terms = (graduation_est or {}).get("semesters_after_current") or 0
    late = None
    if on_time_remaining is not None:
        # plan_terms = số kỳ tương lai trên lộ trình; so với slot còn lại đúng hạn
        late = plan_terms > on_time_remaining

    return {
        "available": bool(hk),
        "program_years": PROGRAM_YEARS,
        "terms_per_year": TERMS_PER_YEAR,
        "terms_total": terms_total,
        "start_label": start_label,
        "current_label": current_label,
        "terms_done": terms_done,
        "on_time_remaining_terms": on_time_remaining,
        "on_time_end_label": on_time_end_label,
        "plan_future_terms": plan_terms,
        "plan_finish_label": (graduation_est or {}).get("finish_label"),
        "likely_late_vs_on_time": late,
        "assumption": (
            f"Đúng hạn = {PROGRAM_YEARS} năm × {TERMS_PER_YEAR} kỳ từ học kỳ đầu có điểm. "
            "SV có thể chọn chấp nhận ra trường muộn để cải thiện điểm."
        ),
    }


def _academic_summary(hv):
    """Tóm tắt cảnh báo học vụ (Điều 21) từ bảng điểm theo học kỳ."""
    if not hv or not hv.get("co_du_lieu"):
        return {"available": False}
    cb = hv.get("canh_bao", {})
    return {
        "available": True,
        "warned_before": bool(cb.get("da_bi_canh_bao")),
        "warned_semesters": cb.get("cac_ky_bi_canh_bao", []),
        "consecutive_now": cb.get("so_lan_lien_tiep_hien_tai", 0),
        "lowest_semester_gpa": cb.get("gpa_ky_thap_nhat"),
        "lowest_cumulative_gpa": cb.get("gpa_tich_luy_thap_nhat"),
        "debt_credits": cb.get("no_dong_hien_tai", 0),
        "debt_limit": 24,
        "assumption": cb.get("gia_dinh"),
    }


def analyze(data, pace_key="moderate", target=None, improvements=None, plan=None):
    """Tính toàn bộ trạng thái cố vấn. ``target`` là dict từ ``resolve_target``;
    ``improvements`` {ma_hp: điểm chữ mới}; ``plan`` {ma_hp: điểm chữ dự kiến}."""
    target = target or dict(GPA_TARGETS["gioi"])
    pace = PACE_OPTIONS.get(pace_key, PACE_OPTIONS["moderate"])
    courses = data.get("mon_hoc", []) or []
    gpa_info = data.get("gpa") or {}
    warnings = list(data.get("canh_bao_du_lieu") or [])

    # ---- GPA nền (lấy từ QLĐT làm chuẩn)
    gpa_now = gpa_info.get("gpa_4")
    gpa_now = _num(gpa_now) if gpa_now is not None else None
    g0 = _num(gpa_info.get("so_tin_tinh_gpa"), 0.0)
    q0 = (gpa_now or 0.0) * g0

    graded = [c for c in courses if c.get("trang_thai") == "HOAN_THANH" and _gpa_counted(c) and _course_point(c) is not None]
    graded_credits = sum(_course_credits(c) for c in graded)
    if graded and g0 > 0 and abs(graded_credits - g0) < 0.5:
        recomputed = sum(_course_point(c) * _course_credits(c) for c in graded) / graded_credits
        if gpa_now is not None and abs(recomputed - gpa_now) > 0.05:
            warnings.append(
                f"GPA tính lại từ điểm từng môn ({recomputed:.2f}) lệch GPA trên QLĐT ({gpa_now:.2f}); "
                "mô phỏng dùng GPA của QLĐT làm gốc."
            )

    # ---- tín chỉ còn thiếu theo chương trình đào tạo
    needs0, block_info = _block_needs(data)
    shortfall = sum(needs0.values())
    required_total = _num(data.get("tin_chi_toan_khoa"), 0.0)
    accumulated = _num(data.get("tin_chi_da_hoc"), 0.0)
    if required_total > 0 and abs((required_total - accumulated) - shortfall) > 0.5 and shortfall > 0:
        warnings.append(
            f"Tín chỉ còn thiếu theo các khối ({shortfall:g}) khác tổng toàn khóa − đã tích lũy "
            f"({max(0.0, required_total - accumulated):g}). Ứng dụng dùng số theo khối."
        )

    current_courses = [c for c in courses if c.get("trang_thai") == "DANG_KY_KY_NAY" and _gpa_counted(c)]
    needs_after_current = dict(needs0)
    current_counted = _apply_to_blocks(needs_after_current, current_courses)

    # ---- kế hoạch kỳ tới (người dùng chọn)
    by_code = {c.get("ma_hp"): c for c in courses}
    plan_courses, plan_grades = [], {}
    for code, letter in (plan or {}).items():
        c = by_code.get(str(code))
        if c is None or c.get("trang_thai") not in ("CO_THE_DANG_KY", "CAN_HOC_LAI"):
            continue
        plan_courses.append(c)
        plan_grades[c["ma_hp"]] = letter if letter in GRADE_POINTS else DEFAULT_PLAN_LETTER
    plan_courses.sort(key=lambda c: str(c.get("ten_hp") or ""))
    plan_codes = {c["ma_hp"] for c in plan_courses}

    needs_after_plan = dict(needs_after_current)
    plan_counted = _apply_to_blocks(needs_after_plan, plan_courses)
    plan_gpa_credits = sum(_course_credits(c) for c in plan_courses if _gpa_counted(c))
    plan_quality = sum(
        GRADE_POINTS[plan_grades[c["ma_hp"]]] * _course_credits(c) for c in plan_courses if _gpa_counted(c)
    )
    plan_total_credits = sum(_course_credits(c) for c in plan_courses)

    # ---- học cải thiện: điểm mới THAY điểm cũ
    candidates = improvement_candidates(data)
    applied, ignored = _resolve_improvements(candidates, improvements)
    improve_delta = sum(a["delta_quality"] for a in applied)
    improve_load = sum(a["so_tin"] for a in applied)
    q1 = q0 + improve_delta
    gpa_after_improve = q1 / g0 if g0 > 0 else None

    # ---- cảnh báo học lại 5% (Điều 27.7)
    # Có bảng điểm theo học kỳ -> đếm đúng môn từng bị F ở bất kỳ lần học nào (kể cả đã học
    # lại và đạt). Chưa có -> chỉ biết môn đang nợ (CAN_HOC_LAI), nên history_complete=False.
    hv = data.get("hoc_vu") or {}
    hv_retake = hv.get("hoc_lai") if hv.get("co_du_lieu") else None
    if hv_retake:
        repeat_detected = _num(hv_retake.get("tin_chi"), 0.0)
        repeat_courses = hv_retake.get("mon", [])
        history_complete = True
    else:
        repeat_detected = sum(_course_credits(c) for c in courses
                              if c.get("trang_thai") == "CAN_HOC_LAI" and not c.get("khong_tinh_tin_chi", False))
        repeat_courses = []
        history_complete = False
    repeat_threshold = required_total * 0.05
    over_limit = repeat_detected > repeat_threshold + EPS
    downgrade_risk = target["gpa"] >= 3.2 - EPS and over_limit
    # Hạng sau khi bị giảm một mức nếu đạt đúng mục tiêu (chỉ Giỏi/Xuất sắc bị giảm).
    downgraded_to = None
    if downgrade_risk:
        downgraded_to = "Khá" if target["gpa"] < 3.6 - EPS else "Giỏi"

    # Điều 27.7: học lại > 5% thì bằng Giỏi/Xuất sắc bị hạ 1 bậc. Muốn VẪN ra bằng Giỏi thì GPA
    # toàn khóa phải đạt mức Xuất sắc (3.60); lộ trình phải tính theo mốc này, không phải 3.20.
    goal_gpa = target["gpa"]
    raised_by_retake = False
    if over_limit and target.get("key") == "gioi":
        goal_gpa = max(goal_gpa, 3.6)
        raised_by_retake = goal_gpa > target["gpa"] + EPS
    target = dict(target, effective_gpa=goal_gpa, raised_by_retake=raised_by_retake)

    # ---- GPA cần đạt cho phần còn lại
    # U: tín chỉ chưa có điểm (đang học + còn lại sau kỳ tới); F: tổng tín chỉ tính GPA lúc tốt nghiệp.
    ungraded = max(0.0, shortfall - plan_counted)
    q2 = q1 + plan_quality
    g2 = g0 + plan_gpa_credits
    final_credits = g2 + ungraded
    gpa_after_plan = q2 / g2 if g2 > 0 else gpa_now

    if ungraded > EPS:
        required = (goal_gpa * final_credits - q2) / ungraded
        max_final = (q2 + 4.0 * ungraded) / final_credits
        feasible = required <= 4.0 + 1e-6
        # Trần nếu cải thiện TOÀN BỘ môn điểm thấp lên A+ (thay vì chỉ môn đã chọn).
        applied_codes = {a["ma_hp"] for a in applied}
        extra = sum((4.0 - c["point"]) * c["so_tin"] for c in candidates if c["ma_hp"] not in applied_codes)
        max_with_all = (q2 + extra + 4.0 * ungraded) / final_credits
        feasible_with_improve = (goal_gpa * final_credits - (q2 + extra)) / ungraded <= 4.0 + 1e-6
    else:
        required = None
        max_final = q2 / final_credits if final_credits > 0 else gpa_now
        feasible = max_final is not None and max_final + 1e-6 >= goal_gpa
        extra = sum((4.0 - c["point"]) * c["so_tin"] for c in candidates
                    if c["ma_hp"] not in {a["ma_hp"] for a in applied})
        max_with_all = (q2 + extra) / final_credits if final_credits > 0 else max_final
        feasible_with_improve = max_with_all is not None and max_with_all + 1e-6 >= goal_gpa

    # ---- lộ trình: gợi ý độc lập + lộ trình sau kế hoạch của người dùng
    base_plan = build_academic_plan(data, pace_key)
    sim_plan = build_academic_plan(_sim_data(data, plan_codes), pace_key) if plan_codes else base_plan
    suggestion_sem = base_plan["semesters"][0] if base_plan["semesters"] else None
    suggestions = [dict(c) for c in suggestion_sem["courses"]] if suggestion_sem else []
    suggestion_total = round(sum(_course_credits(c) for c in suggestions), 1)

    real_current = sim_plan.get("current_semester")
    real_current_courses = [c for c in (real_current["courses"] if real_current else []) if c.get("ma_hp") not in plan_codes]

    if plan_codes:
        later = sim_plan["semesters"]
        next_sem_source, next_courses = "plan", plan_courses
        later_start = 2
    else:
        later = base_plan["semesters"][1:]
        next_sem_source = "auto"
        next_courses = suggestions
        later_start = 2

    # Điểm chữ tham khảo cho các môn CHƯA có điểm do người dùng đặt.
    unfinished = list(real_current_courses)
    if next_sem_source == "auto":
        unfinished += next_courses
    for sem in later:
        unfinished += sem["courses"]
    required_for_ref = required if required is not None else goal_gpa
    assignments, suggested_avg = _assign_reference_grades(unfinished, required_for_ref)

    # Tự kiểm tra: cộng điểm của CHÍNH các môn đã xếp trong lộ trình (môn học lại/cải thiện đã
    # nằm trong q2) rồi so với mục tiêu thật. Nếu không đạt thì lộ trình không được coi là đủ.
    rc_credits = sum(_course_credits(c) for c in unfinished if _gpa_counted(c))
    rc_quality = sum(assignments[c["ma_hp"]]["point"] * _course_credits(c)
                     for c in unfinished if _gpa_counted(c) and c.get("ma_hp") in assignments)
    rc_total = g2 + rc_credits
    rc_gpa = (q2 + rc_quality) / rc_total if rc_total > 0 else None
    roadmap_check = {
        "final_gpa": _r(rc_gpa),
        "goal_gpa": _r(goal_gpa),
        "meets_goal": bool(rc_gpa is not None and round(rc_gpa, 2) + 1e-9 >= goal_gpa),
        "credits": _r(rc_total, 1),
    }

    def decorate(c):
        item = dict(c)
        g = assignments.get(c.get("ma_hp"))
        if g:
            item["grade_target"] = g
        return item

    roadmap = []
    if real_current_courses:
        roadmap.append({
            "key": "current", "label": "Kỳ hiện tại", "kind": "current",
            "total_credits": round(sum(_course_credits(c) for c in real_current_courses), 1),
            "courses": [decorate(c) for c in real_current_courses],
            "improvements": [],
        })

    # Xây các kỳ tương lai (môn mới) rồi phân bổ môn học lại vào từng kỳ.
    future_slots = []
    next_entry = {
        "key": "next", "label": "Kỳ tới", "kind": "plan" if next_sem_source == "plan" else "auto",
        "courses": [],
        "improvements": [],
    }
    for c in next_courses:
        item = dict(c)
        if next_sem_source == "plan":
            letter = plan_grades[c["ma_hp"]]
            item["grade_target"] = {"letter": letter, "point": GRADE_POINTS[letter],
                                    "min_10": next(g["min_10"] for g in GRADE_SCALE if g["letter"] == letter),
                                    "user_set": True}
        else:
            item = decorate(c)
        next_entry["courses"].append(item)
    if next_entry["courses"] or applied:
        future_slots.append(next_entry)
    for i, sem in enumerate(later):
        future_slots.append({
            "key": f"later{i}", "label": _semester_label(later_start + i), "kind": "auto",
            "courses": [decorate(c) for c in sem["courses"]],
            "improvements": [],
        })

    # Ưu tiên điểm thấp trước; tôn trọng pace max mỗi kỳ; dư thì thêm kỳ.
    future_slots = _distribute_improvements(applied, future_slots, pace["max"])
    # Bỏ slot rỗng (không có môn mới lẫn học lại).
    kept = [s for s in future_slots if (s.get("courses") or s.get("improvements"))]
    # Đánh số lại nhãn theo vị trí SAU khi bỏ slot rỗng, để nhãn "Kỳ +N" liên tục
    # và khớp với nhãn học kỳ thật (HK1 2028-2029...) mà frontend tính theo vị trí.
    for pos, s in enumerate(kept, start=1):
        s["label"] = _semester_label(pos)
    roadmap.extend(kept)

    scheduled_ungraded = sum(_course_credits(c) for c in unfinished)
    unscheduled = max(0.0, ungraded - scheduled_ungraded)
    blocked = sim_plan.get("blocked", [])

    # ---- bảng khối: còn cần hoàn thành những gì
    blocks = []
    for code, bi in sorted(block_info.items()):
        remaining_now = needs0[code]
        in_progress = remaining_now - needs_after_current[code]
        in_plan = needs_after_current[code] - needs_after_plan[code]
        left = needs_after_plan[code]
        if remaining_now <= EPS and bi["required"] <= EPS:
            continue
        blocks.append({
            "ma": code, "ten": bi["ten"], "tu_chon": bi["tu_chon"],
            "required": bi["required"], "accumulated": bi["accumulated"],
            "in_progress": round(in_progress, 1), "in_plan": round(in_plan, 1),
            "left": round(left, 1), "done": remaining_now <= EPS,
        })

    # ---- mô-đun "mở khóa"
    unlocked = []
    if plan_codes:
        for c in courses:
            if c.get("trang_thai") != "BI_KHOA":
                continue
            missing = [str(x) for x in (c.get("mon_con_thieu") or [])]
            if missing and all(m in plan_codes for m in missing):
                unlocked.append({"ma_hp": c.get("ma_hp"), "ten_hp": c.get("ten_hp"), "so_tin": c.get("so_tin")})

    # ---- kết luận
    verdict = _verdict(target, required, max_final, max_with_all, feasible, feasible_with_improve,
                       bool(applied), ungraded)
    projected_final = None
    if final_credits > 0:
        avg = 4.0 if (required is not None and required > 4.0) else (suggested_avg if ungraded > EPS else 0.0)
        projected_final = (q2 + avg * ungraded) / final_credits

    graduation = _graduation_estimate(
        roadmap, bool(real_current_courses), unscheduled, hv, pace
    )
    timeline = _timeline_info(
        hv, data, graduation, improve_load=improve_load, pace_max=pace["max"]
    )
    diploma_plan = _diploma_plan(target, over_limit, max_final, max_with_all)

    return {
        "target": target,
        "pace": dict(pace),
        "grade_scale": GRADE_SCALE,
        "credits": {
            "required_total": _r(required_total, 1),
            "accumulated": _r(accumulated, 1),
            "shortfall": _r(shortfall, 1),
            "in_progress": _r(sum(_course_credits(c) for c in current_courses), 1),
            "in_progress_counted": _r(current_counted, 1),
            "after_current": _r(max(0.0, shortfall - current_counted), 1),
            "planned_next": _r(plan_counted, 1),
            "planned_next_total": _r(plan_total_credits, 1),
            "improve_load": _r(improve_load, 1),
            "next_load": _r(plan_total_credits + improve_load, 1),
            "after_plan": _r(sum(needs_after_plan.values()) if needs_after_plan else 0.0, 1),
            "ungraded": _r(ungraded, 1),
            "unscheduled": _r(unscheduled, 1),
        },
        "gpa": {
            "current": _r(gpa_now),
            "current_credits": _r(g0, 1),
            "after_improve": _r(gpa_after_improve),
            "improve_gain": _r((gpa_after_improve - gpa_now) if gpa_after_improve is not None and gpa_now is not None else None),
            "after_plan": _r(gpa_after_plan),
            "required_remaining_average": _r(required),
            "required_letter": letter_for_required(required) if required is not None and required <= 4.0 + 1e-6 else None,
            "remaining_credits": _r(ungraded, 1),
            "final_credits": _r(final_credits, 1),
            "max_final": _r(max_final),
            "max_final_with_all_improvements": _r(max_with_all),
            "projected_final": _r(projected_final),
            "suggested_remaining_average": _r(suggested_avg) if ungraded > EPS else None,
            "feasible": bool(feasible),
            "feasible_with_improvements": bool(feasible_with_improve),
            "classification_projected": classify(projected_final),
        },
        "verdict": verdict,
        "improvement": {
            "candidates": [
                {**c, "selected": next((a["new_letter"] for a in applied if a["ma_hp"] == c["ma_hp"]), None)}
                for c in candidates
            ],
            "applied": applied,
            "ignored": ignored,
            "gpa_gain": _r((gpa_after_improve - gpa_now) if gpa_after_improve is not None and gpa_now is not None else None, 3),
            "max_point": IMPROVE_MAX_POINT,
        },
        "suggestions": {
            "courses": suggestions,
            "total_credits": suggestion_total,
            "min_credits": pace["min"], "max_credits": pace["max"], "target_credits": pace["target"],
            "message": "" if suggestions else "Hiện chưa có môn nào đủ điều kiện để xếp vào kỳ tới.",
        },
        "simulation": {
            "plan_courses": [
                {"ma_hp": c["ma_hp"], "ten_hp": c.get("ten_hp"), "so_tin": c.get("so_tin"),
                 "khoi_kt": c.get("khoi_kt"), "trang_thai": c.get("trang_thai"),
                 "letter": plan_grades[c["ma_hp"]], "gpa_counted": _gpa_counted(c)}
                for c in plan_courses
            ],
            "unlocked": unlocked,
        },
        "roadmap": {
            "semesters": roadmap,
            "blocks": blocks,
            "blocked_count": len(blocked),
            "blocked": [{"ma_hp": c.get("ma_hp"), "ten_hp": c.get("ten_hp"), "so_tin": c.get("so_tin"),
                         "missing": c.get("missing_prereqs_for_plan", [])} for c in blocked][:20],
            "scheduled_credits": _r(scheduled_ungraded, 1),
        },
        "retake": {
            "detected_credits": _r(repeat_detected, 1),
            "threshold_credits": _r(repeat_threshold, 1),
            "threshold_percent": 5,
            "over_limit": bool(over_limit),
            "downgrade_risk_for_target": bool(downgrade_risk),
            "downgraded_to": downgraded_to,
            "courses": repeat_courses,
            "history_complete": history_complete,
        },
        "diploma_plan": diploma_plan,
        "roadmap_check": roadmap_check,
        "academic": _academic_summary(hv),
        "graduation": graduation,
        "timeline": timeline,
        "rank_outcome": _rank_outcome(max_final, max_with_all, over_limit),
        "warnings": warnings,
    }


# Trung bình cần cho phần còn lại mà ta coi là "vừa sức" (≈ B+ trở lên).
COMFORT_REQUIRED = 3.5
RECOMMEND_LETTERS = ("A", "A+")


def recommend_improvements(data, base):
    """Chọn ÍT môn học cải thiện nhất để mục tiêu khả thi.

    ``base`` là kết quả ``analyze`` chưa áp lựa chọn nào. Chỉ gợi ý khi mục tiêu
    KHÔNG khả thi nếu chỉ học môn mới nhưng khả thi khi cải thiện.

    Ưu tiên 1: học lại lên A sao cho phần còn lại chỉ cần ≤ COMFORT_REQUIRED.
    Ưu tiên 2: nếu chưa đủ, học lại lên A+ sao cho phần còn lại cần ≤ 4.0.
    Trả về ``{"needed", "reachable", "letter", "courses", "gpa_after", "required_after"}``.
    """
    gpa = base["gpa"]
    out = {"needed": False, "reachable": bool(gpa["feasible"]), "letter": None,
           "courses": [], "gpa_after": None, "required_after": None}
    if gpa["feasible"]:
        return out
    out["needed"] = True
    if not gpa["feasible_with_improvements"]:
        out["reachable"] = False
        return out

    info = data.get("gpa") or {}
    g0 = _num(info.get("so_tin_tinh_gpa"), 0.0)
    gpa_now = _num(info.get("gpa_4"), 0.0)
    q0 = gpa_now * g0
    goal = base["target"].get("effective_gpa", base["target"]["gpa"])
    final_credits = _num(gpa.get("final_credits"), 0.0)
    ungraded = _num(gpa.get("remaining_credits"), 0.0)
    candidates = base["improvement"]["candidates"]
    if g0 <= 0 or final_credits <= 0 or not candidates:
        out["reachable"] = False
        return out

    def pick(letter, cap):
        new_point = GRADE_POINTS[letter]
        need = goal * final_credits - q0 - cap * ungraded
        scored = [(max(0.0, (new_point - c["point"]) * c["so_tin"]), c) for c in candidates]
        scored.sort(key=lambda x: (-x[0], str(x[1].get("ten_hp") or "")))
        chosen, total = [], 0.0
        for gain, c in scored:
            if total >= need - 1e-6:
                break
            if gain <= EPS:
                continue
            chosen.append((gain, c))
            total += gain
        return (chosen, total) if total >= need - 1e-6 else (None, total)

    letter, chosen, total = None, None, 0.0
    for letter_try, cap in (("A", COMFORT_REQUIRED), ("A+", 4.0)):
        chosen, total = pick(letter_try, cap)
        if chosen is not None:
            letter = letter_try
            break
    if chosen is None:
        out["reachable"] = False
        return out

    out["reachable"] = True
    out["letter"] = letter
    out["courses"] = [
        {"ma_hp": c["ma_hp"], "ten_hp": c["ten_hp"], "so_tin": c["so_tin"],
         "diem": c.get("diem"), "diem_chu": c["diem_chu"], "point": c["point"],
         "new_letter": letter, "gain": _r(gain, 3)}
        for gain, c in chosen
    ]
    out["gpa_after"] = _r((q0 + total) / g0)
    out["required_after"] = _r((goal * final_credits - q0 - total) / ungraded) if ungraded > EPS else None
    return out


def _verdict(target, required, max_final, max_with_all, feasible, feasible_with_improve, has_improve, ungraded):
    label = target.get("label", "đã chọn")
    if target.get("raised_by_retake"):
        label = f"{label} (cần GPA {target['effective_gpa']:.2f} vì học lại vượt 5%)"
    if ungraded <= EPS:
        if feasible:
            return {"level": "ok", "title": f"Đã đủ tín chỉ — GPA đạt mục tiêu {label}.",
                    "detail": f"GPA cuối: {max_final:.2f}."}
        return {"level": "infeasible", "title": f"Đã hết tín chỉ còn lại — GPA không đạt mục tiêu {label}.",
                "detail": "Chỉ còn cách học cải thiện để nâng điểm." if feasible_with_improve else
                          "Kể cả cải thiện hết môn điểm thấp vẫn chưa đủ."}
    if required is not None and required <= 0:
        return {"level": "ok", "title": f"Mục tiêu {label} đã đảm bảo.",
                "detail": "Chỉ cần đạt điểm qua môn ở phần còn lại."}
    if feasible:
        hard = required > 3.5
        return {
            "level": "hard" if hard else "ok",
            "title": f"Mục tiêu {label} khả thi" + (" nhưng rất sát." if hard else "."),
            "detail": f"Cần trung bình {required:.2f}/4.0 (≈ {letter_for_required(required)}) cho {ungraded:g} TC còn lại.",
        }
    if feasible_with_improve:
        return {
            "level": "infeasible",
            "title": f"Mục tiêu {label} không khả thi theo phương án hiện tại.",
            "detail": (f"Cần {required:.2f}/4.0 cho phần còn lại (vượt 4.0); tối đa chỉ đạt {max_final:.2f}. "
                       f"Nếu cải thiện các môn điểm thấp có thể lên tới {max_with_all:.2f} — hãy thử ở mục Học cải thiện."),
        }
    return {
        "level": "infeasible",
        "title": f"Mục tiêu {label} không khả thi.",
        "detail": (f"Cần {required:.2f}/4.0 cho phần còn lại; tối đa chỉ đạt {max_final:.2f}, "
                   f"kể cả khi cải thiện hết môn điểm thấp ({max_with_all:.2f}). Hãy cân nhắc mục tiêu thấp hơn."),
    }

"""Planner xếp môn theo kỳ — NGUỒN DUY NHẤT cho gợi ý kỳ tới / lộ trình.

Dùng chung cho server.py (web) và build_roadmap.py (CLI + trường goi_y_ky_toi
trong roadmap.json), để hai nơi không bao giờ lệch logic nhau.
Hàm thuần: chỉ nhận dict dữ liệu roadmap, không đụng Flask/session.
"""

from build_roadmap import make_current_filter

PACE_OPTIONS = {
    "moderate": {"key": "moderate", "label": "Học vừa", "min": 8, "max": 10, "target": 9},
    "heavy": {"key": "heavy", "label": "Học nhiều", "min": 11, "max": 13, "target": 12},
    "fast": {"key": "fast", "label": "Hỏa tốc", "min": 14, "max": 16, "target": 16},
}


def _num(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _course_credits(course):
    return max(0.0, _num(course.get("so_tin"), 0.0))


def _parse_semester_order(value):
    """Đọc số thứ tự trong '(7)' hoặc '(1,2,3)'; không có thì xếp sau."""
    import re
    if not isinstance(value, str):
        return 999
    m = re.search(r"\(([\d,]+)\)\s*$", value.strip())
    if not m:
        return 999
    nums = [int(x) for x in m.group(1).split(",") if x.strip().isdigit()]
    return min(nums) if nums else 999


def _semester_label(index):
    if index == 1:
        return "Kỳ tiếp theo"
    if index == 2:
        return "Kỳ sau nữa"
    if index == 3:
        return "Kỳ tiếp nữa"
    return f"Kỳ +{index}"


def _priority_score(course, unlock_count=0):
    """Điểm ưu tiên chỉ dùng để chọn khi có nhiều tổ hợp cùng số tín chỉ."""
    mandatory = not bool(course.get("khoi_la_tu_chon"))
    score = 10000 if mandatory else 1000
    if course.get("trang_thai") == "CAN_HOC_LAI":
        score += 2500
    sem = _parse_semester_order(course.get("hoc_ky_du_kien"))
    if sem < 999:
        score += max(0, 600 - sem * 20)
    score += unlock_count * 180
    return score


def _pick_semester_subset(ready, pace, pending_courses):
    """Knapsack nhỏ theo tín chỉ để lấy tổ hợp gần mức học đã chọn nhất."""
    if not ready:
        return []

    prereq_dependents = {}
    for c in pending_courses:
        for prereq in c.get("tien_quyet", []) or []:
            prereq_dependents[prereq] = prereq_dependents.get(prereq, 0) + 1

    # Tín chỉ chương trình hiện đều theo bước 1 TC, nhưng dùng x10 để vẫn an
    # toàn nếu sau này có 0.5 TC.
    max_units = int(round(pace["max"] * 10))
    target_units = int(round(pace["target"] * 10))
    min_units = int(round(pace["min"] * 10))

    # total_units -> (score, selected_indices)
    dp = {0: (0, [])}
    for idx, course in enumerate(ready):
        units = int(round(_course_credits(course) * 10))
        if units <= 0 or units > max_units:
            continue
        score = _priority_score(course, prereq_dependents.get(course.get("ma_hp"), 0))
        for total, (old_score, chosen) in list(dp.items())[::-1]:
            new_total = total + units
            if new_total > max_units:
                continue
            candidate = (old_score + score, chosen + [idx])
            current = dp.get(new_total)
            if current is None or candidate[0] > current[0]:
                dp[new_total] = candidate

    nonzero = [u for u in dp if u > 0]
    if not nonzero:
        return []
    in_range = [u for u in nonzero if u >= min_units]
    pool = in_range if in_range else nonzero

    # Ưu tiên gần target. Nếu bằng nhau, Hỏa tốc lấy nhiều TC hơn; hai mode
    # còn lại lấy tổ hợp có score học thuật cao hơn trước.
    def rank(units):
        score = dp[units][0]
        distance = abs(units - target_units)
        credit_tiebreak = -units if pace["key"] == "fast" else 0
        # Trong cùng khoảng tín chỉ, ưu tiên môn bắt buộc / môn mở khóa trước;
        # sau đó mới tối ưu gần mức target của mode.
        return (-score, distance, credit_tiebreak)

    best_units = min(pool, key=rank)
    return [ready[i] for i in dp[best_units][1]]


def _build_academic_plan(data, pace_key="moderate", pace=None):
    """``pace`` (dict min/max/target/key) ghi đè ``pace_key`` khi truyền vào."""
    pace = pace or PACE_OPTIONS.get(pace_key, PACE_OPTIONS["moderate"])
    courses = data.get("mon_hoc", []) or []
    groups = data.get("khoi_summary", {}) or {}

    current_courses = [
        c for c in courses
        if c.get("trang_thai") == "DANG_KY_KY_NAY"
        and not c.get("khong_tinh_tin_chi", False)
    ]
    # Môn đang học kỳ này tuyệt đối không được đưa lại vào gợi ý kỳ tiếp theo.
    not_current = make_current_filter(courses)
    current_codes = {c.get("ma_hp") for c in current_courses if c.get("ma_hp")}
    passed_codes = {
        c.get("ma_hp") for c in courses
        if c.get("trang_thai") == "HOAN_THANH" and c.get("ma_hp")
    }

    current_by_group = {}
    for c in current_courses:
        group = c.get("khoi_kt")
        current_by_group[group] = current_by_group.get(group, 0.0) + _course_credits(c)

    elective_need = {}
    for code, info in groups.items():
        if info.get("bo_qua") or str(code).upper().startswith("F"):
            continue
        if not info.get("la_tu_chon"):
            continue
        required = _num(info.get("tin_bat_buoc", info.get("tong_tin", 0)))
        accumulated = _num(info.get("da_tich_luy", 0))
        elective_need[code] = max(0.0, required - accumulated - current_by_group.get(code, 0.0))

    pool = [
        c for c in courses
        if c.get("trang_thai") in ("CO_THE_DANG_KY", "BI_KHOA", "CAN_HOC_LAI")
        and not_current(c)
        and not c.get("khong_tinh_tin_chi", False)
        and c.get("trang_thai") != "KHONG_BAT_BUOC"
        and not (c.get("khoi_la_tu_chon") and c.get("khoi_da_du"))
        # Môn không thuộc khối nào (vd "Tự chọn tự do N") là chỗ giữ chỗ: tín chỉ của nó
        # không nằm trong phần còn thiếu của bất kỳ khối nào nên không đưa vào lộ trình.
        and c.get("khoi_kt")
    ]

    # Môn bắt buộc luôn cần; môn tự chọn chỉ còn cần nếu khối chưa đủ TC.
    def still_needed(c):
        if not c.get("khoi_la_tu_chon"):
            return True
        return elective_need.get(c.get("khoi_kt"), 0.0) > 0.001

    completed_before = set(passed_codes) | set(current_codes)  # giả định môn đang học sẽ qua
    semesters = []
    scheduled_codes = set()

    for sem_index in range(1, 17):
        pending = [c for c in pool if c.get("ma_hp") not in scheduled_codes and still_needed(c)]
        if not pending:
            break

        ready = [
            c for c in pending
            if all(p in completed_before for p in (c.get("tien_quyet", []) or []))
        ]
        if not ready:
            break

        # Mandatory trước, sau đó kỳ dự kiến sớm. Với mỗi khối tự chọn chỉ
        # đưa vào đủ số ứng viên để lấp phần tín còn thiếu, tránh gợi ý học
        # thừa cả loạt môn trong một khối tự chọn.
        ready.sort(key=lambda c: (
            bool(c.get("khoi_la_tu_chon")),
            _parse_semester_order(c.get("hoc_ky_du_kien")),
            -_course_credits(c),
            str(c.get("ma_hp") or ""),
        ))
        limited_ready = [c for c in ready if not c.get("khoi_la_tu_chon")]
        elective_groups = {}
        for c in ready:
            if c.get("khoi_la_tu_chon"):
                elective_groups.setdefault(c.get("khoi_kt"), []).append(c)
        for group, items in elective_groups.items():
            need = elective_need.get(group, 0.0)
            picked_credits = 0.0
            for c in items:
                if picked_credits + 1e-9 >= need:
                    break
                limited_ready.append(c)
                picked_credits += _course_credits(c)

        selected = _pick_semester_subset(limited_ready, pace, pending)
        if not selected:
            break

        total = sum(_course_credits(c) for c in selected)
        semesters.append({
            "index": sem_index,
            "label": _semester_label(sem_index),
            "total_credits": round(total, 1),
            "courses": [dict(c) for c in selected],
        })

        for c in selected:
            code = c.get("ma_hp")
            if code:
                scheduled_codes.add(code)
                completed_before.add(code)
            if c.get("khoi_la_tu_chon"):
                group = c.get("khoi_kt")
                elective_need[group] = max(0.0, elective_need.get(group, 0.0) - _course_credits(c))

    # Chỉ báo blocked với các môn thực sự còn cần sau khi đã chạy planner.
    blocked = []
    for c in pool:
        if c.get("ma_hp") in scheduled_codes or not still_needed(c):
            continue
        item = dict(c)
        item["missing_prereqs_for_plan"] = [
            p for p in (c.get("tien_quyet", []) or []) if p not in completed_before
        ]
        blocked.append(item)

    return {
        "pace": dict(pace),
        "current_semester": {
            "label": "Kỳ hiện tại",
            "total_credits": round(sum(_course_credits(c) for c in current_courses), 1),
            "courses": [dict(c) for c in current_courses],
        } if current_courses else None,
        "semesters": semesters,
        "blocked": blocked,
        "semester_count": len(semesters),
        "future_credits": round(sum(s["total_credits"] for s in semesters), 1),
    }


build_academic_plan = _build_academic_plan

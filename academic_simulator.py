"""Multi-semester study-plan validation and persistence helpers.

The simulator deliberately reports estimates, not official enrollment eligibility.
It uses only course and prerequisite data already present in a student's roadmap.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from pathlib import Path
from uuid import uuid4

ALLOWED_STATUSES = {"HOAN_THANH", "DANG_KY_KY_NAY", "CO_THE_DANG_KY", "BI_KHOA", "CAN_HOC_LAI", "KHONG_BAT_BUOC"}


def _credits(course):
    try:
        return max(0.0, float(course.get("so_tin") or 0))
    except (TypeError, ValueError):
        return 0.0


MIN_CREDITS = 8
MAX_CREDITS = 16


def _remaining_credits_to_graduate(roadmap: dict, by_code: dict, passed: set, in_progress: set) -> float:
    """Tín chỉ còn phải học SAU khi các môn đang học kỳ này hoàn thành.

    Ưu tiên số liệu của trường (tin_chi_toan_khoa - tin_chi_da_hoc); nếu thiếu thì
    cộng tín chỉ các môn bắt buộc chưa đạt trong dữ liệu chương trình.
    """
    in_progress_credits = sum(_credits(by_code[c]) for c in in_progress)
    total = roadmap.get("tin_chi_toan_khoa")
    done = roadmap.get("tin_chi_da_hoc")
    try:
        if total is not None and done is not None:
            return max(0.0, float(total) - float(done) - in_progress_credits)
    except (TypeError, ValueError):
        pass
    return sum(
        _credits(c) for code, c in by_code.items()
        if code not in passed and code not in in_progress
        and c.get("trang_thai") != "KHONG_BAT_BUOC"
        and not c.get("khong_tinh_tin_chi")
        and not c.get("khoi_la_tu_chon")
    )


def _semester_sort(value):
    match = re.search(r"(\d+)$", str(value or ""))
    return int(match.group(1)) if match else 0


def validate_plan(roadmap: dict, plan: dict) -> dict:
    """Validate a plan shaped as {name, semesters:[{label, course_ids:[]}]}.

    Existing passed courses are treated as completed. Courses in progress are
    not treated as passed; their dependants are marked conditional.
    """
    courses = roadmap.get("mon_hoc", []) or []
    by_code = {str(c.get("ma_hp")): c for c in courses if c.get("ma_hp")}

    def nm(code):
        course = by_code.get(str(code))
        return str((course or {}).get("ten_hp") or "một học phần")

    def nms(codes):
        return ", ".join(nm(x) for x in codes)

    passed = {code for code, c in by_code.items() if c.get("trang_thai") == "HOAN_THANH"}
    in_progress = {code for code, c in by_code.items() if c.get("trang_thai") == "DANG_KY_KY_NAY"}
    semesters = plan.get("semesters", [])
    errors, warnings, result_semesters = [], [], []
    scheduled_at = {}
    seen = set()
    remaining_total = _remaining_credits_to_graduate(roadmap, by_code, passed, in_progress)
    planned_so_far = 0.0

    if not isinstance(semesters, list) or len(semesters) > 18:
        return {"valid": False, "errors": ["Kế hoạch phải có tối đa 18 học kỳ."], "warnings": [], "semesters": [], "summary": {}}

    for index, sem in enumerate(semesters):
        label = str(sem.get("label") or f"Học kỳ {index + 1}")[:80]
        raw_codes = sem.get("course_ids", [])
        if not isinstance(raw_codes, list):
            errors.append(f"{label}: danh sách môn không hợp lệ.")
            raw_codes = []
        if len(raw_codes) > 100:
            errors.append(f"{label}: số môn vượt giới hạn kiểm tra.")
            raw_codes = raw_codes[:100]
        selected, credits = [], 0.0
        for raw_code in raw_codes:
            code = str(raw_code)[:80]
            if code not in by_code:
                errors.append(f"{label}: không tìm thấy học phần đã chọn trong dữ liệu chương trình.")
                continue
            if code in seen or code in passed:
                errors.append(f"{label}: môn {nm(code)} bị chọn lặp hoặc đã hoàn thành.")
                continue
            course = by_code[code]
            if course.get("trang_thai") == "DANG_KY_KY_NAY":
                errors.append(f"{label}: {nm(code)} đang học trong kỳ hiện tại, không thể xếp lại vào kỳ tương lai.")
                continue
            if course.get("trang_thai") == "KHONG_BAT_BUOC":
                warnings.append(f"{nm(code)} đang được đánh dấu không bắt buộc trong dữ liệu nguồn.")
            if course.get("khong_tinh_tin_chi"):
                warnings.append(f"{nm(code)} không tính vào tổng tín chỉ theo dữ liệu nguồn.")
            prereqs = [str(p) for p in (course.get("tien_quyet") or [])]
            missing = [p for p in prereqs if p not in passed and p not in scheduled_at]
            conditional = [p for p in prereqs if p in in_progress]
            if missing:
                errors.append(f"{label}: {nm(code)} chưa có điều kiện tiên quyết {nms(missing)} trong dữ liệu đã đạt hoặc các kỳ trước.")
            if conditional:
                warnings.append(f"{label}: {nm(code)} phụ thuộc vào kết quả chưa có của {nms(conditional)}; cần xác nhận sau khi có điểm.")
            # A prerequisite scheduled in the same or a later semester is invalid.
            for prereq in prereqs:
                if prereq in scheduled_at and scheduled_at[prereq] >= index:
                    errors.append(f"{label}: {nm(code)} phải học sau môn tiên quyết {nm(prereq)}.")
            selected.append({"ma_hp": code, "ten_hp": course.get("ten_hp") or code, "so_tin": _credits(course), "khoi_kt": course.get("khoi_kt") or "", "tien_quyet": prereqs})
            credits += _credits(course)
            seen.add(code)
            scheduled_at[code] = index
        # Điều 8.3 Quy chế: 8–16 tín chỉ/kỳ. Mức tối thiểu không áp dụng khi tổng tín chỉ
        # còn lại chưa tích lũy (tính đến đầu kỳ này) ít hơn mức tối thiểu.
        remaining_before = max(0.0, remaining_total - planned_so_far)
        if credits < MIN_CREDITS and remaining_before >= MIN_CREDITS:
            errors.append(
                f"{label}: cần tối thiểu {MIN_CREDITS} tín chỉ (hiện có {round(credits, 1)}; "
                f"còn {round(remaining_before, 1)} tín chỉ chưa học)."
            )
        if credits > MAX_CREDITS:
            errors.append(f"{label}: tối đa {MAX_CREDITS} tín chỉ (hiện có {round(credits, 1)}).")
        planned_so_far += credits
        result_semesters.append({"label": label, "index": index, "courses": selected, "total_credits": round(credits, 1)})

    # Estimate outstanding mandatory courses from the source course pool.
    remaining_mandatory = [c for code, c in by_code.items()
        if code not in passed and code not in in_progress and code not in seen
        and not c.get("khoi_la_tu_chon") and c.get("trang_thai") not in ("KHONG_BAT_BUOC",)
        and not c.get("khong_tinh_tin_chi")]
    summary = {
        "semester_count": len(result_semesters),
        "planned_courses": len(seen),
        "planned_credits": round(sum(s["total_credits"] for s in result_semesters), 1),
        "remaining_mandatory_courses_in_source": len(remaining_mandatory),
        "remaining_mandatory_credits_in_source": round(sum(_credits(c) for c in remaining_mandatory), 1),
        "completed_courses_excluded": len(passed),
        "in_progress_courses_not_assumed_passed": len(in_progress),
    }
    if remaining_mandatory:
        warnings.append("Còn môn bắt buộc chưa được xếp trong kế hoạch; kết quả không đồng nghĩa đã đủ điều kiện tốt nghiệp.")
    if not semesters:
        warnings.append("Kế hoạch chưa có học kỳ nào.")
    return {"valid": not errors, "errors": list(dict.fromkeys(errors)), "warnings": list(dict.fromkeys(warnings)), "semesters": result_semesters, "summary": summary}


def _path(user_dir: Path) -> Path:
    return user_dir / "study_plans.json"


def list_plans(user_dir: Path) -> list:
    path = _path(user_dir)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_plan(user_dir: Path, plan: dict) -> dict:
    plans = list_plans(user_dir)
    now = datetime.now(timezone.utc).isoformat()
    plan_id = str(plan.get("id") or uuid4().hex)
    clean = {
        "id": plan_id,
        "name": str(plan.get("name") or "Kế hoạch mới").strip()[:100] or "Kế hoạch mới",
        "semesters": plan.get("semesters", []) if isinstance(plan.get("semesters", []), list) else [],
        "updated_at": now,
    }
    existing = next((i for i, item in enumerate(plans) if item.get("id") == plan_id), None)
    if existing is None:
        plans.append(clean)
    else:
        plans[existing] = clean
    user_dir.mkdir(parents=True, exist_ok=True)
    path = _path(user_dir)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(plans, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return clean


def delete_plan(user_dir: Path, plan_id: str) -> bool:
    plans = list_plans(user_dir)
    filtered = [p for p in plans if p.get("id") != plan_id]
    if len(filtered) == len(plans):
        return False
    path = _path(user_dir)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(filtered, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return True

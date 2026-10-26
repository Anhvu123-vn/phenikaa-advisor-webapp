"""Kiểm tra điều kiện tốt nghiệp (ước tính) theo Điều 26–27 Quy chế đào tạo đại học
Trường Đại học Phenikaa (QĐ 1062/QĐ-ĐHP-ĐT, 20/07/2023).

Module này KHÔNG tự tính lại tín chỉ hay GPA: nó dùng lại các hàm của ``advisor``
để số liệu luôn khớp với phần Cố vấn. Kết quả chỉ là ước tính từ dữ liệu QLĐT,
không phải xét duyệt chính thức của nhà trường.

Trạng thái mỗi điều kiện:
  pass     đã đạt
  fail     chưa đạt
  pending  chưa đạt hiện tại nhưng sẽ đạt nếu các môn đang học đều qua
  unknown  không có dữ liệu, cần sinh viên tự xác nhận
"""
from __future__ import annotations

import advisor

GPA_MIN = 2.0  # Điều 26.1c
EPS = 1e-9

# Điều kiện sinh viên phải tự khai vì QLĐT không có dữ liệu.
SELF_DECLARED = {
    "khong_ky_luat_dinh_chi": ("Không bị truy cứu trách nhiệm hình sự / không đang bị đình chỉ học tập", "Điều 26.1a", True),
    "ngoai_ngu": ("Chuẩn đầu ra ngoại ngữ", "Điều 26.1d", True),
    "tin_hoc": ("Chuẩn đầu ra tin học", "Điều 26.1d", True),
    "nghia_vu_tai_chinh": ("Hoàn thành nghĩa vụ tài chính", "Điều 26.1e", True),
    "don_xet_tot_nghiep": ("Đơn đề nghị xét tốt nghiệp (chỉ cần khi tốt nghiệp sớm/muộn so với thiết kế)", "Điều 26.1f", False),
}


def _item(key, label, rule, status, detail="", source="auto", required=True, extra=None):
    item = {"key": key, "label": label, "rule": rule, "status": status,
            "detail": detail, "source": source, "required": required}
    if extra:
        item["extra"] = extra
    return item


def _fmt(x):
    return f"{x:g}"


def _check_credits(data):
    """Điều 26.1b: tích lũy đủ tín chỉ theo từng khối (trừ khối điều kiện F)."""
    needs, info = advisor._block_needs(data)
    if not needs:
        return _item("tin_chi", "Tích lũy đủ tín chỉ theo chương trình", "Điều 26.1b", "unknown",
                     "Không có dữ liệu khối kiến thức trong roadmap.")
    short = {k: v for k, v in needs.items() if v > EPS}
    if not short:
        return _item("tin_chi", "Tích lũy đủ tín chỉ theo chương trình", "Điều 26.1b", "pass",
                     "Mọi khối kiến thức đã đủ tín chỉ.")
    # Nếu các môn đang học đủ lấp phần thiếu thì coi là "pending".
    after = dict(needs)
    courses = data.get("mon_hoc", []) or []
    current = [c for c in courses if c.get("trang_thai") == "DANG_KY_KY_NAY"]
    advisor._apply_to_blocks(after, current)
    still_short = {k: v for k, v in after.items() if v > EPS}
    total_short = sum(short.values())

    def lines(d):
        return "; ".join(f"{info[k]['ten']} ({k}): còn {_fmt(v)} TC" for k, v in d.items())

    order = {"CAN_HOC_LAI": 0, "DANG_KY_KY_NAY": 1, "CO_THE_DANG_KY": 2, "BI_KHOA": 3}

    def block_courses(code):
        rows = [c for c in courses if c.get("khoi_kt") == code and c.get("trang_thai") in order]
        rows.sort(key=lambda c: (order[c["trang_thai"]], str(c.get("ten_hp") or "")))
        return [{"ten_hp": c.get("ten_hp") or "Học phần chưa rõ tên", "so_tin": c.get("so_tin"),
                 "trang_thai": c.get("trang_thai")} for c in rows]

    def blocks(d):
        return [{"ma": k, "ten": info[k]["ten"], "con": round(v, 2),
                 "tu_chon": info[k].get("tu_chon", False), "courses": block_courses(k)}
                for k, v in d.items()]

    if not still_short:
        return _item("tin_chi", "Tích lũy đủ tín chỉ theo chương trình", "Điều 26.1b", "pending",
                     f"Còn thiếu {_fmt(total_short)} TC, sẽ đủ nếu các môn đang học đều đạt. {lines(short)}",
                     extra={"total": round(total_short, 2), "blocks": blocks(short)})
    return _item("tin_chi", "Tích lũy đủ tín chỉ theo chương trình", "Điều 26.1b", "fail",
                 f"Còn thiếu {_fmt(sum(still_short.values()))} TC sau khi tính môn đang học. {lines(still_short)}",
                 extra={"total": round(sum(still_short.values()), 2), "blocks": blocks(still_short)})


def _check_failed_courses(data):
    """Điều 26.1b: không còn học phần bắt buộc chưa đạt (cần học lại)."""
    failed = [c for c in (data.get("mon_hoc") or []) if c.get("trang_thai") == "CAN_HOC_LAI"]
    if not failed:
        return _item("hoc_lai", "Không còn học phần bắt buộc phải học lại", "Điều 26.1b", "pass")
    def name(c):
        return str(c.get("ten_hp") or "Học phần chưa rõ tên")

    names = ", ".join(name(c) for c in failed[:10])
    return _item("hoc_lai", "Không còn học phần bắt buộc phải học lại", "Điều 26.1b", "fail",
                 f"{len(failed)} học phần cần học lại: {names}",
                 extra={"courses": [{"ten_hp": name(c), "so_tin": c.get("so_tin")} for c in failed]})


def _check_gpa(data):
    """Điều 26.1c: GPA tích lũy toàn khóa từ 2,00 (thang 4)."""
    gpa = (data.get("gpa") or {}).get("gpa_4")
    try:
        gpa = float(gpa) if gpa is not None else None
    except (TypeError, ValueError):
        gpa = None
    if gpa is None:
        return _item("gpa", "GPA tích lũy từ 2,00 trở lên", "Điều 26.1c", "unknown", "Chưa có GPA.")
    status = "pass" if gpa >= GPA_MIN - EPS else "fail"
    return _item("gpa", "GPA tích lũy từ 2,00 trở lên", "Điều 26.1c", status, f"GPA hiện tại: {gpa:.2f}")


OVERRIDABLE = ("gdqp", "gdtc")  # tự xác nhận được khi QLĐT không đủ dữ liệu


def _check_condition_blocks(data, declared=None):
    """Điều 26.1d: chứng chỉ GDQP-AN và GDTC, lấy từ các khối F trong bảng điểm."""
    items = []
    declared = declared if isinstance(declared, dict) else {}
    f_blocks = {k: g for k, g in (data.get("khoi_summary") or {}).items() if advisor._is_f_block(k)}
    groups = {
        "gdqp": ("Giáo dục quốc phòng - An ninh", lambda k, g: k.upper().startswith("F1")),
        "gdtc": ("Giáo dục thể chất", lambda k, g: k.upper().startswith("F2")),
    }
    for key, (label, match) in groups.items():
        picked = {k: g for k, g in f_blocks.items() if match(k, g)}
        required = sum(advisor._num(g.get("tin_bat_buoc", g.get("tong_tin", 0))) for g in picked.values())
        done = sum(advisor._num(g.get("da_tich_luy", 0)) for g in picked.values())
        no_data = (not picked or required <= 0
                   or all(g.get("da_tich_luy") is None for g in picked.values()))
        if no_data:
            value = declared.get(key)
            if value is True:
                items.append(_item(key, f"Hoàn thành {label}", "Điều 26.1d", "pass",
                                   "Sinh viên tự xác nhận.", source="self"))
            elif value is False:
                items.append(_item(key, f"Hoàn thành {label}", "Điều 26.1d", "fail",
                                   "Sinh viên cho biết chưa hoàn thành.", source="self"))
            else:
                items.append(_item(key, f"Hoàn thành {label}", "Điều 26.1d", "unknown",
                                   "QLĐT không ghi số tín chỉ đã tích lũy của khối này, hãy tự xác nhận.",
                                   source="self"))
        elif done + EPS >= required:
            items.append(_item(key, f"Hoàn thành {label}", "Điều 26.1d", "pass",
                               f"Đã tích lũy {_fmt(done)}/{_fmt(required)} TC."))
        else:
            items.append(_item(key, f"Hoàn thành {label}", "Điều 26.1d", "fail",
                               f"Mới tích lũy {_fmt(done)}/{_fmt(required)} TC."))
    return items


def _check_self_declared(self_declared):
    items = []
    declared = self_declared if isinstance(self_declared, dict) else {}
    for key, (label, rule, required) in SELF_DECLARED.items():
        value = declared.get(key)
        if value is True:
            status, detail = "pass", "Sinh viên tự xác nhận."
        elif value is False:
            status, detail = "fail", "Sinh viên cho biết chưa đáp ứng."
        else:
            status, detail = "unknown", "Cần sinh viên tự xác nhận (QLĐT không có dữ liệu)."
        items.append(_item(key, label, rule, status, detail, source="self", required=required))
    return items


def _rank_info(data):
    """Điều 27.6–27.7: hạng tốt nghiệp dự kiến theo GPA hiện tại, có xét hạ bậc do học lại > 5%."""
    gpa = (data.get("gpa") or {}).get("gpa_4")
    try:
        gpa = float(gpa) if gpa is not None else None
    except (TypeError, ValueError):
        gpa = None
    info = {"gpa": gpa, "classification": advisor.classify(gpa), "retake_over_limit": None,
            "final_rank": advisor.classify(gpa),
            "note": "Hạng dự kiến theo GPA hiện tại; hạng thật phụ thuộc điểm các môn còn lại."}
    try:
        retake = advisor.analyze(data)["retake"]
        over = bool(retake.get("over_limit"))
        info["retake_over_limit"] = over
        info["retake_credits"] = retake.get("detected_credits")
        info["retake_threshold_credits"] = retake.get("threshold_credits")
        if over and info["classification"] in ("Xuất sắc", "Giỏi"):
            info["final_rank"] = "Giỏi" if info["classification"] == "Xuất sắc" else "Khá"
            info["note"] += " Đã hạ một bậc vì khối lượng học lại vượt 5% (Điều 27.7)."
    except Exception:  # dữ liệu thiếu thì bỏ qua phần hạ bậc, không làm hỏng cả kiểm tra
        pass
    return info


def check_graduation(roadmap: dict, self_declared: dict | None = None) -> dict:
    """Gom tất cả điều kiện của Điều 26.1 thành một danh sách và một kết luận."""
    data = roadmap or {}
    checks = [_check_credits(data), _check_failed_courses(data), _check_gpa(data)]
    checks += _check_condition_blocks(data, self_declared)
    checks += _check_self_declared(self_declared)

    required = [c for c in checks if c["required"]]
    counts = {s: sum(1 for c in required if c["status"] == s) for s in ("pass", "fail", "pending", "unknown")}
    if counts["fail"]:
        verdict, message = "chua_du", "Chưa đủ điều kiện tốt nghiệp theo dữ liệu hiện có."
    elif counts["pending"] or counts["unknown"]:
        verdict, message = "cho_xac_nhan", "Chưa thể kết luận: còn điều kiện phụ thuộc môn đang học hoặc cần bạn tự xác nhận."
    else:
        verdict, message = "du_dieu_kien", "Đáp ứng các điều kiện kiểm tra được (ước tính)."

    return {
        "verdict": verdict,
        "message": message,
        "counts": counts,
        "checks": checks,
        "rank": _rank_info(data),
        "disclaimer": "Kết quả là ước tính từ dữ liệu QLĐT, không phải xét duyệt chính thức. "
                      "Điều kiện tốt nghiệp thật do Phòng Đào tạo và Hội đồng xét tốt nghiệp quyết định.",
    }

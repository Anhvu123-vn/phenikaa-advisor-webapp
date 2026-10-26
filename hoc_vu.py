"""hoc_vu.py
-----------------
Đọc bảng điểm THEO HỌC KỲ của trang "Tra cứu điểm" (khung #zone_bangdiem, có cột
"Lần học") và tính các chỉ số học vụ theo Quy chế đào tạo trình độ đại học của
Trường ĐH Phenikaa (QĐ 1062/QĐ-ĐHP-ĐT, 20/07/2023).

Căn cứ điều khoản:
  * Điều 20      : công thức điểm trung bình (thang 4, làm tròn 2 chữ số).
  * Điều 19.2.b  : học cải thiện — điểm học phần là điểm CAO NHẤT các lần học.
  * Điều 21.1    : cảnh báo học tập (GPA kỳ < 0,8 kỳ đầu / < 1,0 các kỳ sau;
                   GPA tích lũy < 1,2 / 1,4 / 1,6 / 1,8 theo trình độ năm;
                   nợ đọng > 24 tín chỉ; không đăng ký học).
  * Điều 21.2.a  : buộc thôi học nếu cảnh báo quá 2 lần liên tiếp.
  * Điều 27.7    : hạng Giỏi/Xuất sắc bị giảm một mức nếu khối lượng học phần
                   phải đăng ký học lại vượt 5% tổng tín chỉ chương trình.

Hàm thuần, không phụ thuộc Flask; chỉ cần BeautifulSoup.
"""

import re
from pathlib import Path

from bs4 import BeautifulSoup

RETAKE_LIMIT_RATIO = 0.05          # Điều 27.7
DEBT_LIMIT_CREDITS = 24            # Điều 21.1.c
SEM_GPA_FIRST, SEM_GPA_NEXT = 0.8, 1.0          # Điều 21.1.a
CUM_GPA_BY_YEAR = (1.2, 1.4, 1.6, 1.8)          # Điều 21.1.b (năm 1, 2, 3, 4+)
PROGRAM_YEARS = 4   # GIẢ ĐỊNH: M = tổng tín chỉ chương trình / 4 (xếp trình độ năm)

HEADER_MAP = {
    "mã học phần": "ma_hp", "tên học phần": "ten_hp", "số tín chỉ": "so_tin",
    "lần học": "lan_hoc", "lần thi": "lan_thi", "điểm hệ 10": "diem_10",
    "điểm hệ 4": "diem_4", "điểm chữ": "diem_chu", "đánh giá": "danh_gia",
    "ghi chú": "ghi_chu",
}


def _num(text):
    try:
        return float(str(text).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def _cell_text(cell):
    """Ô trên giao diện có nhãn mobile <em class='show-in-mobi'> — bỏ nhãn đi."""
    for em in cell.find_all("em", class_="show-in-mobi"):
        em.decompose()
    return cell.get_text(" ", strip=True)


def _label(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _is_fail(row):
    return (row.get("diem_chu") or "").strip().upper() == "F" or \
        (row.get("danh_gia") or "").strip().lower() in ("học lại", "không đạt")


def _is_pass(row):
    return (row.get("danh_gia") or "").strip().lower() == "đạt"


def parse_bang_diem_hoc_ky(path):
    """Trả về list các lần học: mỗi phần tử là 1 dòng điểm của 1 học kỳ.

    Danh sách rỗng nếu trang chưa có khung #zone_bangdiem (crawl cũ)."""
    html = Path(path).read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html, "lxml")
    zone = soup.find(id="zone_bangdiem")
    if zone is None:
        return []
    attempts = []
    for panel in zone.find_all(id=re.compile(r"^hocky_\d{4}_\d{4}_\d+$")):
        m = re.match(r"hocky_(\d{4})_(\d{4})_(\d+)$", panel["id"])
        nam_bd, ky = int(m.group(1)), int(m.group(3))
        table = panel.find("table")
        if table is None:
            continue
        headers = [HEADER_MAP.get(_label(th.get_text(" ", strip=True)))
                   for th in table.find_all("tr")[0].find_all(["th", "td"])]
        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all(["th", "td"])
            if len(cells) != len(headers):
                continue
            row = {}
            for key, cell in zip(headers, cells):
                if key:
                    row[key] = _cell_text(cell)
            if not row.get("ma_hp"):
                continue
            attempts.append({
                "nam_hoc": f"{m.group(1)}-{m.group(2)}",
                "ky": ky,
                "thu_tu": nam_bd * 10 + ky,
                "ma_hp": row["ma_hp"].strip(),
                "ten_hp": row.get("ten_hp", "").strip(),
                "so_tin": _num(row.get("so_tin")) or 0.0,
                "lan_hoc": int(_num(row.get("lan_hoc")) or 1),
                "lan_thi": int(_num(row.get("lan_thi")) or 1),
                "diem_10": _num(row.get("diem_10")),
                "diem_4": _num(row.get("diem_4")),          # None với môn P/NP
                "diem_chu": (row.get("diem_chu") or "").strip(),
                "danh_gia": (row.get("danh_gia") or "").strip(),
            })
    attempts.sort(key=lambda a: a["thu_tu"])
    return attempts


def _gpa(rows):
    credits = sum(r["so_tin"] for r in rows)
    if credits <= 0:
        return None, 0.0
    return sum(r["diem_4"] * r["so_tin"] for r in rows) / credits, credits


def _year_level(accumulated, total_credits):
    """Điều 20.4: N < M năm 1; M ≤ N < 2M năm 2, ... (M = tổng TC / số năm)."""
    m = (total_credits or 0) / PROGRAM_YEARS
    if m <= 0:
        return 1
    return min(int(accumulated // m) + 1, 5)


def phan_tich(attempts, tin_chi_toan_khoa):
    """Tính lịch sử từng kỳ, số tín chỉ học lại và cảnh báo học vụ."""
    if not attempts:
        return {"co_du_lieu": False}

    semesters, order = {}, []
    for a in attempts:
        if a["thu_tu"] not in semesters:
            semesters[a["thu_tu"]] = []
            order.append(a["thu_tu"])
        semesters[a["thu_tu"]].append(a)

    best = {}            # mã HP -> lần học có điểm cao nhất (Điều 19.2.b)
    passed = {}          # mã HP -> số tín (đã đạt, kể cả P)
    failed_ever = {}     # mã HP -> {"so_tin", "lan_truot"}
    out_sem, warn_streak, max_streak, warned_semesters = [], 0, 0, []
    first = True

    for key in order:
        rows = semesters[key]
        graded = [r for r in rows if r["diem_4"] is not None]
        gpa_ky, tin_gpa_ky = _gpa(graded)
        for r in rows:
            if _is_fail(r):
                f = failed_ever.setdefault(r["ma_hp"], {"so_tin": r["so_tin"], "lan_truot": 0})
                f["lan_truot"] += 1
            if _is_pass(r):
                passed[r["ma_hp"]] = r["so_tin"]
            if r["diem_4"] is not None:
                cur = best.get(r["ma_hp"])
                if cur is None or r["diem_4"] > cur["diem_4"]:
                    best[r["ma_hp"]] = r
        cum_gpa, cum_credits = _gpa([b for b in best.values() if b["diem_4"] is not None])
        # Khớp QLĐT: chỉ tính môn có điểm hệ 4 (môn P/NP như GDTC không vào 103 TC).
        tich_luy = sum(t for ma, t in passed.items() if ma in best)
        no_dong = sum(f["so_tin"] for ma, f in failed_ever.items() if ma not in passed)
        level = _year_level(tich_luy, tin_chi_toan_khoa)

        reasons = []
        sem_limit = SEM_GPA_FIRST if first else SEM_GPA_NEXT
        if gpa_ky is not None and round(gpa_ky, 2) < sem_limit:
            reasons.append(f"GPA kỳ {gpa_ky:.2f} < {sem_limit:.1f}")
        cum_limit = CUM_GPA_BY_YEAR[min(level, 4) - 1]
        if cum_gpa is not None and round(cum_gpa, 2) < cum_limit:
            reasons.append(f"GPA tích lũy {cum_gpa:.2f} < {cum_limit:.1f} (năm {level})")
        if no_dong > DEBT_LIMIT_CREDITS:
            reasons.append(f"nợ đọng {no_dong:g} TC > {DEBT_LIMIT_CREDITS}")
        warn_streak = warn_streak + 1 if reasons else 0
        max_streak = max(max_streak, warn_streak)
        if reasons:
            warned_semesters.append({"hoc_ky": f"{rows[0]['nam_hoc']} HK{rows[0]['ky']}", "ly_do": reasons})
        first = False

        out_sem.append({
            "nam_hoc": rows[0]["nam_hoc"], "ky": rows[0]["ky"],
            "nhan": f"{rows[0]['nam_hoc']} · HK{rows[0]['ky']}",
            "so_mon": len(rows), "tin_chi_dang_ky": sum(r["so_tin"] for r in rows),
            "tin_chi_dat": sum(r["so_tin"] for r in rows if _is_pass(r)),
            "gpa_ky": round(gpa_ky, 2) if gpa_ky is not None else None,
            "gpa_tich_luy": round(cum_gpa, 2) if cum_gpa is not None else None,
            "tin_chi_tich_luy": tich_luy, "no_dong": no_dong,
            "trinh_do_nam": level, "bi_canh_bao": bool(reasons),
            "mon_truot": [r["ma_hp"] for r in rows if _is_fail(r)],
            # Chi tiết từng môn của kỳ (hiện khi bấm vào kỳ ở "Tiến trình hiện tại").
            "mon": [{
                "ma_hp": r["ma_hp"], "ten_hp": r["ten_hp"], "so_tin": r["so_tin"],
                "lan_hoc": r["lan_hoc"], "diem_10": r["diem_10"], "diem_4": r["diem_4"],
                "diem_chu": r["diem_chu"], "danh_gia": r["danh_gia"],
                "truot": bool(_is_fail(r)),
            } for r in rows],
        })

    # ---- học lại: môn từng bị F ở bất kỳ lần học nào (Điều 27.7, đọc theo nghĩa
    # "học phần phải đăng ký học lại"). Cần xác nhận với Phòng Đào tạo.
    names = {a["ma_hp"]: a["ten_hp"] for a in attempts}
    retake_courses = [{
        "ma_hp": ma, "ten_hp": names.get(ma, ma), "so_tin": f["so_tin"],
        "so_lan_truot": f["lan_truot"], "da_dat_lai": ma in passed,
    } for ma, f in failed_ever.items()]
    retake_credits = sum(c["so_tin"] for c in retake_courses)
    limit = (tin_chi_toan_khoa or 0) * RETAKE_LIMIT_RATIO
    still_owed = sum(c["so_tin"] for c in retake_courses if not c["da_dat_lai"])

    last = out_sem[-1]
    gpa_min = min((s for s in out_sem if s["gpa_ky"] is not None), key=lambda s: s["gpa_ky"])
    cum_min = min((s for s in out_sem if s["gpa_tich_luy"] is not None), key=lambda s: s["gpa_tich_luy"])
    return {
        "co_du_lieu": True,
        "hoc_ky": out_sem,
        "tong_hop": {
            "gpa_tich_luy": last["gpa_tich_luy"],
            "tin_chi_tich_luy": last["tin_chi_tich_luy"],
            "tin_chi_tinh_gpa": round(_gpa([b for b in best.values() if b["diem_4"] is not None])[1], 1),
        },
        "hoc_lai": {
            "tin_chi": retake_credits,
            "nguong_tin_chi": round(limit, 2),
            "ty_le_nguong": RETAKE_LIMIT_RATIO,
            "vuot_nguong": retake_credits > limit + 1e-9,
            "con_no_tin_chi": still_owed,
            "mon": sorted(retake_courses, key=lambda c: c["ten_hp"]),
        },
        "canh_bao": {
            "da_bi_canh_bao": bool(warned_semesters),
            "cac_ky_bi_canh_bao": warned_semesters,
            "so_lan_lien_tiep_toi_da": max_streak,
            "so_lan_lien_tiep_hien_tai": warn_streak,
            "gpa_ky_thap_nhat": {"nhan": gpa_min["nhan"], "gpa": gpa_min["gpa_ky"]},
            "gpa_tich_luy_thap_nhat": {"nhan": cum_min["nhan"], "gpa": cum_min["gpa_tich_luy"]},
            "no_dong_hien_tai": last["no_dong"],
            "gia_dinh": f"Trình độ năm tính theo M = tổng tín chỉ / {PROGRAM_YEARS} (chương trình {PROGRAM_YEARS} năm).",
        },
    }

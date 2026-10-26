"""
build_roadmap.py
------------------
Đối chiếu "Chương trình học" (danh sách môn + tiên quyết theo khung) với
"Tra cứu điểm" (kết quả học tập thật của bạn) để dựng lộ trình học:

  - HOAN_THANH : đã học và đạt
  - CO_THE_DANG_KY : chưa học, nhưng đã đủ hết môn tiên quyết -> đăng ký được
  - BI_KHOA : chưa học, còn thiếu ít nhất 1 môn tiên quyết -> chưa đăng ký được

Input: 3 file HTML export thẳng từ cổng sinh viên
  - trang "Chương trình học"          (bảng #tblHocPhan)
  - trang "Tra cứu điểm"              (bảng #tblTongHopDiemHP)
  - trang "Tra cứu kết quả đăng ký"   (các thẻ .subject-item, không bắt buộc)

Output: roadmap.json (dữ liệu đầy đủ để build UI) + in báo cáo ra màn hình.

Cài đặt: pip install pandas lxml beautifulsoup4
Chạy:    python build_roadmap.py "02_Chương trình học.html" "01_Tra cứu điểm.html" "08_Tra cứu kết quả đăng ký.html"
"""

import json
import math
import re
import sys
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

import hoc_vu


def normalize(text) -> str:
    """Chuẩn hoá tên môn để so khớp: bỏ dấu *, khoảng trắng thừa, hoa/thường,
    và thay dấu tiếng Việt bằng chữ không dấu (để 'tiếng anh' == 'tieng anh').
    
    Hỗ trợ cả text dạng str hoặc float (NaN, None)."""
    if not isinstance(text, str):
        return ""
    text = text.replace("*", "").strip()
    text = re.sub(r"\s+", " ", text)
    # Thay dấu tiếng Việt bằng chữ không dấu
    import unicodedata
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text.casefold()


def parse_prereqs(raw: str):
    """
    '  ' -> []
    'Học trước(Tiếng anh cơ bản 1)' -> [('Học trước', 'Tiếng anh cơ bản 1')]
    'Tiên quyết(Đại số tuyến tính,Giải tích 2)'
        -> [('Tiên quyết','Đại số tuyến tính'), ('Tiên quyết','Giải tích 2')]
    'Học trước(Toán rời rạc), Tiên quyết(Cấu trúc dữ liệu và thuật toán)'
        -> cả hai
    """
    if not isinstance(raw, str) or not raw.strip():
        return []
    out = []
    for kind, names in re.findall(r"(Học trước|Tiên quyết)\(([^)]+)\)", raw):
        for name in names.split(","):
            name = name.strip()
            if name:
                out.append((kind, name))
    return out


def load_registered_courses(path: str) -> set:
    """Đọc trang 'Tra cứu kết quả đăng ký' (dạng thẻ/card, không phải bảng
    <table>) để biết môn nào ĐANG ĐĂNG KÝ ở kỳ hiện tại — tránh gợi ý lại
    môn bạn vừa đăng ký nhưng chưa có điểm (nên chưa được tính là hoàn thành).
    Trả về tập hợp TÊN MÔN đã chuẩn hoá (chưa map ra mã, việc đó làm ở
    build_roadmap vì cần name_to_code)."""
    if not path:
        return set()
    html = Path(path).read_text(encoding="utf-8", errors="ignore")
    names = set()
    for m in re.finditer(r'<div class="subject-item">.*?<h4>Môn ([^<]+)</h4>', html, re.DOTALL):
        names.add(normalize(m.group(1)))
    return names


def load_curriculum(path: str) -> pd.DataFrame:
    tables = pd.read_html(path, attrs={"id": "tblHocPhan"}, encoding="utf-8", flavor="lxml")
    df = tables[0]
    df = df.rename(columns={
        "Mã học phần": "ma_hp",
        "Tên học phần": "ten_hp",
        "Khối KT": "khoi_kt",
        "Số tín học phần": "so_tin",
        "Điều kiện ràng buộc": "dieu_kien_raw",
        "Học kỳ dự kiến": "hoc_ky_du_kien",
        "Học kỳ thực tế": "hoc_ky_thuc_te",
    })
    df["ma_hp"] = df["ma_hp"].astype(str).str.strip()
    df["ten_hp"] = df["ten_hp"].astype(str).str.strip()

    # Phát hiện trang "Chương trình học" bị render không đầy đủ (chỉ có
    # header, không có dòng dữ liệu) — đây là triệu chứng đã quan sát: bảng
    # #tblHocPhan là SPA, chỉ render body SAU khi dropdown #dropSearch_ChuongTrinh
    # được chọn. Nếu crawler lưu HTML trước khi bảng kịp load, mọi mã/tên môn
    # sẽ rỗng -> `build_roadmap()` sẽ sinh ra roadmap.json với mon_hoc=[],
    # khiến dashboard hiển thị "Chưa có dữ liệu" dù file JSON tồn tại.
    # Fail-fast ở đây để caller biết CẦN crawl lại, thay vì âm thầm ghi file rỗng.
    if len(df) == 0:
        raise ValueError(
            "Bảng #tblHocPhan trong file 'Chương trình học' không có dòng "
            "dữ liệu nào (chỉ có header). Có thể dropdown 'Chọn chương trình' "
            "trên QLDT chưa được chọn lúc crawler lưu HTML. Hãy crawl lại."
        )

    return df


def load_tong_tin_chi(path: str) -> float | None:
    """Đọc tổng tín chỉ cần tích lũy để tốt nghiệp từ thẻ
    ``<span id="iTongSoTinChi">...</span>`` trên trang 'Chương trình học'.

    Con số này là do nhà trường/ngành đặt (tổng TC các khối bắt buộc + tự chọn
    tối thiểu để ra trường) — khác với ``sum(so_tin)`` của tất cả môn (bao gồm
    cả môn thay thế/không bắt buộc, thường cao hơn).

    Trả về ``None`` nếu không tìm thấy span (vd file HTML cũ, hoặc trang đã
    đổi cấu trúc) — caller tự fallback sang cộng ``so_tin``.
    """
    try:
        with open(path, encoding="utf-8") as f:
            html = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    soup = BeautifulSoup(html, "html.parser")
    span = soup.find("span", id="iTongSoTinChi")
    if span is None:
        return None
    try:
        return float(span.get_text(strip=True))
    except (TypeError, ValueError):
        return None


def load_transcript(path: str) -> pd.DataFrame:
    tables = pd.read_html(path, attrs={"id": "tblTongHopDiemHP"}, encoding="utf-8", flavor="lxml")
    df = tables[0]
    df = df.rename(columns={
        "Mã học phần": "ma_hp",
        "Mã khối": "khoi_ma",
        "Tên khối": "khoi_ten",
        "Số tín chỉ": "so_tin",
        "Điểm": "diem",
        "Điểm quy đổi": "diem_quy_doi",
        "Điểm chữ": "diem_chu",
        "Đánh giá": "danh_gia",
        "Kết quả": "ket_qua",
    })
    df["ma_hp"] = df["ma_hp"].astype(str).str.strip()
    keep_cols = ["ma_hp", "khoi_ma", "khoi_ten", "so_tin", "diem",
                 "diem_quy_doi", "diem_chu", "danh_gia"]
    if "ket_qua" in df.columns:
        keep_cols.append("ket_qua")
    return df[keep_cols]


def load_khoi_summary(path: str) -> dict:
    """Đọc bảng tổng hợp theo khối kiến thức (id='tblTongHopDiem') — cho biết
    khối nào phải học HẾT (Tổng số tín bắt buộc == Tổng số tín chỉ) và khối
    nào chỉ cần ĐẠT ĐỦ một số tín nhất định trong nhiều lựa chọn (tự chọn:
    Tổng số tín bắt buộc < Tổng số tín chỉ). Ví dụ điển hình: khối "Giáo dục
    thể chất" có nhiều môn để chọn nhưng chỉ cần đạt 1 tín chỉ.

    Trả về: {ma_khoi: {"tong_tin": ..., "tin_bat_buoc": ..., "da_tich_luy": ...}}
    """
    try:
        tables = pd.read_html(path, attrs={"id": "tblTongHopDiem"}, encoding="utf-8", flavor="lxml")
    except ValueError:
        return {}
    df = tables[0]
    df = df.rename(columns={
        "Mã khối": "ma_khoi",
        "Tên khối": "ten_khoi",
        "Tổng số tín chỉ": "tong_tin",
        "Tổng số tín bắt buộc": "tin_bat_buoc",
        "Tổng số tín đã tích lũy": "da_tich_luy",
    })
    result = {}
    for r in df.itertuples():
        ma = str(getattr(r, "ma_khoi", "")).strip()
        if not ma or ma.lower() == "nan" or ma.strip().lower() in ("tổng", "tong"):
            continue
        try:
            tong_tin = float(getattr(r, "tong_tin"))
            tin_bat_buoc = float(getattr(r, "tin_bat_buoc"))
            da_tich_luy = float(getattr(r, "da_tich_luy"))
        except (TypeError, ValueError):
            continue
        result[ma] = {
            "ten_khoi": getattr(r, "ten_khoi", ma),
            "tong_tin": tong_tin,
            "tin_bat_buoc": tin_bat_buoc,
            "da_tich_luy": da_tich_luy,
            "la_tu_chon": tin_bat_buoc < tong_tin,  # True = khối tự chọn, không cần học hết
            "da_du_yeu_cau": da_tich_luy >= tin_bat_buoc,
        }
    return result


def chuan_hoa_khoi_summary(khoi_summary: dict) -> dict:
    """Server 'Tra cứu điểm' trả về tin_bat_buoc=0 cho khối thể chất (đáng lẽ
    mỗi khối F2.x phải tích lũy 1 TC) — và có mấy khối 'tích lũy X TC' khác.

    Quy ước sửa lại:
      - Khối có TÊN chứa '(Tích lũy N TC)' (N là số, mặc định 1) → tin_bat_buoc = N
      - Khối F1 (Giáo dục quốc phòng - an ninh) → bỏ qua, không phân tích
        (đánh dấu bằng cách set ten_khoi có prefix '[BO_QUA]')
    """
    import re as _re
    out = {}
    for ma, k in khoi_summary.items():
        k = dict(k)   # copy
        ten = (k.get("ten_khoi") or "").strip()
        # Bỏ qua F1 quốc phòng
        if ma.upper().startswith("F1") or "quốc phòng" in ten.lower() or "quoc phong" in ten.lower():
            k["bo_qua"] = True
            out[ma] = k
            continue
        # Tên có "Tích lũy N TC" → sửa BB
        m = _re.search(r"[Tt]ích\s+lũy\s+(\d+)\s*TC", ten)
        if m:
            k["tin_bat_buoc"] = float(m.group(1))
        out[ma] = k
    return out


def compute_gpa(trans: pd.DataFrame) -> dict:
    """
    Tính GPA hệ 4 và hệ 10, chỉ tính trên các học phần ĐÃ CÓ ĐIỂM CHỮ dạng
    xếp loại (A/B+/C.../D) — loại các môn Đạt/Không đạt kiểu P/F (thường là
    Giáo dục thể chất) vì các trường ở VN không tính GDTC vào GPA. Nhận diện
    môn P/F kiểu này qua diem_quy_doi bị thiếu (trống) dù đã "Đạt".
    """
    graded = trans[trans["diem_quy_doi"].notna()].copy()
    graded = graded[graded["so_tin"].notna()]
    tin_gpa = graded["so_tin"].astype(float).sum()

    if tin_gpa > 0:
        gpa_4 = (graded["diem_quy_doi"].astype(float) *
                 graded["so_tin"].astype(float)).sum() / tin_gpa
    else:
        gpa_4 = None

    graded_10 = graded[graded["diem"].notna()]
    tin_gpa_10 = graded_10["so_tin"].astype(float).sum()
    if tin_gpa_10 > 0:
        gpa_10 = (graded_10["diem"].astype(float) *
                  graded_10["so_tin"].astype(float)).sum() / tin_gpa_10
    else:
        gpa_10 = None

    xep_loai = None
    if gpa_4 is not None:
        if gpa_4 >= 3.6:
            xep_loai = "Xuất sắc"
        elif gpa_4 >= 3.2:
            xep_loai = "Giỏi"
        elif gpa_4 >= 2.5:
            xep_loai = "Khá"
        elif gpa_4 >= 2.0:
            xep_loai = "Trung bình"
        else:
            xep_loai = "Yếu"

    return {
        "gpa_4": round(gpa_4, 2) if gpa_4 is not None else None,
        "gpa_10": round(gpa_10, 2) if gpa_10 is not None else None,
        "xep_loai": xep_loai,
        "so_tin_tinh_gpa": tin_gpa,
        "so_mon_tinh_gpa": len(graded),
    }


def make_current_filter(courses: list):
    """Bộ lọc DUY NHẤT loại các học phần đang học kỳ này (DANG_KY_KY_NAY) khỏi
    mọi gợi ý kỳ tới. Chặn theo mã học phần và theo tên đã chuẩn hóa (phòng
    dữ liệu có bản ghi trùng). Trả về hàm ``not_current(course) -> bool``.
    Dùng chung cho build_roadmap.py và server.py để không lệch logic.
    """
    current = [c for c in (courses or []) if c.get("trang_thai") == "DANG_KY_KY_NAY"]
    codes = {c.get("ma_hp") for c in current if c.get("ma_hp")}
    names = {normalize(c.get("ten_hp", "")) for c in current if c.get("ten_hp")}

    def not_current(c) -> bool:
        return (
            c.get("ma_hp") not in codes
            and normalize(c.get("ten_hp", "")) not in names
        )

    return not_current


def semester_sort_key(hoc_ky_du_kien: str):
    """'2026_2027_1(10)' -> 10 ; '2023_2024_1;..._3(1,2,3)' -> 1 (kỳ sớm nhất
    trong các kỳ được phép học). Không đọc được -> đẩy xuống cuối."""
    if not isinstance(hoc_ky_du_kien, str):
        return 999
    m = re.search(r"\(([\d,]+)\)\s*$", hoc_ky_du_kien.strip())
    if not m:
        return 999
    nums = [int(x) for x in m.group(1).split(",") if x.strip().isdigit()]
    return min(nums) if nums else 999


def goi_y_ky_toi_tu_planner(courses: list, khoi_summary: dict,
                            min_tc: float = 8.0, max_tc: float = 16.0) -> list:
    """Gợi ý kỳ tới = kỳ đầu tiên của planner dùng chung (planner.py), để CLI
    và web luôn cho cùng kết quả. Trả về dạng cũ (ma_hp, ten_hp, so_tin,
    hoc_ky_du_kien, mo_khoa_cho, uu_tien) cho print_summary_report."""
    from planner import build_academic_plan  # import muộn: planner import ngược module này

    pace = {"key": "custom", "label": "Tùy chỉnh",
            "min": min_tc, "max": max_tc, "target": max_tc}
    plan = build_academic_plan(
        {"mon_hoc": courses, "khoi_summary": khoi_summary}, pace=pace)
    first = plan["semesters"][0]["courses"] if plan["semesters"] else []

    unlock = {}
    for c in courses:
        if c.get("trang_thai") == "HOAN_THANH":
            continue
        for code in c.get("tien_quyet") or []:
            unlock[code] = unlock.get(code, 0) + 1

    out = []
    for c in first:
        item = {
            "ma_hp": c["ma_hp"], "ten_hp": c["ten_hp"], "so_tin": c["so_tin"],
            "hoc_ky_du_kien": c["hoc_ky_du_kien"],
            "mo_khoa_cho": unlock.get(c["ma_hp"], 0),
        }
        if c.get("trang_thai") == "CAN_HOC_LAI":
            item["uu_tien"] = "HOC_LAI"
        out.append(item)
    return out


def _sanitize_for_json(obj):
    """
    Thay mọi giá trị float NaN/Infinity bằng None (JSON hợp lệ là 'null'),
    đệ quy qua dict/list. Python's json.dumps mặc định CHO PHÉP xuất ra
    NaN/Infinity dạng token trần (không có dấu ngoặc kép) — đây KHÔNG phải
    JSON hợp lệ theo chuẩn, và JSON.parse() của trình duyệt sẽ báo lỗi kiểu
    "Unexpected token 'N'" (từ chữ N của NaN) khi gặp phải. Lỗi này thường
    đến từ pandas: các ô trống trong bảng HTML được đọc thành NaN thay vì
    chuỗi rỗng/None. Luôn gọi hàm này ngay trước khi ghi/serialize JSON.
    """
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    return obj


def build_roadmap(curriculum_path: str, transcript_path: str,
                   registered_path: str = None,
                   max_tin_chi_ky_toi: float = 16.0,
                   min_tin_chi_ky_toi: float = 8.0) -> dict:
    curr = load_curriculum(curriculum_path)
    trans = load_transcript(transcript_path)
    khoi_summary = load_khoi_summary(transcript_path)
    # Tổng "Tổng số tín bắt buộc" của bảng khối kiến thức (trang Tra cứu điểm,
    # mục Khối kiến thức) = số TC cần tích lũy để ra trường (vd 134).
    # PHẢI cộng từ dữ liệu GỐC, trước chuan_hoa_khoi_summary() vì hàm đó sửa
    # tin_bat_buoc của khối F2.x (thể chất) nên tổng sẽ lệch.
    tong_bat_buoc_khoi = sum(
        float(k.get("tin_bat_buoc") or 0) for k in khoi_summary.values()
    )
    khoi_summary = chuan_hoa_khoi_summary(khoi_summary)
    registered_names = load_registered_courses(registered_path) if registered_path else set()

    # Tổng tín chỉ cần tích lũy để tốt nghiệp: ưu tiên đọc từ HTML trang
    # 'Chương trình học' (do nhà trường/ngành đặt, khác nhau theo ngành).
    # Fallback về sum(so_tin) nếu HTML không có span iTongSoTinChi.
    tong_tin_chi_html = load_tong_tin_chi(curriculum_path)
    tin_total_courses = sum(float(r.so_tin) for r in curr.itertuples())

    # map "tên môn đã chuẩn hoá" -> mã học phần, để giải mã tên trong
    # "Điều kiện ràng buộc" (chỉ ghi tên, không ghi mã) thành mã học phần.
    name_to_code = {normalize(r.ten_hp): r.ma_hp for r in curr.itertuples()}

    # map mã học phần -> đã hoàn thành hay chưa (dựa trên bảng điểm thật).
    # Tiêu chí đúng đã kiểm chứng: cột "Đánh giá" = "Đạt" (không dùng cột
    # "Kết quả" vì giá trị thật của nó chưa được xác nhận, có thể mang ý
    # nghĩa khác, VD trạng thái đăng ký thay vì kết quả học).
    if "danh_gia" in trans.columns:
        completed_codes = set(trans.loc[trans["danh_gia"] == "Đạt", "ma_hp"])
        # Môn đã thi nhưng không đạt (trượt): có điểm chữ F, không phải "Đạt"
        # Có thể có dòng có điểm nhưng danh_gia = "Không đạt" hoặc điểm_chữ = "F"
        failed_codes = set(trans.loc[
            (trans["danh_gia"] != "Đạt") & (trans["danh_gia"].notna()),
            "ma_hp"
        ])
        # Bổ sung: bắt luôn điểm chữ F (an toàn)
        if "diem_chu" in trans.columns:
            failed_codes |= set(trans.loc[trans["diem_chu"] == "F", "ma_hp"])
    else:
        completed_codes = set(trans.loc[trans["ket_qua"] == "Hoàn thành", "ma_hp"])
        failed_codes = set(trans.loc[trans["ket_qua"] == "Không hoàn thành", "ma_hp"])

    grade_by_code = {
        r.ma_hp: {"diem": r.diem, "diem_chu": r.diem_chu,
                  "danh_gia": getattr(r, "danh_gia", None)}
        for r in trans.itertuples()
    }
    # tên đầy đủ của từng khối kiến thức (vd A1 -> "Khối kiến thức giáo dục đại cương")
    khoi_ten_map = {
        r.khoi_ma: r.khoi_ten for r in trans.itertuples() if pd.notna(r.khoi_ma)
    }

    # ---- ĐẾM LẠI TC THỰC TẾ THEO KHỐI TỪ BẢNG CHI TIẾT ----
    # Lý do: server (bảng tblTongHopDiem) thường trả `da_tich_luy = 0` cho
    # các khối có môn P/F (GDTC, Tiếng Anh bổ trợ) vì hệ thống không cộng
    # tín chỉ cho điểm chữ P. Bảng chi tiết tblTongHopDiemHP lại có đủ
    # thông tin môn đã ĐẠT → ta tự cộng lại cho chính xác.
    # Quy tắc: lấy MAX(server_da_tich_luy, tổng_tin_chỉ_của_môn_Đạt_trong_bảng_chi_tiết)
    if "danh_gia" in trans.columns:
        done_trans = trans[trans["danh_gia"] == "Đạt"]
    else:
        done_trans = trans[trans["ket_qua"] == "Hoàn thành"]
    khoi_da_tich_luy_thuc_te: dict = {}
    for r in done_trans.itertuples():
        if pd.isna(getattr(r, "khoi_ma", None)):
            continue
        ma = str(r.khoi_ma).strip()
        if not ma:
            continue
        try:
            tin = float(r.so_tin)
        except (TypeError, ValueError):
            tin = 0.0
        khoi_da_tich_luy_thuc_te[ma] = khoi_da_tich_luy_thuc_te.get(ma, 0.0) + tin

    # override vào khoi_summary
    for ma, tl in khoi_da_tich_luy_thuc_te.items():
        if ma in khoi_summary:
            cur = float(khoi_summary[ma].get("da_tich_luy") or 0)
            if tl > cur:
                khoi_summary[ma]["da_tich_luy"] = tl
                khoi_summary[ma]["da_du_yeu_cau"] = (
                    tl >= float(khoi_summary[ma].get("tin_bat_buoc") or 0)
                )
                khoi_summary[ma]["_nguon_tich_luy"] = "tblTongHopDiemHP"
        else:
            # Khối mới xuất hiện trong bảng chi tiết nhưng không có trong
            # bảng tổng hợp (hiếm gặp, vd F2.x cũ) → tạo entry tối thiểu
            khoi_summary[ma] = {
                "ten_khoi": khoi_ten_map.get(ma, ma),
                "tong_tin": tl,
                "tin_bat_buoc": tl,
                "da_tich_luy": tl,
                "la_tu_chon": False,
                "da_du_yeu_cau": True,
                "_nguon_tich_luy": "tblTongHopDiemHP",
            }

    courses = []
    unresolved_prereq_names = set()

    for r in curr.itertuples():
        khoi_info = khoi_summary.get(r.khoi_kt)
        khoi_ten = str(khoi_ten_map.get(r.khoi_kt, ""))
        # Bỏ qua Giáo dục quốc phòng - an ninh: nhận diện bằng tên khối HOẶC tên môn
        ten_hp_lower = str(r.ten_hp).lower()
        if ("giáo dục quốc phòng" in normalize(khoi_ten)
                or "giáo dục quốc phòng" in normalize(ten_hp_lower)
                or "quốc phòng" in ten_hp_lower):
            continue

        prereq_pairs = parse_prereqs(r.dieu_kien_raw)
        prereq_codes = []
        for kind, name in prereq_pairs:
            code = name_to_code.get(normalize(name))
            if code:
                prereq_codes.append(code)
            else:
                unresolved_prereq_names.add(name)

        # ===== ĐIỀU KIỆN MỞ KHÓA THÊM (F3) =====
        # Theo quy chế của trường: các môn Tiếng Anh (khối A1) chỉ bị KHÓA
        # bởi F3 khi TRONG A1 CHƯA CÓ môn Tiếng Anh nào được học.
        # Nếu A1 đã có điểm ít nhất 1 môn Tiếng Anh → tự hiểu đã qua F3
        # (F3 chỉ là điều kiện tạm thời khi chưa học môn TA nào trong A1),
        # các môn TA còn lại mở theo chuỗi prerequisite của chúng.
        ten_hp_norm = normalize(r.ten_hp)
        is_tieng_anh_A1 = (
            khoi_info is not None
            and r.khoi_kt == "A1"
            and ("tieng anh" in ten_hp_norm or "tiếng anh" in r.ten_hp.lower())
            and "bo tro" not in ten_hp_norm       # loại trừ chính nó
            and "nhap mon" not in ten_hp_norm    # không áp dụng "Nhập môn"
        )
        if is_tieng_anh_A1:
            # Tìm mã F3 (nhận diện bằng tên, không phụ thuộc mã cố định)
            f3_ta_bo_tro_ma = name_to_code.get(normalize("Tiếng Anh bổ trợ"))
            # Kiểm tra A1 đã có môn TA nào hoàn thành chưa
            # Gộp tất cả môn Tiếng Anh trong A1 (trừ F3), so với bảng điểm
            a1_tieng_anh_codes = [
                row.ma_hp for row in curr.itertuples()
                if row.khoi_kt == "A1"
                and "tieng anh" in normalize(str(row.ten_hp))
                and "bo tro" not in normalize(str(row.ten_hp))
            ]
            has_a1_ta_done = any(c in completed_codes for c in a1_tieng_anh_codes)
            # Chỉ thêm F3 làm điều kiện khi A1 chưa có môn TA nào hoàn thành
            if (f3_ta_bo_tro_ma
                    and not has_a1_ta_done
                    and f3_ta_bo_tro_ma not in prereq_codes
                    and f3_ta_bo_tro_ma not in completed_codes):
                prereq_codes.append(f3_ta_bo_tro_ma)

        is_done = r.ma_hp in completed_codes
        # ===== QUY TẮC ĐẶC BIỆT: Kiểm tra năng lực TA đầu khóa =====
        # Theo quy chế trường: nếu sinh viên đã hoàn thành môn "Tiếng Anh bổ trợ"
        # (F3 - FEL704000) thì bài "Kiểm tra năng lực tiếng Anh đầu khóa"
        # (DT00001) được coi là ĐÃ QUA — không cần làm nữa.
        # Lý do: bài test đầu khóa có 2 nhánh:
        #   - Qua → được miễn F3
        #   - Không qua → phải học F3 (Tiếng Anh bổ trợ)
        # Nếu đã học F3 → bài test coi như đã được thay thế bằng kết quả F3.
        ten_hp_lower = r.ten_hp.lower()
        is_kiem_tra_dau_khoa = (
            "kiểm tra năng lực" in ten_hp_lower
            and "tiếng anh" in ten_hp_lower
            and "đầu khóa" in ten_hp_lower
        )
        if is_kiem_tra_dau_khoa:
            f3_ta_bo_tro_ma = name_to_code.get(normalize("Tiếng Anh bổ trợ"))
            if f3_ta_bo_tro_ma and f3_ta_bo_tro_ma in completed_codes:
                # Đã học xong Tiếng Anh bổ trợ → bài kiểm tra đầu khóa coi
                # như đã qua (thay thế).
                is_done = True

        # ===== QUY TẮC ĐẶC BIỆT: Tiếng Anh bổ trợ (F3) =====
        # Theo quy chế: F3 chỉ áp dụng cho SV CHƯA có điểm môn Tiếng Anh nào
        # trong khối A1. Nếu SV đã có điểm các môn Tiếng Anh A1 (Anh cơ bản
        # 1-3, Anh nâng cao 1-3,...) → SV thuộc khóa cũ, đã "qua" F3 một
        # cách ngầm định → đánh dấu F3 là HOAN_THANH (tín chỉ được tích lũy,
        # không hiển thị "cần học" nữa).
        ten_hp_norm_f3 = normalize(r.ten_hp)
        is_f3_ta_bo_tro = (
            "tieng anh" in ten_hp_norm_f3
            and "bo tro" in ten_hp_norm_f3
        )
        if is_f3_ta_bo_tro and not is_done:
            a1_tieng_anh_codes_for_f3 = [
                row.ma_hp for row in curr.itertuples()
                if row.khoi_kt == "A1"
                and "tieng anh" in normalize(str(row.ten_hp))
                and "bo tro" not in normalize(str(row.ten_hp))
            ]
            has_a1_ta_done_f3 = any(
                c in completed_codes for c in a1_tieng_anh_codes_for_f3
            )
            if has_a1_ta_done_f3:
                # Đã có điểm môn TA khác trong A1 → coi F3 đã qua
                is_done = True

        is_registered_now = (not is_done) and (normalize(r.ten_hp) in registered_names)
        missing = [c for c in prereq_codes if c not in completed_codes]

        if is_done:
            status = "HOAN_THANH"
        elif r.ma_hp in failed_codes:
            # Môn đã thi nhưng trượt (có điểm chữ F hoặc danh_gia = "Không đạt")
            # → cần học lại. Coi như "đã đăng ký và có dữ liệu" nhưng KHÔNG đạt.
            status = "CAN_HOC_LAI"
        elif is_registered_now:
            status = "DANG_KY_KY_NAY"
        elif not missing:
            status = "CO_THE_DANG_KY"
        else:
            status = "BI_KHOA"

        # --- Xử lý khối TỰ CHỌN: nếu khối này chỉ cần đạt đủ 1 số tín chỉ
        # (không cần học hết), và bạn đã đạt đủ số tín bắt buộc của khối đó
        # rồi, thì các môn CHƯA HỌC còn lại trong khối không còn "cần thiết"
        # nữa — đổi thành KHONG_BAT_BUOC để không bị gợi ý nhầm.
        if status in ("CO_THE_DANG_KY", "BI_KHOA") and khoi_info and khoi_info["la_tu_chon"] and khoi_info["da_du_yeu_cau"]:
            status = "KHONG_BAT_BUOC"

        # ---- Cờ loại khỏi tín chỉ tích lũy ----
        # Theo anh Lâm (đã xác nhận): TOÀN BỘ khối F (F1, F2.x, F3, ...)
        # là "khối kiến thức điều kiện", chỉ qua/trượt, KHÔNG cộng
        # vào tín chỉ tích lũy tốt nghiệp:
        #   - F1   : Giáo dục quốc phòng (nếu có) - điều kiện ra trường
        #   - F2.x : Giáo dục thể chất - điều kiện ra trường (1 môn/khối)
        #   - F3   : Tiếng Anh bổ trợ - điều kiện để mở các môn TA khác
        khoi_kt_val = r.khoi_kt
        khoi_kt_upper = (str(khoi_kt_val).strip().upper()
                         if khoi_kt_val is not None and not (isinstance(khoi_kt_val, float) and pd.isna(khoi_kt_val))
                         else "")
        khong_tinh_tin = khoi_kt_upper.startswith("F")
        info = grade_by_code.get(r.ma_hp, {})
        courses.append({
            "ma_hp": r.ma_hp,
            "ten_hp": r.ten_hp,
            "khoi_kt": r.khoi_kt,
            "khoi_ten": khoi_ten_map.get(r.khoi_kt, r.khoi_kt),
            "khoi_la_tu_chon": bool(khoi_info["la_tu_chon"]) if khoi_info else None,
            "khoi_da_du": bool(khoi_info["da_du_yeu_cau"]) if khoi_info else None,
            "so_tin": r.so_tin,
            "hoc_ky_du_kien": r.hoc_ky_du_kien,
            "hoc_ky_thuc_te": getattr(r, "hoc_ky_thuc_te", None),
            "tien_quyet": prereq_codes,
            "tien_quyet_ten": [name for _, name in prereq_pairs],
            "trang_thai": status,
            "mon_con_thieu": missing,
            "diem": None if pd.isna(info.get("diem")) else info.get("diem"),
            "diem_chu": None if pd.isna(info.get("diem_chu")) else info.get("diem_chu"),
            "khong_tinh_tin_chi": khong_tinh_tin,
        })

    n_done = sum(1 for c in courses if c["trang_thai"] == "HOAN_THANH")
    n_registered = sum(1 for c in courses if c["trang_thai"] == "DANG_KY_KY_NAY")
    n_open = sum(1 for c in courses if c["trang_thai"] == "CO_THE_DANG_KY")
    n_locked = sum(1 for c in courses if c["trang_thai"] == "BI_KHOA")
    n_not_required = sum(1 for c in courses if c["trang_thai"] == "KHONG_BAT_BUOC")
    n_can_hoc_lai = sum(1 for c in courses if c["trang_thai"] == "CAN_HOC_LAI")
    tin_done = sum(
        float(c["so_tin"]) for c in courses
        if c["trang_thai"] == "HOAN_THANH"
        and not c.get("khong_tinh_tin_chi", False)
    )
    # Theo anh Lâm: "không tính F vào tín chỉ, không trừ gì hết"
    # vì k tính thì trừ làm gì. Tức là tổng tín chỉ crawl từ HTML
    # (#iTongSoTinChi = 134) là tổng thực sự cần tích lũy, đã KHÔNG bao
    # gồm các môn khối F (điều kiện).
    # Đã tích lũy (tin_done) cũng đã loại F → tỉ lệ 103/134 là chính xác.
    # Thứ tự ưu tiên: (1) tổng "tín bắt buộc" của bảng khối kiến thức,
    # (2) span #iTongSoTinChi, (3) cộng toàn bộ môn (hay bị cao, vd 189).
    if tong_bat_buoc_khoi > 0:
        tin_total = tong_bat_buoc_khoi
        nguon_tin_total = "tblTongHopDiem (tổng tín bắt buộc)"
    elif tong_tin_chi_html is not None:
        tin_total = tong_tin_chi_html
        nguon_tin_total = "span iTongSoTinChi"
    else:
        tin_total = tin_total_courses
        nguon_tin_total = "fallback: cộng tất cả môn (có thể sai)"
        print(f"[CẢNH BÁO] Không đọc được tổng tín bắt buộc, dùng {tin_total} TC.")
    print(f"[INFO] Tổng TC toàn khóa = {tin_total} (nguồn: {nguon_tin_total})")

    # Kiểm tra chéo: nếu span #iTongSoTinChi tồn tại mà lệch với tổng bắt buộc
    # của bảng khối thì báo ra để người dùng/dev biết dữ liệu có thể sai.
    canh_bao_du_lieu = []
    if (tong_bat_buoc_khoi > 0 and tong_tin_chi_html is not None
            and abs(tong_bat_buoc_khoi - tong_tin_chi_html) > 1e-6):
        msg = (f"Tổng tín bắt buộc theo bảng khối ({tong_bat_buoc_khoi:g}) khác "
               f"với số trên trang Chương trình học ({tong_tin_chi_html:g}).")
        canh_bao_du_lieu.append(msg)
        print(f"[CẢNH BÁO] {msg}")
    if tong_bat_buoc_khoi <= 0 and tong_tin_chi_html is None:
        canh_bao_du_lieu.append(
            "Không đọc được tổng tín chỉ tốt nghiệp; số liệu toàn khóa có thể sai."
        )

    gpa = compute_gpa(trans)

    # Bảng điểm THEO HỌC KỲ (cột "Lần học"): nguồn duy nhất để biết lịch sử học lại,
    # GPA từng kỳ và cảnh báo học vụ. Crawl cũ có khung #zone_bangdiem rỗng -> bỏ qua.
    try:
        hoc_vu_data = hoc_vu.phan_tich(
            hoc_vu.parse_bang_diem_hoc_ky(transcript_path), tin_total)
    except Exception as exc:  # không để lỗi phần này làm hỏng cả lộ trình
        hoc_vu_data = {"co_du_lieu": False}
        canh_bao_du_lieu.append(f"Không đọc được bảng điểm theo học kỳ: {exc}")
    if not hoc_vu_data.get("co_du_lieu"):
        canh_bao_du_lieu.append(
            "Chưa có bảng điểm theo học kỳ (cột 'Lần học'): cảnh báo học lại và học vụ "
            "chưa đầy đủ. Hãy cập nhật dữ liệu QLĐT bằng crawler bản mới.")
    else:
        th = hoc_vu_data["tong_hop"]
        if gpa.get("gpa_4") is not None and th["gpa_tich_luy"] is not None \
                and abs(th["gpa_tich_luy"] - gpa["gpa_4"]) > 0.01:
            canh_bao_du_lieu.append(
                f"GPA tính lại từ bảng điểm theo kỳ ({th['gpa_tich_luy']:.2f}) lệch GPA "
                f"trên QLĐT ({gpa['gpa_4']:.2f}).")

    goi_y_ky_toi = goi_y_ky_toi_tu_planner(
        courses, khoi_summary, min_tin_chi_ky_toi, max_tin_chi_ky_toi)

    return _sanitize_for_json({
        "tong_so_mon": len(courses),
        "so_mon_hoan_thanh": n_done,
        "so_mon_dang_ky_ky_nay": n_registered,
        "so_mon_co_the_dang_ky": n_open,
        "so_mon_bi_khoa": n_locked,
        "so_mon_khong_bat_buoc": n_not_required,
        "so_mon_can_hoc_lai": n_can_hoc_lai,
        "tin_chi_da_hoc": tin_done,
        "tin_chi_toan_khoa": tin_total,
        "nguon_tin_chi_toan_khoa": nguon_tin_total,
        "canh_bao_du_lieu": canh_bao_du_lieu,
        "ten_mon_khong_khop": sorted(unresolved_prereq_names),
        "khoi_summary": khoi_summary,
        "gpa": gpa,
        "hoc_vu": hoc_vu_data,
        "gioi_han_tin_chi_ky_toi": {
            "toi_thieu": min_tin_chi_ky_toi,
            "toi_da": max_tin_chi_ky_toi,
        },
        "goi_y_ky_toi": goi_y_ky_toi,
        "mon_hoc": courses,
    })


def _canh_bao_the_chat_thieu(mon_the_chat: list):
    """Cảnh báo các khối F2 (thể chất) chưa có môn HOAN_THANH nào.

    Quy tắc của anh Lâm: dù khối F không tính tín chỉ tích lũy, nhưng đây
    là điều kiện ra trường — mỗi khối F2.x phải có ≥1 môn đạt. Nếu khối
    nào anh/chị chưa học môn nào, tool sẽ:
      - in cảnh báo ⚠️
      - liệt kê các môn GDTC còn lại trong khối đó (kể cả KHONG_BAT_BUOC)
        để đăng ký, vì môn KHONG_BAT_BUOC chỉ là "không bắt buộc trong khung
        bắt buộc", nhưng đăng ký vẫn được nếu có mở lớp.
    """
    # Gom môn theo mã khối F2.x
    by_khoi: dict = {}
    for c in mon_the_chat:
        khoi_kt_raw = c.get("khoi_kt")
        khoi = (str(khoi_kt_raw).strip().upper()
                if khoi_kt_raw is not None and not (isinstance(khoi_kt_raw, float) and pd.isna(khoi_kt_raw))
                else "")
        if not khoi.startswith("F2"):
            continue
        by_khoi.setdefault(khoi, []).append(c)

    if not by_khoi:
        return

    khoi_can_bao: list = []
    for ma_khoi in sorted(by_khoi.keys()):
        ds_mon = by_khoi[ma_khoi]
        da_hoan_thanh = [m for m in ds_mon if m.get("trang_thai") == "HOAN_THANH"]
        if da_hoan_thanh:
            continue  # khối này đã có môn đạt → OK
        # Khối thiếu → đề xuất các môn còn lại để đăng ký
        # (gồm cả KHONG_BAT_BUOC vì môn thay thế vẫn có thể đăng ký nếu mở lớp)
        de_xuat = [m for m in ds_mon
                   if m.get("trang_thai") in ("CO_THE_DANG_KY", "BI_KHOA", "KHONG_BAT_BUOC")]
        ten_khoi = by_khoi[ma_khoi][0].get("ten_khoi") or ma_khoi
        khoi_can_bao.append((ma_khoi, ten_khoi, ds_mon, de_xuat))

    if not khoi_can_bao:
        return

    print(f"\n{'=' * 70}")
    print("  ⚠️  CẢNH BÁO KHỐI THỂ CHẤT CHƯA CÓ MÔN NÀO HOÀN THÀNH")
    print("=" * 70)
    print("  Khối F là điều kiện ra trường, không tính TC tích lũy.")
    print("  NHƯNG mỗi khối F2.x bắt buộc phải có ≥1 môn đạt.")
    print("  Bạn cần đăng ký 1 môn trong các khối sau:")
    for ma_khoi, ten_khoi, ds_mon, de_xuat in khoi_can_bao:
        print(f"\n  ▸ {ma_khoi} — {ten_khoi} ({len(ds_mon)} môn trong khung, "
              f"chưa có môn nào đạt)")
        for m in de_xuat:
            tt = m.get("trang_thai", "?")
            tc = m.get("so_tin", 0)
            tt_str = "có thể đăng ký" if tt == "CO_THE_DANG_KY" else f"({tt})"
            print(f"     [{m['ma_hp']}] {m['ten_hp']}  ({tc} tín — {tt_str})")


def _group_mon_by_kind(mon_hoc: list, khoi_summary: dict) -> dict:
    """Chia môn học thành 3 nhóm: BAT_BUOC / TU_CHON / THE_CHAT.

    Phân loại dựa vào cờ ``khoi_la_tu_chon`` (đã được build sẵn) và mã khối KT.
    Quy ước:
      - BAT_BUOC : khoi_la_tu_chon == False (hoặc không có thông tin khối)
      - THE_CHAT : mã khối bắt đầu 'F2.' (Chạy, Bóng, Aerobic, Pickleball…)
                   và F3 (Tiếng Anh bổ trợ) — cũng là khối 'tích lũy'
      - TU_CHON  : phần còn lại (khối tự chọn: D2.1, C2, F1…)
      - BỎ QUA  : khối Quốc phòng (F1) — không phân tích
    """
    groups = {"BAT_BUOC": [], "TU_CHON": [], "THE_CHAT": []}
    for c in mon_hoc:
        khoi_kt_raw = c.get("khoi_kt")
        khoi_kt = (str(khoi_kt_raw).strip().upper()
                   if khoi_kt_raw is not None and not (isinstance(khoi_kt_raw, float) and pd.isna(khoi_kt_raw))
                   else "")
        # Bỏ qua khối Quốc phòng
        if khoi_kt.startswith("F1"):
            continue
        # Nếu khối được đánh dấu bỏ qua thì skip
        ks = khoi_summary.get(c.get("khoi_kt", ""), {}) if c.get("khoi_kt") else {}
        if ks.get("bo_qua"):
            continue

        la_tu_chon = c.get("khoi_la_tu_chon")
        if la_tu_chon is False:
            groups["BAT_BUOC"].append(c)
        elif khoi_kt.startswith("F2.") or khoi_kt == "F3":
            groups["THE_CHAT"].append(c)
        elif la_tu_chon is True:
            groups["TU_CHON"].append(c)
        else:
            # Mã khối trống (vd: Tự chọn tự do) → coi là tự chọn
            groups["TU_CHON"].append(c)
    return groups


def _status_for(c: dict, khoi_summary: dict) -> str:
    """Trả về 1 trong:
      - 'DA_HOC'      : đã hoàn thành / đang đăng ký kỳ này
      - 'CAN_HOC_LAI' : môn đã thi nhưng trượt (điểm F hoặc "Không đạt") → cần học lại
      - 'KHONG_CAN'   : môn tự chọn mà khối đã đủ TC
      - 'CHUA_HOC'    : chưa học (bị khóa hoặc có thể đăng ký)
    """
    stt = c.get("trang_thai")
    if stt == "CAN_HOC_LAI":
        return "CAN_HOC_LAI"
    if stt in ("HOAN_THANH", "DANG_KY_KY_NAY"):
        return "DA_HOC"
    if c.get("khoi_da_du") and c.get("khoi_la_tu_chon"):
        return "KHONG_CAN"
    return "CHUA_HOC"


def _print_khoi_bang_tra_cuu(khoi_summary: dict):
    """In bảng tổng hợp 'Khối kiến thức' lấy từ trang Tra cứu điểm — đây là
    nguồn số liệu TC tin cậy nhất (do nhà trường cấp)."""
    if not khoi_summary:
        return
    print("\n" + "-" * 70)
    print("BẢNG KHỐI KIẾN THỨC  (lấy từ Tra cứu điểm — bảng #tblTongHopDiem)")
    print("-" * 70)
    print(f"  {'Mã':6s} {'Tên khối':45s} {'TC':>4s} {'BB':>4s} {'Đã học':>8s} {'Trạng thái':<14s}")
    for ma in sorted(khoi_summary.keys()):
        k = khoi_summary[ma]
        if k.get("bo_qua"):
            print(f"  {ma:6s} {k['ten_khoi'][:45]:45s} — bỏ qua (QP-AN)")
            continue
        tt = "BẮT BUỘC" if not k["la_tu_chon"] else "TỰ CHỌN"
        tt_full = ("ĐỦ" if k["da_du_yeu_cau"] else "THIẾU") + f"  ({tt})"
        def _g(x):
            try:
                v = float(x)
                if v != v:
                    return 0
                return int(v)
            except (TypeError, ValueError):
                return 0
        print(f"  {ma:6s} {k['ten_khoi'][:45]:45s} "
              f"{_g(k['tong_tin']):>4d} {_g(k['tin_bat_buoc']):>4d} "
              f"{_g(k['da_tich_luy']):>5d}/{_g(k['tin_bat_buoc']):<3d} {tt_full}")


def _print_one_group(ten_nhom: str, mon_list: list, khoi_summary: dict):
    print(f"\n{'=' * 70}")
    print(f"  {ten_nhom}  ({len(mon_list)} môn trong khung)")
    print("=" * 70)

    if not mon_list:
        print("  (không có)")
        return

    # Đếm theo trạng thái
    da_hoc        = [c for c in mon_list if _status_for(c, khoi_summary) == "DA_HOC"]
    can_hoc_lai   = [c for c in mon_list if _status_for(c, khoi_summary) == "CAN_HOC_LAI"]
    khong_can     = [c for c in mon_list if _status_for(c, khoi_summary) == "KHONG_CAN"]
    chua_hoc      = [c for c in mon_list if _status_for(c, khoi_summary) == "CHUA_HOC"]

    # Gom môn theo khối để in thông báo khối
    def _khoi(c): return c.get("khoi_kt") or "_KHOI_RONG_"
    khoi_da_hoc: dict = {}
    for c in da_hoc:
        khoi_da_hoc.setdefault(_khoi(c), []).append(c)

    print(f"  Học rồi          : {len(da_hoc)}")
    print(f"  ⚠️  Cần học lại   : {len(can_hoc_lai)}  (đã thi nhưng trượt)")
    print(f"  Không cần thiết  : {len(khong_can)}  "
          f"(khối đã đủ TC bắt buộc)")
    print(f"  Chưa học         : {len(chua_hoc)}")

    # ---- Thông báo từng khối đã đủ / học thừa ----
    thong_bao_khoi: list = []
    for ma_khoi, ds_mon in khoi_da_hoc.items():
        if ma_khoi == "_KHOI_RONG_" or not ma_khoi:
            continue
        khoi = khoi_summary.get(ma_khoi, {})
        if not khoi or khoi.get("bo_qua"):
            continue
        bb = khoi.get("tin_bat_buoc") or 0
        tl = khoi.get("da_tich_luy") or 0
        try:
            bb_f = float(bb)
            tl_f = float(tl)
        except (TypeError, ValueError):
            continue
        if tl_f <= 0:
            # server không trả số TC tích lũy (vd F3) → dùng tổng TC môn đã học
            tl_f = float(sum(float(m.get("so_tin") or 0) for m in ds_mon))
        if tl_f >= bb_f and bb_f > 0:
            ten = khoi.get("ten_khoi", "Khối này")
            if tl_f > bb_f:
                thua_tc = tl_f - bb_f
                thong_bao_khoi.append(
                    f"  ✅ {ten}: ĐÃ HOÀN THÀNH — không cần học thêm. "
                    f"(Bạn học thừa ~{thua_tc:g} TC so với yêu cầu {bb_f:g} TC)"
                )
            else:
                thong_bao_khoi.append(
                    f"  ✅ {ten}: ĐÃ HOÀN THÀNH — không cần học thêm. "
                    f"(đủ {tl_f:g}/{bb_f:g} TC)"
                )
        elif bb_f == 0 and tl_f > 0:
            # Khối không có yêu cầu TC (vd F3) — note là đã học bao nhiêu
            ten = khoi.get("ten_khoi", "Khối này")
            thong_bao_khoi.append(
                f"  ℹ️  {ten}: đã tích lũy {tl_f:g} TC (khối không yêu cầu tối thiểu)."
            )

    if thong_bao_khoi:
        print("\n  📣 THÔNG BÁO KHỐI:")
        for tb in thong_bao_khoi:
            print(tb)

    def _ten_khoi_or_ma(c):
        """Ưu tiên hiển thị TÊN khối (dễ hiểu với sinh viên) thay vì mã nội
        bộ kiểu 'A1'/'D2.2' — mã chỉ giữ lại khi không có tên."""
        khoi = khoi_summary.get(c.get("khoi_kt", ""), {})
        return khoi.get("ten_khoi") or c.get("khoi_kt") or "chưa phân loại"

    def _line(c):
        # Nếu là bài "Kiểm tra năng lực TA đầu khóa" đã được tính là hoàn
        # thành thông qua môn Tiếng Anh bổ trợ, hiển thị ghi chú để SV hiểu.
        ten_hp_l = (c.get("ten_hp") or "").lower()
        if (c.get("so_tin") == 0
                and "kiểm tra năng lực" in ten_hp_l
                and "tiếng anh" in ten_hp_l
                and "đầu khóa" in ten_hp_l):
            return (f"    [{c['ma_hp']}] {c['ten_hp']}  "
                    f"(0 tín, {_ten_khoi_or_ma(c)}, ✅ đã qua — thay thế bằng Tiếng Anh bổ trợ)")
        return (f"    [{c['ma_hp']}] {c['ten_hp']}  "
                f"({c['so_tin']} tín, {_ten_khoi_or_ma(c)})")

    if da_hoc:
        print("\n  ▸ ĐÃ HỌC:")
        for c in da_hoc:
            print(_line(c))
    if can_hoc_lai:
        # Môn nợ — đã thi nhưng trượt → cần học lại
        print("\n  ▸ ⚠️  CẦN HỌC LẠI (đã thi nhưng trượt):")
        for c in can_hoc_lai:
            diem_cu = c.get("diem_chu") or "?"
            diem_so = c.get("diem")
            diem_str = f"{diem_so:g} ({diem_cu})" if diem_so is not None else diem_cu
            print(f"    [{c['ma_hp']}] {c['ten_hp']}  "
                  f"({c['so_tin']} tín, {_ten_khoi_or_ma(c)}, điểm cũ: {diem_str})")
    if khong_can:
        # Chỉ ghi số lượng, không liệt kê từng môn — các môn này không cần
        # hành động gì (khối tự chọn đã đủ tín chỉ), liệt kê chi tiết chỉ
        # gây rối mà không giúp ích cho quyết định đăng ký kỳ tới.
        print(f"\n  ▸ KHÔNG CẦN THIẾT: {len(khong_can)} môn tự chọn "
              f"(khối đã đủ tín chỉ, không cần học thêm)")
    if chua_hoc:
        print("\n  ▸ CHƯA HỌC:")
        for c in chua_hoc:
            print(_line(c))


def print_report(data: dict):
    print("=" * 70)
    print("LỘ TRÌNH HỌC TẬP")
    print("=" * 70)
    print(f"Tổng số môn trong chương trình : {data['tong_so_mon']}")
    print(f"  Đã hoàn thành      : {data['so_mon_hoan_thanh']}")
    print(f"  Đang đăng ký kỳ này: {data.get('so_mon_dang_ky_ky_nay', 0)}")
    print(f"  Có thể đăng ký     : {data['so_mon_co_the_dang_ky']}")
    so_no = data.get("so_mon_can_hoc_lai", 0)
    if so_no:
        print(f"  ⚠️  Cần học lại      : {so_no}  (học phần nợ - ưu tiên đăng ký trước)")
    print(f"  Đang bị khóa       : {data['so_mon_bi_khoa']}")
    print(f"Tín chỉ: {data['tin_chi_da_hoc']:.1f} / {data['tin_chi_toan_khoa']:.1f}")

    gpa = data["gpa"]

    # ---- BẢNG KHỐI KIẾN THỨC (từ tra cứu điểm) — in ngay đầu ----
    khoi_summary = data.get("khoi_summary", {}) or {}
    _print_khoi_bang_tra_cuu(khoi_summary)

    # ---- In 3 nhóm: BẮT BUỘC / TỰ CHỌN / THỂ CHẤT ----
    groups = _group_mon_by_kind(data["mon_hoc"], khoi_summary)
    _print_one_group("1) HỌC PHẦN BẮT BUỘC", groups["BAT_BUOC"], khoi_summary)
    _print_one_group("2) HỌC PHẦN TỰ CHỌN",  groups["TU_CHON"],  khoi_summary)
    _print_one_group("3) HỌC PHẦN THỂ CHẤT", groups["THE_CHAT"], khoi_summary)

    # ---- CẢNH BÁO KHỐI THỂ CHẤT CHƯA ĐỦ ĐIỀU KIỆN ----
    # Theo anh Lâm: khối F là điều kiện, không tính TC, NHƯNG phải học đủ
    # từng khối F2 (mỗi khối F2.x phải có ≥1 môn HOAN_THANH). Nếu khối
    # nào chưa có môn HOAN_THANH, cảnh báo + đề xuất đăng ký môn GDTC
    # còn lại trong khối đó (các môn KHONG_BAT_BUOC vẫn có thể đăng ký).
    _canh_bao_the_chat_thieu(groups["THE_CHAT"])

    print("\n--- GPA HIỆN TẠI ---")
    if gpa["gpa_4"] is not None:
        print(f"  GPA hệ 4  : {gpa['gpa_4']}  ({gpa['xep_loai']})")
        print(f"  GPA hệ 10 : {gpa['gpa_10']}")
        print(f"  Tín chỉ đã tích lũy: {data['tin_chi_da_hoc']:.1f} / "
              f"{data['tin_chi_toan_khoa']:.1f} tín chỉ "
              f"({gpa['so_tin_tinh_gpa']:.0f} tín đã tín GPA)")
    else:
        print("  Chưa có môn nào được tính GPA.")

    if data["ten_mon_khong_khop"]:
        print("\n(!) Không khớp được tên môn tiên quyết sau với mã học phần "
              "(có thể do sai chính tả/dấu * trong dữ liệu gốc):")
        for name in data["ten_mon_khong_khop"]:
            print(f"    - {name}")

    # ---- HỌC PHẦN NỢ (đã thi nhưng trượt) — ưu tiên số 1 ----
    no_courses = [c for c in data["mon_hoc"] if c["trang_thai"] == "CAN_HOC_LAI"]
    if no_courses:
        print(f"\n{'=' * 70}")
        print(f"  ⚠️  HỌC PHẦN NỢ - CẦN HỌC LẠI ({len(no_courses)} môn)")
        print(f"{'=' * 70}")
        print("  ƯU TIÊN ĐĂNG KÝ NGAY KỲ TỚI:")
        for c in no_courses:
            diem_cu = c.get("diem_chu") or "?"
            diem_so = c.get("diem")
            diem_str = f"{diem_so:g} ({diem_cu})" if diem_so is not None else diem_cu
            hoc_ky = c.get("hoc_ky_du_kien") or "?"
            print(f"    [{c['ma_hp']}] {c['ten_hp']}  "
                  f"({c['so_tin']} tín, điểm cũ: {diem_str}, dự kiến {hoc_ky})")

    open_courses = [c for c in data["mon_hoc"] if c["trang_thai"] == "CO_THE_DANG_KY"]
    print(f"\n--- CÓ THỂ ĐĂNG KÝ NGAY, TOÀN BỘ ({len(open_courses)} môn) ---")
    for c in sorted(open_courses, key=lambda x: semester_sort_key(x.get("hoc_ky_du_kien"))):
        print(f"  [{c['ma_hp']}] {c['ten_hp']}  ({c['so_tin']} tín, "
              f"dự kiến {c['hoc_ky_du_kien']})")

    goi_y = data["goi_y_ky_toi"]
    tong_tin_goi_y = sum(c["so_tin"] or 0 for c in goi_y)
    gioi_han = data.get("gioi_han_tin_chi_ky_toi", {"toi_thieu": 8, "toi_da": 16})
    print(f"\n--- GỢI Ý ĐĂNG KÝ KỲ TỚI ({len(goi_y)} môn, "
          f"~{tong_tin_goi_y:.0f} tín chỉ; giới hạn "
          f"{gioi_han['toi_thieu']:g}-{gioi_han['toi_da']:g} tín) ---")
    for c in goi_y:
        note = f", mở khóa cho {c['mo_khoa_cho']} môn khác" if c["mo_khoa_cho"] else ""
        tag = " [HỌC LẠI]" if c.get("uu_tien") == "HOC_LAI" else ""
        print(f"  [{c['ma_hp']}] {c['ten_hp']}  ({c['so_tin']} tín, "
              f"dự kiến {c['hoc_ky_du_kien']}{note}){tag}")

    locked_courses = [c for c in data["mon_hoc"] if c["trang_thai"] == "BI_KHOA"]
    print(f"\n--- ĐANG BỊ KHÓA ({len(locked_courses)} môn) ---")
    code_to_name = {c["ma_hp"]: c["ten_hp"] for c in data["mon_hoc"]}
    for c in sorted(locked_courses, key=lambda x: semester_sort_key(x.get("hoc_ky_du_kien"))):
        missing_names = [code_to_name.get(m, m) for m in c["mon_con_thieu"]]
        print(f"  [{c['ma_hp']}] {c['ten_hp']}  — còn thiếu: "
              f"{', '.join(missing_names)}")

    registered = [c for c in data["mon_hoc"] if c["trang_thai"] == "DANG_KY_KY_NAY"]
    if registered:
        print(f"\n--- ĐANG ĐĂNG KÝ KỲ NÀY ({len(registered)} môn — chưa có điểm, "
              "không tính lại vào gợi ý) ---")
        for c in registered:
            print(f"  [{c['ma_hp']}] {c['ten_hp']}  ({c['so_tin']} tín)")


def print_summary_report(data: dict):
    """In báo cáo tổng hợp dạng KHỐI — trực quan, dễ đọc cho sinh viên.
    
    Format theo yêu cầu:
      🎓 LỘ TRÌNH TỐT NGHIỆP: 103.0 / 134.0 TÍN CHỈ (GPA: 2.76 - Khá)
      1. KHỐI ĐÃ HOÀN THÀNH 100% (Không cần đăng ký thêm)
      2. KHỐI CẦN HOÀN THÀNH
      3. HỌC PHẦN ĐANG HỌC KỲ NÀY
    """
    gpa = data["gpa"]
    tin_da = data["tin_chi_da_hoc"]
    tin_tong = data["tin_chi_toan_khoa"]
    xep_loai = gpa.get("xep_loai", "?")
    gpa4 = gpa.get("gpa_4")
    gpa10 = gpa.get("gpa_10")
    
    # ---- Header ----
    gpa_str = f"{gpa4} - {xep_loai}" if gpa4 is not None else "Chưa có"
    print("=" * 70)
    print(f"  🎓 LỘ TRÌNH TỐT NGHIỆP: {tin_da:.0f} / {tin_tong:.0f} TÍN CHỈ "
          f"(GPA: {gpa_str})")
    print("=" * 70)
    
    # ---- Gom môn theo khối + trạng thái ----
    khoi_summary = data.get("khoi_summary", {}) or {}
    mon_hoc = data["mon_hoc"]
    
    # Các khối chính (bỏ F1, F3 — chỉ cảnh báo nếu thiếu)
    KHOI_CHINH = ["A1", "B1", "C1", "C2", "D1", "D2.1", "D2.2", "E1", "E2"]
    # Các khối F
    F_KHOI = {k: v for k, v in khoi_summary.items() if k.startswith("F")}
    
    # ---- Gom môn theo khối ----
    by_khoi: dict = {}
    for c in mon_hoc:
        k = c.get("khoi_kt") or "_NONE_"
        by_khoi.setdefault(k, []).append(c)
    
    def tin_da_khoi(khoi_key):
        ks = khoi_summary.get(khoi_key, {})
        bb = ks.get("tin_bat_buoc", 0)
        tl = ks.get("da_tich_luy")
        if tl is not None and not (isinstance(tl, float) and pd.isna(tl)):
            return float(tl)
        if bb and bb > 0:
            # fallback: đếm môn HOAN_THANH trong khối
            return sum(float(m.get("so_tin", 0)) for m in by_khoi.get(khoi_key, [])
                      if m.get("trang_thai") == "HOAN_THANH"
                      and not m.get("khong_tinh_tin_chi", False))
        return 0.0
    
    def tin_yeu_cau(khoi_key):
        ks = khoi_summary.get(khoi_key, {})
        bb = ks.get("tin_bat_buoc", 0)
        return float(bb) if bb and not (isinstance(bb, float) and pd.isna(bb)) else 0.0
    
    # ---- 1. KHỐI ĐÃ HOÀN THÀNH ----
    # Các khối mà da_tich_luy >= tin_bat_buoc và > 0
    da_xong: list = []
    con_thieu: list = []
    for k in KHOI_CHINH:
        ks = khoi_summary.get(k)
        if not ks or ks.get("bo_qua"):
            continue
        bb = tin_yeu_cau(k)
        tl = tin_da_khoi(k)
        if bb <= 0:
            continue
        if tl >= bb:
            da_xong.append((k, ks.get("ten_khoi", k), bb, tl))
        else:
            con_thieu.append((k, ks.get("ten_khoi", k), bb, tl))
    
    # Gom các khối nhỏ lại thành nhóm lớn cho dễ đọc
    # A: A1 (đại cương)
    # C: C1 + C2 (bổ trợ)
    # D: D1 + D2.1 + D2.2 (chuyên ngành)
    # E: E1 + E2 (thực tập)
    nhom_khoi = {
        "A": ["A1"],
        "C": ["C1", "C2"],
        "D": ["D1", "D2.1", "D2.2"],
        "E": ["E1", "E2"],
    }
    
    def khoi_da_xong_nhom(nhom_keys):
        d = 0.0
        b = 0.0
        for k in nhom_keys:
            d += tin_da_khoi(k)
            b += tin_yeu_cau(k)
        return d, b
    
    # ---- CẦN HOÀN THÀNH ----
    # Gom môn cần học theo nhóm (CO_THE_DANG_KY, BI_KHOA, CAN_HOC_LAI)
    can_hoc_lai = [c for c in mon_hoc if c["trang_thai"] == "CAN_HOC_LAI"]
    co_the_dk = [c for c in mon_hoc if c["trang_thai"] == "CO_THE_DANG_KY"]
    bi_khoa = [c for c in mon_hoc if c["trang_thai"] == "BI_KHOA"]
    dang_ky_nay = [c for c in mon_hoc if c["trang_thai"] == "DANG_KY_KY_NAY"]
    
    tong_thieu_tin = sum(float(c.get("so_tin", 0)) for c in can_hoc_lai + co_the_dk + bi_khoa)
    tong_thieu_mon = len(can_hoc_lai) + len(co_the_dk) + len(bi_khoa)
    
    print(f"\n2. KHỐI CẦN HOÀN THÀNH (Còn thiếu {tong_thieu_mon} môn - {tong_thieu_tin:.0f} tín)")
    print("-" * 70)
    
    if can_hoc_lai:
        print(f"  🔴 HỌC PHẦN NỢ - CẦN HỌC LẠI ({len(can_hoc_lai)} môn):")
        for c in can_hoc_lai:
            dc = c.get("diem_chu") or "?"
            ds = c.get("diem")
            ds_str = f"{ds:g} ({dc})" if ds is not None else dc
            k = c.get("khoi_kt") or ""
            ten_k = khoi_summary.get(k, {}).get("ten_khoi") or k
            print(f"    [{c['ma_hp']}] {c['ten_hp']} ({c['so_tin']} tín, {ten_k}) "
                  f"— điểm cũ: {ds_str}")
    
    # Gom CO_THE_DANG_KY + BI_KHOA theo khối để in đẹp
    def in_nhom_khoi(title, mon_list, mau="🟡", locked=False):
        if not mon_list:
            return
        print(f"  {mau} {title} ({len(mon_list)} môn):")
        # Nhóm theo khối
        by_k = {}
        for c in mon_list:
            kv = c.get("khoi_kt")
            k = (str(kv) if kv is not None and not (isinstance(kv, float) and pd.isna(kv)) else "_NONE_")
            by_k.setdefault(k, []).append(c)
        for k in sorted(by_k.keys(), key=str):
            ten_k = khoi_summary.get(str(k), {}).get("ten_khoi") or k
            if locked:
                print(f"    🔒 {ten_k} — {len(by_k[k])} môn chưa mở:")
                for c in by_k[k]:
                    # Lấy tên các môn thiếu
                    thieu = [mon_hoc[0].get("ten_hp", "?")]  # placeholder
                    # Tìm trong mon_con_thieu
                    missing_names = []
                    for mc in mon_hoc:
                        if mc["ma_hp"] in c.get("mon_con_thieu", []):
                            missing_names.append(mc["ten_hp"])
                    print(f"      [{c['ma_hp']}] {c['ten_hp']} ({c['so_tin']} tín) — "
                          f"còn thiếu: {', '.join(missing_names) if missing_names else 'tiên quyết'}")
            else:
                # In "GỢI Ý ĐĂNG KÝ KỲ TỚI" 1 LẦN cho tất cả môn
                pass  # xử lý riêng bên dưới
    
    # In tất cả CO_THE_DANG_KY gộp 1 lần
    if co_the_dk:
        # Gom theo khối rồi in
        by_k2: dict = {}
        for c in co_the_dk:
            kv = c.get("khoi_kt")
            k = (str(kv) if kv is not None and not (isinstance(kv, float) and pd.isna(kv)) else "_NONE_")
            by_k2.setdefault(k, []).append(c)
        print(f"  🟡 Có thể đăng ký ngay ({len(co_the_dk)} môn):")
        print("  👉 GỢI Ý ĐĂNG KÝ KỲ TỚI:")
        for k in sorted(by_k2.keys(), key=str):
            for c in sorted(by_k2[k], key=lambda x: semester_sort_key(x.get("hoc_ky_du_kien"))):
                ten_k = khoi_summary.get(str(k), {}).get("ten_khoi") or k
                print(f"    [ ] [{c['ma_hp']}] {c['ten_hp']} ({c['so_tin']} tín)")
    
    # BI_KHOA - gom theo nhóm điều kiện
    if bi_khoa:
        # Gom theo nguyên nhân khóa
        by_nguyen_nhan = {}
        for c in bi_khoa:
            thieu = tuple(sorted(c.get("mon_con_thieu", [])))
            by_nguyen_nhan.setdefault(thieu, []).append(c)
        
        if len(by_nguyen_nhan) == 1 and len(bi_khoa) <= 3:
            # Tất cả cùng chờ 1 thứ → gom lại
            nguyen_nhan = list(by_nguyen_nhan.keys())[0]
            ten_thieu = [c.get("ten_hp", "?") for c in mon_hoc if c["ma_hp"] in nguyen_nhan]
            thieu_str = ", ".join(ten_thieu) if ten_thieu else "các môn tiên quyết"
            print(f"  🔴 Thực tập & Tốt nghiệp (E1, E2) — {len(bi_khoa)} môn đang chờ:")
            print(f"    🔒 {', '.join(c['ten_hp'] for c in bi_khoa)} — "
                  f"Đang khóa (chờ xong {thieu_str})")
        else:
            for nguyen_nhan, ds in by_nguyen_nhan.items():
                ten_thieu = [c.get("ten_hp", "?") for c in mon_hoc if c["ma_hp"] in nguyen_nhan]
                thieu_str = ", ".join(ten_thieu) if ten_thieu else "tiên quyết"
                print(f"  🔴 Đang khóa ({len(ds)} môn) — chờ: {thieu_str}")
                for c in ds:
                    print(f"    🔒 [{c['ma_hp']}] {c['ten_hp']} ({c['so_tin']} tín)")
    
    # ---- 3. HỌC PHẦN ĐANG HỌC KỲ NÀY ----
    if dang_ky_nay:
        tong_tin_dn = sum(float(c.get("so_tin", 0)) for c in dang_ky_nay)
        ten_mon = " | ".join(f"{c['ten_hp']} ({c['so_tin']} tín)" for c in dang_ky_nay)
        print(f"\n3. HỌC PHẦN ĐANG HỌC KỲ NÀY ({len(dang_ky_nay)} môn - {tong_tin_dn:.0f} tín chỉ)")
        print("-" * 70)
        print(f"  ⏳ {ten_mon}")
    
    # ---- Cảnh báo F2 thiếu ----
    from build_roadmap import _canh_bao_the_chat_thieu
    f2_mon = [c for c in mon_hoc if isinstance(c.get("khoi_kt"), str) and c.get("khoi_kt", "").startswith("F2")]
    _canh_bao_the_chat_thieu(f2_mon)
    
    # ---- Footer ----
    print()
    print(f"GPA hệ 4: {gpa4} ({xep_loai}) | GPA hệ 10: {gpa10}" if gpa4 else "")


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        print("Cách dùng: python build_roadmap.py <file_Chuong_trinh_hoc.html> "
              "<file_Tra_cuu_diem.html> [file_Tra_cuu_ket_qua_dang_ky.html]")
        sys.exit(1)

    registered_path = sys.argv[3] if len(sys.argv) == 4 else None
    data = build_roadmap(sys.argv[1], sys.argv[2], registered_path)
    print_summary_report(data)

    out_path = Path("roadmap.json")
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nĐã lưu dữ liệu đầy đủ vào: {out_path.resolve()}")

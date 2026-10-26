"""Sinh HTML mẫu (đã ẩn danh) mô phỏng 3 trang QLDT để test build_roadmap."""
from pathlib import Path

OUT = Path(__file__).parent / "fixtures"
OUT.mkdir(parents=True, exist_ok=True)

def table(tid, headers, rows):
    th = "".join(f"<th>{h}</th>" for h in headers)
    tr = "".join("<tr>" + "".join(f"<td>{v}</td>" for v in r) + "</tr>" for r in rows)
    return f'<table id="{tid}"><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>'

# Chương trình học: 6 môn (tổng 17 TC) gồm 1 môn khối F không tính
curr_rows = [
    ["MAT1", "Toán A", "A1", 3, "", 1],
    ["PRG1", "Lập trình 1", "B1", 4, "", 1],
    ["PRG2", "Lập trình 2", "B1", 4, "Học trước(Lập trình 1)", 2],
    ["DBS1", "Cơ sở dữ liệu", "D1", 3, "Học trước(Lập trình 1)", 2],
    ["WEB1", "Xây dựng web", "D1", 3, "Học trước(Lập trình 2)", 3],
    ["GDQ1", "Giáo dục quốc phòng", "F1", 8, "", 1],
]
curr = table("tblHocPhan",
    ["Mã học phần", "Tên học phần", "Khối KT", "Số tín học phần", "Điều kiện ràng buộc", "Học kỳ dự kiến"],
    curr_rows)
(OUT / "chuong_trinh_hoc.html").write_text(
    f"<html><body><span id='iTongSoTinChi'>19</span>{curr}</body></html>", encoding="utf-8")

# Tra cứu điểm: bảng điểm + bảng tổng hợp khối (tổng bắt buộc = 17, tổng tất cả = 19)
trans = table("tblTongHopDiemHP",
    ["Mã học phần", "Mã khối", "Tên khối", "Số tín chỉ", "Điểm", "Điểm quy đổi", "Điểm chữ", "Đánh giá", "Kết quả"],
    [["MAT1", "A1", "Đại cương", 3, 8.0, 3.5, "B+", "Đạt", ""],
     ["PRG1", "B1", "Cơ sở ngành", 4, 7.0, 3.0, "B", "Đạt", ""]])
khoi = table("tblTongHopDiem",
    ["STT", "Mã khối", "Tên khối", "Tổng số tín chỉ", "Tổng số tín bắt buộc", "Tổng số tín đã tích lũy"],
    [[1, "A1", "Đại cương", 3, 3, 3],
     [2, "B1", "Cơ sở ngành", 8, 8, 4],
     [3, "D1", "Chuyên ngành", 6, 6, 0],
     [4, "F1", "Giáo dục quốc phòng - an ninh", 2, 0, 0],
     ["", "Tổng", "", 19, 17, 7]])
(OUT / "tra_cuu_diem.html").write_text(
    f"<html><body>{trans}{khoi}</body></html>", encoding="utf-8")
print("ok")

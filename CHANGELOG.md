# Changelog — Phenikaa Advisor

## Đăng nhập tự động bằng mã sinh viên + mật khẩu
- **Form ở cửa sổ crawl:** bấm "Cập nhật dữ liệu" → nhập mã sinh viên (tự thêm `@st.phenikaa-uni.edu.vn`) và mật khẩu
  → Advisor tự bấm "Sign in using Microsoft", điền tài khoản, mật khẩu và đi tiếp như bình thường. Có nút "Bỏ qua, tự đăng nhập" để giữ luồng cũ.
- **Crawler:** thêm `click_microsoft_signin`, `fill_microsoft_username`, `fill_microsoft_password` (chỉ chạy trên `qldtbeta…/login.aspx` và
  `login.microsoftonline.com`, có throttle). Mật khẩu chỉ thử **1 lần**; nếu Microsoft báo sai thì dừng, tránh bị khóa tài khoản.
- **Server:** `/api/crawl_auto` nhận thêm `mssv` + `password` (validate ở `qldt_credentials.py`). Mật khẩu không lưu vào job, không nằm trên
  dòng lệnh, không ghi log; chỉ truyền qua biến môi trường của tiến trình con và crawler xóa nó khỏi `environ` ngay khi khởi động.
- **Bảo mật:** nên chạy qua HTTPS khi deploy (mật khẩu đi từ trình duyệt tới server). Xác thực 2 bước (nếu có) vẫn do người dùng tự hoàn tất.
- Trang ADFS của trường (`sso.phenikaa-uni.edu.vn`, ô `#passwordInput`, nút `#submitButton`) được hỗ trợ và không còn bị kéo về QLĐT.
- Nhập mật khẩu có kiểm tra giá trị thật sự được giữ lại (nếu không thì gõ từng phím); bấm Sign in bằng chuột, rồi JS, rồi Enter.
- Nhật ký chẩn đoán: trạng thái BẬT/TẮT, lỗi khi tự điền, danh sách ô tìm thấy nếu kẹt.
- Test mới: `test_login_autofill.py`, `e2e_mock_login.py` (chạy trên trang giả bằng Chromium thật).

## Kiểm tra điều kiện tốt nghiệp + sửa quy tắc tối thiểu tín chỉ/kỳ
- **Module mới `graduation_check.py`:** gom các điều kiện Điều 26.1 Quy chế (QĐ 1062/QĐ-ĐHP-ĐT) thành một danh sách
  trạng thái `pass / fail / pending / unknown` và một kết luận `du_dieu_kien / cho_xac_nhan / chua_du`.
  Dùng lại `advisor` (khối tín chỉ, GPA, học lại > 5%) nên số liệu khớp phần Cố vấn. Chỉ là ước tính, không phải xét duyệt chính thức.
  Tự kiểm tra: đủ tín chỉ theo khối, không còn môn bắt buộc cần học lại, GPA >= 2,00, GDTC (khối F2.x).
  Cần sinh viên tự xác nhận: ngoại ngữ, tin học, kỷ luật, nghĩa vụ tài chính, GDQP-AN khi QLĐT không ghi tích lũy.
- **Giao diện:** tab mới "Điều kiện tốt nghiệp" ở thanh bên (kết luận, từng điều kiện, ô tự xác nhận, hạng dự kiến).
- **Frontend mô phỏng:** bỏ chặn cứng "mỗi kỳ >= 8 TC" ở `app.js` (chỉ còn chặn > 16 TC); việc kiểm tra tối thiểu do server xử lý theo Điều 8.3.
- **API mới:** `GET/POST /api/graduation-check` (POST lưu phần tự xác nhận vào `graduation_self.json` của từng người dùng).
- **Sửa `academic_simulator.validate_plan` (Điều 8.3):** mức tối thiểu 8 TC/kỳ không áp dụng khi tổng tín chỉ còn lại
  chưa học (tính đến đầu kỳ đó) ít hơn 8. Trước đây kỳ nào dưới 8 TC cũng bị báo lỗi nên test
  `test_valid_prerequisite_across_semesters` trượt. Mức tối đa 16 TC giữ nguyên.
- Test mới: `test_graduation_check.py` (13 test) và 4 test cho quy tắc tối thiểu tín chỉ.

## Cảnh báo học vụ theo bảng điểm từng học kỳ
- **Module mới `hoc_vu.py`:** đọc khung `#zone_bangdiem` của "Tra cứu điểm" (có cột "Lần học"), tính GPA từng kỳ,
  GPA tích lũy theo điểm cao nhất của từng môn (Điều 19.2.b) và các cảnh báo theo Quy chế QĐ 1062/QĐ-ĐHP-ĐT.
  Kiểm tra trên dữ liệu thật: GPA tích lũy 2,76 / 103 TC, khớp QLĐT.
- **Học lại vượt 5% (Điều 27.7):** đếm mọi môn từng bị F ở bất kỳ lần học nào (kể cả đã học lại và đạt),
  so với 5% tổng tín chỉ chương trình. Chọn mục tiêu Giỏi/Xuất sắc → hiện cảnh báo ngay, nêu hạng sẽ bị giảm xuống.
  Cách đọc "học phần phải đăng ký học lại" cần xác nhận với Phòng Đào tạo.
- **Cảnh báo học tập (Điều 21):** GPA kỳ (<0,8 kỳ đầu / <1,0 các kỳ sau), GPA tích lũy theo trình độ năm,
  nợ đọng >24 TC, số lần cảnh báo liên tiếp. Trình độ năm tính theo M = tổng TC / 4 (giả định chương trình 4 năm).
- `roadmap.json` có thêm khóa `hoc_vu`; `/api/advisor` trả thêm `retake.courses/downgraded_to/over_limit` và `academic`.
- Dữ liệu crawl cũ (khung bảng điểm rỗng) vẫn chạy: `history_complete=false` và có cảnh báo yêu cầu cập nhật dữ liệu.
- Test mới: `test_hoc_vu.py` (7 test).

## Hỗ trợ học tập (gộp công cụ, sửa GPA & tín chỉ)
- **Giao diện còn 4 mục:** Tổng quan · Hỗ trợ học tập · Môn học · Cập nhật dữ liệu QLĐT. Bỏ các mục riêng
  "Gợi ý kỳ tới", "Mô phỏng kế hoạch", "GPA & Tốt nghiệp".
- **Hỗ trợ học tập theo 3 bước:** (1) chọn mục tiêu Khá/Giỏi/Xuất sắc/tùy chỉnh 2.00–4.00 →
  (2) phân tích hiện tại → (3) công cụ: Gợi ý kỳ tới · Học cải thiện · Mô phỏng kế hoạch · Lộ trình tốt nghiệp.
  Chưa chọn mục tiêu thì chưa hiện công cụ. Thanh kết quả luôn phản ánh mọi lựa chọn cùng lúc.
- **Học cải thiện (mới):** chọn môn đã học điểm ≤ B, nhập điểm dự kiến. Điểm mới THAY điểm cũ của đúng môn đó:
  mẫu số GPA và tín chỉ tích lũy không đổi; chỉ tính vào tải học kỳ tới. Điểm mới không cao hơn điểm cũ bị bỏ qua.
  Ngưỡng và quy tắc nằm ở đầu `advisor.py` (`IMPROVE_MAX_POINT`, `IMPROVE_REQUIRE_HIGHER`) — đối chiếu quy chế Phenikaa.
- **Sửa tín chỉ còn lại:** trước đây lấy từ tổng tín chỉ của các môn planner xếp được (vd 31 TC dù còn thiếu 62).
  Nay tính theo khối của chương trình đào tạo (tín bắt buộc − đã tích lũy). Tách rõ: còn thiếu để tốt nghiệp /
  đang học / dự kiến học kỳ tới / chưa xếp được vào lộ trình.
- **Sửa GPA cần đạt:** `(mục tiêu × (TC đã tính GPA + TC còn lại) − điểm chất lượng hiện có) / TC còn lại`. Nếu > 4.0 báo
  "không khả thi theo phương án hiện tại" và chỉ ra mức tối đa đạt được khi cải thiện môn điểm thấp.
- Mọi tính toán gom vào `advisor.py` + `POST /api/advisor` (một điểm tính duy nhất): đổi mục tiêu / môn cải thiện /
  môn kỳ tới / điểm dự kiến đều tính lại GPA và lộ trình đồng bộ. Bỏ `/api/suggestions_next_semester`, `/api/graduation_plan`.
- Planner không còn xếp môn không thuộc khối nào (vd "Tự chọn tự do N") vào lộ trình.
- Môn học: thêm tab "Đã học" (có điểm) và "Học lại"; ô tìm kiếm tra cứu trên mọi nhóm.
- Cập nhật dữ liệu QLĐT thành mục riêng, hiện thời điểm cập nhật gần nhất.
- Xóa `app.js` ở thư mục gốc (bản sao lệch, server chỉ phục vụ `frontend/`); banner `canh_bao_du_lieu` đã gộp vào `frontend/app.js`.
- Thêm `test_advisor.py` (12 test: công thức GPA, thay điểm, không cộng tín chỉ, mô phỏng, khối tự chọn).

## Bản sửa mới nhất
- **Sửa lỗi tổng tín chỉ 189 (đúng phải là tổng bắt buộc, vd 134).** `tin_chi_toan_khoa`
  giờ lấy từ cột "Tổng số tín bắt buộc" của bảng Khối kiến thức (`#tblTongHopDiem`),
  cộng từ dữ liệu gốc *trước* `chuan_hoa_khoi_summary()`. Thứ tự dự phòng:
  bảng khối → `#iTongSoTinChi` → cộng mọi môn (có cảnh báo). Thêm
  `nguon_tin_chi_toan_khoa` và `canh_bao_du_lieu` vào `roadmap.json`; cảnh báo hiện
  ở `/api/summary`.
- `load_khoi_summary` bỏ qua dòng "Tổng" cuối bảng.
- Gom logic loại môn đang học khỏi gợi ý về một hàm `make_current_filter()`
  (build_roadmap.py), dùng chung cho build_roadmap, planner và endpoint.
- `/api/refresh` trả `success: false` + HTTP 500 khi build lỗi (trước đây luôn báo thành công).
- **Gộp planner:** thuật toán xếp kỳ chuyển sang `planner.py` (nguồn duy nhất). Web, CLI và
  `goi_y_ky_toi` trong `roadmap.json` cùng dùng nó; bỏ `suggest_next_semester()` cũ.
  Kết quả web (gợi ý kỳ tới + lộ trình GPA) được kiểm tra giống hệt bản trước.
  Hệ quả: gợi ý của CLI giờ đồng nhất với web (có xét khối tự chọn), khác thứ tự ưu tiên cũ.
- Bỏ route `/api/suggestions` (frontend không gọi).
- Frontend: banner hiển thị `canh_bao_du_lieu` ở tab Tổng quan.
- Thêm `tests/` (dữ liệu mẫu ẩn danh + 5 test cho phần parse/tổng tín chỉ/lọc môn).

## Lịch sử các bản trước
### Lọc môn đang học khỏi gợi ý kỳ tới
Mọi học phần `DANG_KY_KY_NAY` không xuất hiện trong gợi ý kỳ tiếp theo; lọc theo mã và tên;
áp dụng cho planner nhiều kỳ và API gợi ý kỳ tới; vẫn hiện ở mục "Đang học kỳ này".

### GPA & Lộ trình tốt nghiệp
Gợi ý kỳ tới theo 3 cường độ (Học vừa 8–10, Học nhiều 11–13, Hỏa tốc 14–16 TC); mục tiêu
Khá 2.50 / Giỏi 3.20 / Xuất sắc 3.60; GPA trung bình cần đạt cho phần còn lại; điểm chữ
tham khảo từng môn; cảnh báo ngưỡng 5% tín chỉ học lại. Kế hoạch chỉ mang tính tham khảo —
việc mở lớp thực tế phụ thuộc kế hoạch đào tạo của trường.

### Giao diện rút gọn
Bỏ menu Tiến độ riêng; mục Môn học mặc định "Đang học kỳ này" (đang học / có thể đăng ký /
bị khóa / tự chọn đã đủ); tab Bị khóa hiện tên môn cần hoàn thành trước; Gợi ý kỳ tới chỉ
hiện tên + tín chỉ; GPA & Tốt nghiệp giữ 3 số chính, lộ trình thu gọn theo từng kỳ.

### Đăng nhập (login fix v2/v3)
Chỉ tự bấm "No" ở màn *Stay signed in?* khi có marker `#KmsiDescription` (không bấm nhầm
`#idBtn_Back` ở bước khác của Microsoft). Chỉ bấm "Cổng sinh viên" khi thật sự thấy nhãn đó;
không bao giờ thao tác trên `login.aspx` hay trang Microsoft.

### Đa người dùng + Chrome từ xa trên VPS
Dữ liệu mỗi trình duyệt tại `data/users/<client_id>/`; mỗi trình duyệt tối đa 1 job crawl.
Với `PHENIKAA_REMOTE_BROWSER=1`: mỗi job có Xvfb + Chrome + x11vnc riêng (chỉ bind localhost),
noVNC qua `/ws/vnc/<job_id>` có kiểm tra session sở hữu; xong/lỗi thì dọn tiến trình.
Production: gunicorn 1 worker + nhiều threads vì job nằm trong RAM. Xem `DEPLOY_VPS.md`.

## Academic Simulator (2026-10-15)
- Added a multi-semester plan validator with prerequisite ordering checks, duplicate-course checks, and warnings for courses that are still in progress.
- Added session-scoped APIs to simulate, create, update, list, and delete study plans.
- Added the “Mô phỏng tốt nghiệp” UI with semester course selection, validation results, saved plans, and comparison of multiple plans.
- Added automated tests for the simulator and pinned pandas HTML parsing to the lxml backend so the project does not depend on html5lib being selected implicitly.
- Added `requirements.txt` because the Dockerfile already expects it.


## Academic Simulator UI simplification
- Replaced the large multi-select box with a searchable list of checkbox course cards.
- Shows selected credits and clearly distinguishes prerequisite eligibility from confirmed real-world course offerings.
- Kept blocked courses collapsed to reduce visual clutter.

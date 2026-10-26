# Giao diện Hỗ trợ học tập (bản tối giản, từng bước)

Luồng mới trong mục **Hỗ trợ học tập**:

1. **Chọn mục tiêu** bằng thanh kéo GPA (2.00–4.00) hoặc bấm mốc Khá 2.50 / Giỏi 3.20 / Xuất sắc 3.60.
   Chưa chọn thì chưa hiện gì khác. Đổi mục tiêu sẽ quay về bước 2 và chọn lại môn học lại.
2. **Kết luận** một câu + một nút, theo 3 trường hợp:
   - đạt được → "Xem môn học kỳ tới"
   - chỉ đạt khi học cải thiện → "Xem môn nên học lại"
   - không khả thi → gợi ý mục tiêu thấp hơn
   Các số chi tiết nằm trong "Xem chi tiết".
3. **Môn nên học lại**: hệ thống tự chọn sẵn ÍT môn nhất (backend `advisor.recommend_improvements`),
   người dùng chỉ cần bỏ bớt/thêm. Dòng cố định phía trên: GPA dự kiến / mục tiêu.
4. **Môn học kỳ tới**: chọn số tín chỉ (8–10 / 11–13 / 14–16), danh sách môn tick sẵn, tổng tín chỉ.
   Thay cho tab Mô phỏng cũ (mặc định mỗi môn mới dự kiến đạt B).
5. **Lộ trình**: danh sách kỳ thu gọn, dự kiến tốt nghiệp, lưu ý tham khảo.

## Thay đổi code
- `advisor.py`: thêm `recommend_improvements(data, base)`.
  Ưu tiên học lại lên A sao cho phần còn lại cần ≤ 3.5; nếu chưa đủ thì lên A+ sao cho cần ≤ 4.0.
- `server.py`: `/api/advisor` trả thêm `recommend`.
- `frontend/index.html`, `app.js`, `style.css`: viết lại phần `supportSection` (class `wz-*`).
  CSS cũ của các thẻ/tab không còn dùng; có thể xóa dần.
- `test_advisor.py`: 3 test mới cho `recommend_improvements`.

## Lưu ý
- Điểm dự kiến sau học lại cố định là A (hoặc A+) cho mọi môn; không còn chọn điểm riêng từng môn.
- Bỏ khả năng tìm kiếm/thêm môn bất kỳ ở bước kỳ tới ngoài danh sách "Thêm môn khác".

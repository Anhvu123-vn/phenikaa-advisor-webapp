# Login fix v2

Sửa lỗi sau khi đăng nhập Microsoft xong lại quay về `login.aspx` và crawler tiếp tục đếm ngược.

Nguyên nhân ở bản trước: code coi cứ thấy `#idBtn_Back` là màn **Stay signed in?** rồi tự bấm. Microsoft dùng `#idBtn_Back` ở nhiều bước khác nhau, nên có thể bấm nhầm nút Back/No khi chưa ở KMSI và phá luồng SSO.

Bản này chỉ tự bấm **No** khi xác nhận có marker riêng `#KmsiDescription` (hoặc tiêu đề KMSI rõ ràng). Các bước Microsoft khác hoàn toàn không bị crawler bấm vào.

Nếu đăng nhập thành công, log phải chuyển sang:

`[OK] Đăng nhập thành công!`

sau đó:

`Đang lấy danh sách menu...`

## v3 - màn chọn Cổng sinh viên
Sau Microsoft SSO, Phenikaa có thể đưa người dùng tới `index.aspx#dashboard` nhưng đây vẫn là màn chọn cổng (Cổng sinh viên / Cổng cán bộ). Bản trước đã chặn mọi auto-click khi URL chứa `/congsinhvien/`, vì vậy crawler đứng chờ dù đăng nhập đã thành công.

Bản v3 chỉ bấm khi thực sự thấy nhãn **Cổng sinh viên**, sau đó mới chờ menu học vụ render. Trang `login.aspx` và các trang Microsoft vẫn bị loại trừ để tránh vòng lặp đăng nhập.


## Cập nhật: chỉ 1 tab QLĐT + tự bấm "Có" ở "Duy trì đăng nhập?"

- Mode `cdp`: trước đây Chrome được khởi chạy kèm `DASHBOARD_URL` (tab 1) rồi code lại `context.new_page()` (tab 2) -> 2 tab QLĐT.
  Nay Chrome mở `about:blank` và `_acquire_single_cdp_page()` dùng lại đúng 1 tab (đóng tab QLĐT/tab trống thừa trong Chrome riêng của Advisor).
  Crawl xong không đóng tab cuối cùng để cửa sổ Chrome riêng không bị tắt.
- Mode `manual/remote`: khi nhận cookie `.Auth` không mở thêm tab mới, dùng lại tab đang theo dõi.
- Màn Microsoft "Duy trì đăng nhập?" (KMSI): tự tick "Không hiển thị lại thông báo này" rồi bấm **Có** (`#idSIButton9`)
  (trước đây bấm Không). Chỉ bấm khi xác nhận đúng màn KMSI. Áp dụng cho cả mode cdp / profile / manual / password.

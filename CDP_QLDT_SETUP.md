# Advisor + Chrome CDP (Windows/local)

## Cách dùng
1. Cài Google Chrome nếu máy chưa có.
2. Chạy Advisor theo cách cũ (`python server.py` hoặc file khởi chạy đang dùng) và mở `http://localhost:5000`.
3. Bấm **Cập nhật dữ liệu từ QLĐT**.
4. Lần đầu, Chrome riêng của Advisor sẽ mở. Đăng nhập QLĐT trực tiếp trong cửa sổ đó và hoàn tất xác thực nếu trường yêu cầu. Không nhập mật khẩu vào Advisor.
5. Nếu lần đầu chưa crawl được do bạn chưa đăng nhập kịp, để Chrome đó đăng nhập xong rồi bấm cập nhật lại.
6. Những lần sau, bấm cập nhật. Advisor sẽ tái sử dụng profile riêng và phiên đăng nhập còn hiệu lực. Nếu QLĐT hết phiên, đăng nhập lại trong cửa sổ Chrome này.

## Cách hoạt động
- Chrome của Advisor dùng thư mục profile riêng `.qldt_chrome_profile/`, độc lập với Chrome cá nhân.
- CDP lắng nghe ở `127.0.0.1:9223` (chỉ trên máy local), không mở cổng debug ra mạng LAN/Internet.
- Sau khi crawl, Advisor chỉ đóng tab crawl; cửa sổ Chrome riêng và profile vẫn được giữ lại để dùng lần sau.
- Không xóa `.qldt_chrome_profile/` nếu muốn giữ phiên đăng nhập. Không chia sẻ thư mục này vì nó có thể chứa cookie phiên đăng nhập.
- Có thể đóng cửa sổ Chrome riêng của Advisor khi không dùng; lần cập nhật sau Advisor sẽ mở lại profile đó.

## Khắc phục lỗi
- Nếu không tìm thấy Chrome: cài Google Chrome rồi khởi động lại Advisor.
- Nếu CDP không sẵn sàng: đóng cửa sổ Chrome riêng của Advisor, đợi vài giây rồi bấm cập nhật lại.
- Nếu đăng nhập hết hạn hoặc menu không xuất hiện: đăng nhập thủ công trong Chrome riêng, sau đó bấm cập nhật lại.
- Chrome cá nhân đang dùng không bị đóng hoặc điều khiển bởi chức năng này.

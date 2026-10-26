# Phenikaa Advisor — VPS Remote Chrome

Bản này giữ crawler Playwright hiện tại nhưng khi chạy trên VPS sẽ tạo **một Chrome riêng cho từng crawl job** và hiển thị Chrome đó ngay trong modal của Phenikaa Advisor.

## Luồng hoạt động

1. Sinh viên mở Phenikaa Advisor.
2. Bấm **Crawl dữ liệu**.
3. Backend tạo riêng cho phiên đó: `Xvfb -> Fluxbox -> Chrome -> x11vnc`.
4. Màn hình Chrome được truyền về web bằng noVNC/WebSocket.
5. Sinh viên tự đăng nhập QLDT ngay trong khung Chrome.
6. Crawler phát hiện đăng nhập thành công và tự lấy 3 trang như trước.
7. `roadmap.json` được lưu trong `data/users/<client_id>/` riêng của sinh viên.
8. Crawl xong, Chrome/VNC của job được tắt và dashboard tự hiện.

## Điểm bảo mật cần hiểu đúng

- Phenikaa Advisor **không có form username/password QLDT** và code không lưu mật khẩu.
- Tuy nhiên Chrome đang chạy trên VPS, nên thao tác bàn phím/chuột phải được truyền qua kết nối remote tới VPS để điều khiển Chrome. Vì vậy chỉ triển khai trên VPS bạn kiểm soát và nên dùng HTTPS khi public.
- VNC chỉ bind `127.0.0.1`; người ngoài không truy cập trực tiếp cổng VNC. WebSocket `/ws/vnc/<job_id>` kiểm tra Flask session để chỉ browser sở hữu job mới nối được.

## Cài nhanh trên Ubuntu VPS

Cài Docker + Compose plugin nếu VPS chưa có:

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2 unzip
sudo systemctl enable --now docker
```

Giải nén source, vào thư mục project rồi chạy:

```bash
chmod +x deploy_vps.sh
./deploy_vps.sh
```

Script sẽ tự tạo `.env` với secret ngẫu nhiên và chạy:

```bash
docker compose up -d --build
```

Mở:

```text
http://IP-VPS/
```

Nếu VPS bật UFW:

```bash
sudo ufw allow 80/tcp
```

Xem log:

```bash
docker compose logs -f app
```

Dừng web:

```bash
docker compose down
```

## Kiểm tra đúng chức năng

- Vào web bằng Chrome thường -> bấm Crawl -> phải xuất hiện màn hình Chrome QLDT trong modal.
- Đăng nhập QLDT trong khung đó -> crawler phải tự chạy tiếp.
- Khi xong -> modal tự đóng -> dashboard hiện dữ liệu.
- Mở cửa sổ ẩn danh -> phải là user mới, chưa thấy roadmap của Chrome thường.

## Khi dùng domain + HTTPS

Sau khi reverse proxy/domain đã có HTTPS, đổi trong `.env`:

```env
PHENIKAA_COOKIE_SECURE=1
```

rồi restart:

```bash
docker compose up -d
```

Nginx mẫu trong `deploy/nginx.conf` đã cấu hình WebSocket cho noVNC và tắt buffering cho SSE crawl logs. File hiện tại chỉ listen HTTP port 80; TLS/certificate phụ thuộc domain của bạn nên chưa hard-code.

## Tài nguyên VPS

Mỗi crawl đang đăng nhập chạy một Chromium + Xvfb + x11vnc. Nếu nhiều sinh viên crawl đồng thời, RAM tăng theo số phiên. Nên bắt đầu test với ít user và theo dõi:

```bash
docker stats
```

Nếu tài khoản Microsoft/Phenikaa chặn đăng nhập từ IP datacenter/VPS theo chính sách của trường thì ứng dụng không thể tự vượt qua chính sách đó.

## Lưu ý khi dùng form nhập mật khẩu
Sinh viên nhập mã sinh viên + mật khẩu ngay trên web, nên **phải chạy qua HTTPS** (domain + chứng chỉ), sau đó đặt
`PHENIKAA_COOKIE_SECURE=1`. Không nên dùng form này khi truy cập HTTP trực tiếp qua IP vì mật khẩu sẽ đi không mã hóa.

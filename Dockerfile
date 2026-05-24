# Sử dụng image Python bản nhẹ
FROM python:3.11-slim

# Cập nhật hệ thống và cài đặt các thư viện lõi hỗ trợ xử lý Audio (PyAudio, STT, FFmpeg)
RUN apt-get update && apt-get install -y \
    gcc \
    ffmpeg \
    libasound-dev \
    portaudio19-dev \
    python3-pyaudio \
    && rm -rf /var/lib/apt/lists/*

# Thiết lập thư mục làm việc trong container
WORKDIR /app

# Copy danh sách thư viện và cài đặt trước (Tối ưu cache của Docker)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ mã nguồn của dự án (main.py, mcp_config.json, các mcp_servers...)
COPY . .

# Mở cổng 8000 để giao tiếp WebSocket
EXPOSE 8000

# Chạy Uvicorn Server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
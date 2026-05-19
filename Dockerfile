FROM python:3.11-slim

# Cài đặt thư viện hệ thống xử lý audio (cần thiết cho SpeechRecognition)
RUN apt-get update && apt-get install -y ffmpeg libasound-dev portaudio19-dev python3-pyaudio && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import asyncpg
import json
import os
from datetime import datetime

app = FastAPI(title="AI Backend - Robot Orchestrator")

# Cấu hình kết nối PostgreSQL (Thay đổi theo môi trường của bạn)
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "password")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "robot_db")
DSN = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:5432/{DB_NAME}"

# Pool kết nối database dùng chung
db_pool = None


@app.on_event("startup")
async def startup():
    global db_pool
    # Khởi tạo connection pool tới PostgreSQL khi server bật
    db_pool = await asyncpg.create_pool(DSN)
    print("✅ Đã kết nối thành công tới PostgreSQL")


@app.on_event("shutdown")
async def shutdown():
    # Đóng connection pool khi tắt server
    await db_pool.close()


async def create_chat_session(user_id: str) -> str:
    """Tạo một phiên trò chuyện mới cho bé trong bảng chat_sessions"""
    async with db_pool.acquire() as connection:
        session_id = await connection.fetchval(
            """
            INSERT INTO chat_sessions (user_id)
            VALUES ($1) RETURNING session_id
            """,
            user_id
        )
        return str(session_id)


async def save_chat_history(session_id: str, sender: str, content: str):
    """Lưu tin nhắn vào bảng chat_history"""
    async with db_pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO chat_history (session_id, sender, content)
            VALUES ($1, $2, $3)
            """,
            session_id, sender, content
        )


@app.websocket("/ws/robot/{user_id}")
async def robot_endpoint(websocket: WebSocket, user_id: str):
    """
    Đường ống TCP/IP (WebSocket) kết nối trực tiếp với ESP32
    """
    await websocket.accept()
    print(f"🔌 Robot của bé (User ID: {user_id}) đã kết nối.")

    session_id = None
    try:
        # Bước 1: Khởi tạo session lưu lịch sử chat cho phiên làm việc này
        session_id = await create_chat_session(user_id)
        print(f"📝 Đã tạo Session ID mới: {session_id}")

        while True:
            # Giai đoạn 1: Thu nhận và Hiểu ý định từ phần cứng biên
            # Lắng nghe dữ liệu từ Robot (Trong thực tế có thể là Bytes Audio cần qua STT)
            # Ở đây dùng Text để mô phỏng dữ liệu sau khi đã chuyển đổi STT
            payload = await websocket.receive_text()
            print(f"🎤 Thu nhận từ Robot: {payload}")

            # Lưu câu hỏi của trẻ vào Database
            await save_chat_history(session_id, "user", payload)

            # ---------------------------------------------------------
            # [TODO: Giai đoạn 1 & 2] - Sẽ thực hiện ở các bước tiếp theo
            # 1. Gọi API lên Đám mây AI (OpenAI/Anthropic) kèm context.
            # 2. Nếu AI trả về Tool Calling -> Gọi sang API Gateway -> MCP Server (Ví dụ: Tính toán).
            # 3. Lấy kết quả thô từ MCP, gửi lại lên AI để sinh câu nói tự nhiên.
            # ---------------------------------------------------------

            # Giả lập luồng xử lý tạm thời (Mockup AI Response)
            ai_text_response = f"Chú Robot đã nghe thấy con nói: '{payload}'. Đợi chú nối cáp sang Đám mây AI nhé!"

            # Lưu câu trả lời của Robot vào Database
            await save_chat_history(session_id, "robot", ai_text_response)

            # Giai đoạn 3: Phản hồi về Robot
            # Trả chuỗi ký tự (hoặc luồng Audio nhị phân nén sau khi qua TTS) về thiết bị biên
            await websocket.send_text(json.dumps({
                "type": "text_response",
                "message": ai_text_response
            }))

    except WebSocketDisconnect:
        print(f"❌ Robot của bé (User ID: {user_id}) đã ngắt kết nối.")
        # Cập nhật trạng thái session nếu cần thiết
        if session_id:
            async with db_pool.acquire() as connection:
                await connection.execute(
                    "UPDATE chat_sessions SET is_active = FALSE WHERE session_id = $1",
                    session_id
                )
    except Exception as e:
        print(f"⚠️ Lỗi kết nối mạng: {str(e)}")
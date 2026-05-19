from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import asyncpg
import json
import os
import httpx
from datetime import datetime
from contextlib import asynccontextmanager
import uuid


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan handler: try to create DB pool but don't crash the app if connection fails."""
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(DSN)
        print("✅ Đã kết nối thành công tới PostgreSQL")
    except Exception as e:
        # Log error but allow the application to start without a DB connection.
        print(f"⚠️ Không thể kết nối PostgreSQL: {e}")
        db_pool = None
    try:
        yield
    finally:
        if db_pool:
            await db_pool.close()
            print("✅ Đã đóng connection pool PostgreSQL")


app = FastAPI(title="AI Backend - Robot Orchestrator", lifespan=lifespan)

# Cấu hình kết nối PostgreSQL (Thay đổi theo môi trường của bạn)
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "chaidim")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "postgres")
DSN = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:5432/{DB_NAME}"

# Pool kết nối database dùng chung
db_pool = None


# (startup/shutdown handled via `lifespan` above)


async def create_chat_session(user_id: str) -> str:
    """Tạo một phiên trò chuyện mới cho bé trong bảng chat_sessions"""
    if not db_pool:
        fake_id = uuid.uuid4().hex
        print(f"ℹ️ DB not available - created local session_id={fake_id} for user={user_id}")
        return fake_id

    async with db_pool.acquire() as connection:
        session_id = await connection.fetchval(
            """
            INSERT INTO chat_sessions (user_id)
            VALUES ($1::uuid) RETURNING session_id
            """,
            user_id # asyncpg sẽ tự động map biến này vào $1::uuid
        )
        return str(session_id)


async def save_chat_history(session_id: str, sender: str, content: str):
    """Lưu tin nhắn vào bảng chat_history"""
    if not db_pool:
        # In dev mode without DB, just log the message to console and skip persistence
        print(f"ℹ️ (no DB) save_chat_history skipped: session={session_id} sender={sender} content={content}")
        return

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
            payload = await websocket.receive_text()
            print(f"🎤 Thu nhận từ Robot: {payload}")
            await save_chat_history(session_id, "user", payload)

            # ---------------------------------------------------------
            # MÔ PHỎNG ĐÁM MÂY AI (LLM CLOUD) QUYẾT ĐỊNH GỌI TOOL
            # ---------------------------------------------------------
            # Nếu trẻ hỏi một câu liên quan đến "123 cộng 456"
            if "cộng" in payload.lower() or "bằng mấy" in payload.lower():
                print("🧠 [LLM Mock] Phát hiện ý định tính toán, chuẩn bị gọi Tool...")

                # LLM trả về lệnh gọi công cụ chuẩn hóa
                tool_call_payload = {
                    "tool": "calculator",
                    "action": "add",
                    "a": 123,
                    "b": 456
                }

                # Định tuyến qua Gateway (Đẩy HTTP POST sang Port 8001)
                async with httpx.AsyncClient() as client:
                    try:
                        gw_response = await client.post(
                            "http://127.0.0.1:8001/mcp/math",
                            json=tool_call_payload
                        )
                        result_data = gw_response.json()
                        calc_result = result_data.get("result", "Lỗi")

                        # Tổng hợp câu trả lời (LLM lắp ráp dữ liệu thô thành câu tự nhiên)
                        ai_text_response = f"Kết quả là {calc_result} con nhé!"
                    except Exception as e:
                        ai_text_response = "Đường truyền tới máy tính đang bị lỗi con ạ."
            else:
                ai_text_response = f"Chú Robot đã nghe thấy con nói: '{payload}'."

            # Lưu và trả kết quả về Robot
            await save_chat_history(session_id, "robot", ai_text_response)
            await websocket.send_text(json.dumps({
                "type": "text_response",
                "message": ai_text_response
            }))
    except WebSocketDisconnect:
        print(f"❌ Robot của bé (User ID: {user_id}) đã ngắt kết nối.")
        # Cập nhật trạng thái session nếu cần thiết
        if session_id and db_pool:
            async with db_pool.acquire() as connection:
                await connection.execute(
                    "UPDATE chat_sessions SET is_active = FALSE WHERE session_id = $1",
                    session_id
                )
    except Exception as e:
        print(f"⚠️ Lỗi kết nối mạng: {str(e)}")


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host=host, port=port, log_level="info")
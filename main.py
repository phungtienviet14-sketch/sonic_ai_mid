from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import asyncpg
import json
import os
import uuid
import httpx
from datetime import datetime
from contextlib import asynccontextmanager

# 1. Thêm 2 dòng này để nạp biến môi trường từ file .env
from dotenv import load_dotenv
load_dotenv()

# Thư viện Google GenAI SDK mới
from google import genai
from google.genai import types

# 2. Khởi tạo client (Lúc này nó sẽ tự tìm thấy GEMINI_API_KEY)
gemini_client = genai.Client()

# Định nghĩa Tool/Function Calling cho LLM biết hệ thống có gì
calculator_tool = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="calculator",
            description="Công cụ thực hiện phép tính toán học cơ bản. LUÔN sử dụng công cụ này khi người dùng yêu cầu cộng hoặc trừ các con số.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "action": types.Schema(type="STRING",
                                           description="Loại phép tính: truyền vào 'add' nếu là phép cộng, 'subtract' nếu là phép trừ"),
                    "a": types.Schema(type="NUMBER", description="Số hạng thứ nhất"),
                    "b": types.Schema(type="NUMBER", description="Số hạng thứ hai"),
                },
                required=["action", "a", "b"]
            )
        )
    ]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(DSN)
        print("✅ Đã kết nối thành công tới PostgreSQL")
    except Exception as e:
        print(f"⚠️ Không thể kết nối PostgreSQL: {e}")
        db_pool = None
    try:
        yield
    finally:
        if db_pool:
            await db_pool.close()
            print("✅ Đã đóng connection pool PostgreSQL")


app = FastAPI(title="AI Backend - Robot Orchestrator", lifespan=lifespan)

# Cấu hình Database
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "chaidim")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "postgres")
DSN = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:5432/{DB_NAME}"
db_pool = None


async def create_chat_session(user_id: str) -> str:
    if not db_pool:
        fake_id = uuid.uuid4().hex
        return fake_id
    async with db_pool.acquire() as connection:
        session_id = await connection.fetchval(
            "INSERT INTO chat_sessions (user_id) VALUES ($1::uuid) RETURNING session_id", user_id
        )
        return str(session_id)


async def save_chat_history(session_id: str, sender: str, content: str):
    if not db_pool: return
    async with db_pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO chat_history (session_id, sender, content) VALUES ($1, $2, $3)",
            session_id, sender, content
        )


@app.get("/")
async def health_check():
    return {"status": "AI Backend is running with Gemini 2.5 Flash",
            "database": "connected" if db_pool else "disconnected"}


@app.websocket("/ws/robot/{user_id}")
async def robot_endpoint(websocket: WebSocket, user_id: str):
    await websocket.accept()
    print(f"🔌 Robot của bé (User ID: {user_id}) đã kết nối.")
    session_id = None

    try:
        session_id = await create_chat_session(user_id)

        # Khởi tạo một phiên Chat không đồng bộ (aio) với Gemini cho riêng bé này
        # Hệ thống cung cấp System Prompt và gài sẵn calculator_tool
        chat = gemini_client.aio.chats.create(
            model="gemini-2.5-flash",
            config=types.GenerateContentConfig(
                temperature=0.7,
                system_instruction="Bạn là chú Robot thông minh, vui vẻ và thân thiện đang nói chuyện với trẻ em. Khi trẻ em hỏi bài tập toán, bắt buộc phải dùng công cụ để tính, không tự nhẩm. Hãy trả lời ngắn gọn, xưng là 'Chú Robot' và gọi bé là 'con'.",
                tools=[calculator_tool]
            )
        )

        while True:
            payload = await websocket.receive_text()
            print(f"🎤 Thu nhận từ Robot: {payload}")
            await save_chat_history(session_id, "user", payload)

            print("🧠 Đang gửi lên Đám mây AI (Gemini)...")
            # Giai đoạn 1: Gửi nguyên văn câu hỏi lên LLM
            response = await chat.send_message(payload)

            # Giai đoạn 2: LLM phân tích và quyết định có gọi Tool không
            if response.function_calls:
                for tool_call in response.function_calls:
                    if tool_call.name == "calculator":
                        print(f"⚙️ [Gemini] Yêu cầu gọi công cụ Tính toán với tham số: {tool_call.args}")

                        tool_payload = {
                            "tool": "calculator",
                            "action": tool_call.args.get("action"),
                            "a": tool_call.args.get("a"),
                            "b": tool_call.args.get("b")
                        }

                        # Định tuyến qua Gateway (Bắn HTTP POST)
                        async with httpx.AsyncClient() as http_client:
                            try:
                                gw_response = await http_client.post("http://127.0.0.1:8001/mcp/math",
                                                                     json=tool_payload)
                                result_data = gw_response.json()
                                calc_result = result_data.get("result", "Lỗi")
                                print(f"✅ [Gateway] Trả về kết quả: {calc_result}")
                            except Exception as e:
                                print(f"⚠️ [Gateway] Lỗi kết nối: {e}")
                                calc_result = "Lỗi đường truyền đến máy tính"

                        # Giai đoạn 3: Nạp kết quả thô ngược lại cho Gemini
                        tool_response_part = types.Part.from_function_response(
                            name="calculator",
                            response={"result": calc_result}
                        )

                        print("🧠 Đang gửi kết quả tính toán về LLM để lắp ráp câu văn...")
                        final_response = await chat.send_message(tool_response_part)
                        ai_text_response = final_response.text
            else:
                # Trẻ hỏi các câu giao tiếp bình thường (không cần tính toán)
                ai_text_response = response.text

            print(f"🤖 [Robot] Phản hồi: {ai_text_response}")
            await save_chat_history(session_id, "robot", ai_text_response)

            # Đẩy về phần cứng biên
            await websocket.send_text(json.dumps({
                "type": "text_response",
                "message": ai_text_response
            }))

    except WebSocketDisconnect:
        print(f"❌ Robot của bé (User ID: {user_id}) đã ngắt kết nối.")
        if session_id and db_pool:
            async with db_pool.acquire() as connection:
                await connection.execute("UPDATE chat_sessions SET is_active = FALSE WHERE session_id = $1", session_id)
    except Exception as e:
        print(f"⚠️ Lỗi kết nối mạng: {str(e)}")


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host=host, port=port, log_level="info")
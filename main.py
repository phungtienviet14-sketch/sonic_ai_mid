from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import asyncpg
import json
import os
import uuid
import httpx
import io
import asyncio
from datetime import datetime
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from google import genai
from google.genai import types

# --- THƯ VIỆN AUDIO ---
import edge_tts
import speech_recognition as sr

gemini_client = genai.Client()

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

db_pool = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(DSN)
        print("✅ Đã kết nối DB")
    except Exception as e:
        print(f"⚠️ Lỗi DB: {e}")
        db_pool = None
    yield
    if db_pool:
        await db_pool.close()


app = FastAPI(title="AI Backend - Robot Orchestrator", lifespan=lifespan)

DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "chaidim")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "postgres")
DSN = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:5432/{DB_NAME}"


# ==========================================
# CÁC HÀM XỬ LÝ AUDIO BẤT ĐỒNG BỘ
# ==========================================
def sync_stt(audio_bytes: bytes) -> str:
    """Hàm đồng bộ chạy ngầm: Dịch Byte Audio (chuẩn WAV) thành Text"""
    recognizer = sr.Recognizer()
    try:
        # Giả định ESP32 gửi lên mảng byte của file định dạng WAV
        with sr.AudioFile(io.BytesIO(audio_bytes)) as source:
            audio = recognizer.record(source)
        # Sử dụng Google Web Speech API miễn phí
        text = recognizer.recognize_google(audio, language="vi-VN")
        return text
    except sr.UnknownValueError:
        return "Chú Robot không nghe rõ con nói gì."
    except Exception as e:
        print(f"⚠️ Lỗi STT: {e}")
        return ""


async def audio_to_text(audio_bytes: bytes) -> str:
    """Đưa hàm STT vào thread pool để không block server FastAPI"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, sync_stt, audio_bytes)


async def text_to_audio_bytes(text: str) -> bytes:
    """Dùng edge-tts tạo luồng Audio MP3 từ Text"""
    # vi-VN-HoaiMyNeural là giọng nữ AI cực kỳ truyền cảm, hợp với trẻ em
    communicate = edge_tts.Communicate(text, "vi-VN-HoaiMyNeural")
    audio_data = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data.extend(chunk["data"])
    return bytes(audio_data)


# ==========================================
# CÁC HÀM DATABASE
# ==========================================
async def create_chat_session(user_id: str) -> str:
    if not db_pool: return uuid.uuid4().hex
    async with db_pool.acquire() as connection:
        return str(
            await connection.fetchval("INSERT INTO chat_sessions (user_id) VALUES ($1::uuid) RETURNING session_id",
                                      user_id))


async def save_chat_history(session_id: str, sender: str, content: str):
    if not db_pool: return
    async with db_pool.acquire() as connection:
        await connection.execute("INSERT INTO chat_history (session_id, sender, content) VALUES ($1, $2, $3)",
                                 session_id, sender, content)


# ==========================================
# WEBSOCKET ENDPOINT CHÍNH
# ==========================================
@app.websocket("/ws/robot/{user_id}")
async def robot_endpoint(websocket: WebSocket, user_id: str):
    await websocket.accept()
    print(f"🔌 Robot của bé (User ID: {user_id}) đã kết nối.")
    session_id = await create_chat_session(user_id)

    chat = gemini_client.aio.chats.create(
        model="gemini-2.5-flash",
        config=types.GenerateContentConfig(
            temperature=0.7,
            system_instruction="Bạn là chú Robot thông minh, vui vẻ và thân thiện đang nói chuyện với trẻ em. Khi trẻ em hỏi bài tập toán, bắt buộc phải dùng công cụ để tính. Hãy trả lời ngắn gọn, xưng là 'Chú Robot' và gọi bé là 'con'.",
            tools=[calculator_tool]
        )
    )

    try:
        while True:
            # Nhận dữ liệu dưới dạng Dictionary (Có thể là Text hoặc Bytes)
            message = await websocket.receive()
            payload = ""

            # Phân loại luồng dữ liệu ESP32 gửi lên
            if "text" in message:
                payload = message["text"]
                print(f"⌨️ Thu nhận Text: {payload}")
            elif "bytes" in message:
                audio_bytes = message["bytes"]
                print(f"🎤 Thu nhận {len(audio_bytes)} bytes Audio. Đang dịch STT...")
                payload = await audio_to_text(audio_bytes)
                print(f"📝 Kết quả STT: {payload}")

            if not payload or payload == "Chú Robot không nghe rõ con nói gì.":
                await websocket.send_text(
                    json.dumps({"type": "text_response", "message": "Con nói to lên một chút nhé!"}))
                continue

            await save_chat_history(session_id, "user", payload)

            print("🧠 Đang xử lý LLM...")
            response = await chat.send_message(payload)

            # Xử lý Tool Calling (MCP Gateway)
            ai_text_response = response.text
            if response.function_calls:
                for tool_call in response.function_calls:
                    if tool_call.name == "calculator":
                        print("⚙️ Đang gọi Gateway Tính toán...")
                        tool_payload = {"tool": "calculator", "action": tool_call.args.get("action"),
                                        "a": tool_call.args.get("a"), "b": tool_call.args.get("b")}
                        async with httpx.AsyncClient() as http_client:
                            try:
                                gw_res = await http_client.post("http://127.0.0.1:8001/mcp/math", json=tool_payload)
                                calc_result = gw_res.json().get("result", "Lỗi")
                            except Exception:
                                calc_result = "Lỗi mạng"

                        tool_response_part = types.Part.from_function_response(name="calculator",
                                                                               response={"result": calc_result})
                        final_response = await chat.send_message(tool_response_part)
                        ai_text_response = final_response.text

            print(f"🤖 Trả lời Text: {ai_text_response}")
            await save_chat_history(session_id, "robot", ai_text_response)

            # BƯỚC MỚI: Trả về dữ liệu cho phần cứng
            # 1. Gửi chuỗi Text (Để ESP32 có thể hiển thị lên màn hình LCD nếu có)
            await websocket.send_text(json.dumps({
                "type": "text_response",
                "message": ai_text_response
            }))

            # 2. Dịch câu trả lời sang Audio và gửi luồng Nhị phân (Binary) xuống Robot
            print("🔊 Đang tạo luồng âm thanh (TTS)...")
            response_audio_bytes = await text_to_audio_bytes(ai_text_response)
            await websocket.send_bytes(response_audio_bytes)
            print("✅ Đã gửi Audio xuống loa Robot.")

    except WebSocketDisconnect:
        print(f"❌ Ngắt kết nối.")
        if session_id and db_pool:
            async with db_pool.acquire() as connection:
                await connection.execute("UPDATE chat_sessions SET is_active = FALSE WHERE session_id = $1", session_id)
    except Exception as e:
        print(f"⚠️ Lỗi: {e}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
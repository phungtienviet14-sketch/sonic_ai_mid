# File: main.py
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import asyncpg
import json
import os
import uuid
import io
import asyncio
import re
import traceback
from datetime import datetime
from contextlib import asynccontextmanager, AsyncExitStack

from dotenv import load_dotenv

load_dotenv()

from google import genai
from google.genai import types

# --- THƯ VIỆN AUDIO ---
import edge_tts
import speech_recognition as sr

# --- THƯ VIỆN MCP CLIENT ---
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

gemini_client = genai.Client()
db_pool = None

# ==========================================
# QUẢN LÝ PHIÊN MCP CLIENT TOÀN CỤC
# ==========================================
mcp_sessions = {}  # Lưu trữ { "tên_server": session_kết_nối }
mcp_tools_registry = []  # Danh sách các tool fetch được từ các MCP Server
exit_stack = AsyncExitStack()  # Dùng để đóng luồng stdio tự động khi tắt ứng dụng

DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "chaidim")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "postgres")
DSN = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:5432/{DB_NAME}"

# Công cụ nội bộ (Ghi nhớ sở thích) vẫn giữ nguyên
preference_tool = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="update_preferences",
            description="Sử dụng công cụ này KHI VÀ CHỈ KHI trẻ em chủ động kể về sở thích, đồ vật yêu thích, ước mơ, hoặc những thứ trẻ không thích. Trích xuất thông tin đó để hệ thống ghi nhớ.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "category": types.Schema(type="STRING",
                                             description="Phân loại sở thích (snake_case). Ví dụ: 'favorite_food', 'favorite_animal'"),
                    "value": types.Schema(type="STRING",
                                          description="Giá trị cụ thể mà trẻ nhắc đến. Ví dụ: 'xúc xích'")
                },
                required=["category", "value"]
            )
        )
    ]
)


# Hàm hỗ trợ: Chuyển đổi JSON Schema của MCP thành Schema của Google Gemini
def json_schema_to_gemini(schema: dict) -> types.Schema:
    if not schema:
        return types.Schema(type="OBJECT")

    t_map = {"string": "STRING", "integer": "INTEGER", "number": "NUMBER", "boolean": "BOOLEAN", "array": "ARRAY",
             "object": "OBJECT"}
    raw_type = schema.get("type", "object").lower()
    gemini_type = t_map.get(raw_type, "STRING")

    if gemini_type == "OBJECT":
        props = schema.get("properties", {})
        gemini_props = {k: json_schema_to_gemini(v) for k, v in props.items()}
        return types.Schema(
            type="OBJECT",
            properties=gemini_props,
            required=schema.get("required", []),
            description=schema.get("description", "")
        )
    elif gemini_type == "ARRAY":
        items = json_schema_to_gemini(schema.get("items", {}))
        return types.Schema(type="ARRAY", items=items, description=schema.get("description", ""))
    else:
        return types.Schema(type=gemini_type, description=schema.get("description", ""))


# ==========================================
# LIFESPAN - KHỞI ĐỘNG HỆ THỐNG
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool
    # 1. Khởi tạo DB
    try:
        db_pool = await asyncpg.create_pool(DSN)
        print("✅ Đã kết nối DB")
    except Exception as e:
        print(f"⚠️ Lỗi DB: {e}")
        db_pool = None

    # 2. Khởi tạo MCP Clients từ file cấu hình
    try:
        with open("mcp_config.json", "r", encoding="utf-8") as f:
            config = json.load(f)

        servers = config.get("mcpServers", {})
        for name, srv_conf in servers.items():
            if srv_conf.get("type") == "stdio":
                # Chạy Server dưới dạng subprocess
                params = StdioServerParameters(
                    command=srv_conf["command"],
                    args=srv_conf.get("args", []),
                    env=os.environ.copy()
                )

                transport = await exit_stack.enter_async_context(stdio_client(params))
                read, write = transport
                session = await exit_stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                mcp_sessions[name] = session
                print(f"✅ Đã kết nối MCP Server: {name} (stdio)")

                # Fetch toàn bộ tools của server này
                tools_res = await session.list_tools()
                for t in tools_res.tools:
                    mcp_tools_registry.append({"server": name, "tool": t})
                    print(f"   🔧 Tự động nạp công cụ: {t.name}")
    except Exception as e:
        print(f"⚠️ Lỗi khởi tạo MCP Client: {e}")
        traceback.print_exc()

    yield

    # Dọn dẹp tài nguyên
    await exit_stack.aclose()
    if db_pool:
        await db_pool.close()


app = FastAPI(title="AI Backend - Robot Orchestrator", lifespan=lifespan)


# ==========================================
# CÁC HÀM XỬ LÝ AUDIO & DATABASE (Giữ Nguyên)
# ==========================================
def sync_stt(audio_bytes: bytes) -> str:
    if not audio_bytes: return ""
    recognizer = sr.Recognizer()
    try:
        with sr.AudioFile(io.BytesIO(audio_bytes)) as source:
            audio = recognizer.record(source)
        return recognizer.recognize_google(audio, language="vi-VN")
    except sr.UnknownValueError:
        return "Chú Robot không nghe rõ con nói gì."
    except Exception as e:
        print(f"⚠️ Lỗi định dạng file Audio (STT): {e}")
        return "Chú Robot không nghe rõ con nói gì."


async def audio_to_text(audio_bytes: bytes) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, sync_stt, audio_bytes)


async def text_to_audio_bytes(text: str) -> bytes:
    clean_text = text.strip() if text else ""
    if not clean_text: return b""
    try:
        communicate = edge_tts.Communicate(clean_text, "vi-VN-HoaiMyNeural")
        audio_data = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data.extend(chunk["data"])
        return bytes(audio_data)
    except Exception as e:
        print(f"⚠️ Lỗi API TTS: {e}")
        return b""


async def get_user_profile(user_id: str) -> dict:
    if not db_pool: return None
    async with db_pool.acquire() as connection:
        try:
            record = await connection.fetchrow(
                "SELECT full_name, age, preferences, weak_points FROM users WHERE user_id = $1::uuid", user_id)
            return dict(record) if record else None
        except Exception:
            return None


async def update_user_preferences(user_id: str, category: str, value: str):
    if not db_pool: return
    async with db_pool.acquire() as connection:
        try:
            current_prefs_str = await connection.fetchval(
                "SELECT preferences::text FROM users WHERE user_id = $1::uuid", user_id)
            current_prefs = json.loads(current_prefs_str) if current_prefs_str else {}
            if category in current_prefs:
                if isinstance(current_prefs[category], list):
                    if value.lower() not in [v.lower() for v in current_prefs[category]]:
                        current_prefs[category].append(value)
                else:
                    current_prefs[category] = [current_prefs[category], value]
            else:
                current_prefs[category] = [value]
            await connection.execute("UPDATE users SET preferences = $1::jsonb WHERE user_id = $2::uuid",
                                     json.dumps(current_prefs, ensure_ascii=False), user_id)
            print(f"💾 Đã lưu sở thích: [{category}] = {value}")
            return "Đã ghi nhớ thành công"
        except Exception as e:
            return "Lỗi khi ghi nhớ"


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


async def safe_send_message(chat, payload, websocket: WebSocket, max_retries=3):
    """Gửi tin nhắn an toàn có cơ chế tự động đọc thời gian phạt của Google để chờ"""
    for attempt in range(max_retries):
        try:
            return await chat.send_message(payload)
        except Exception as e:
            error_msg = str(e)

            # Xử lý riêng lỗi Quá tải (429 Rate Limit)
            if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                # Mặc định đợi 15s nếu không tìm thấy thời gian cụ thể
                wait_time = 15.0

                # Dùng Regex để tự động trích xuất thời gian Google yêu cầu chờ
                match = re.search(r'retry in ([\d\.]+)s', error_msg)
                if match:
                    # Cộng thêm 2 giây đệm (buffer) để đảm bảo chắc chắn API đã mở lại
                    wait_time = float(match.group(1)) + 2.0

                print(
                    f"⏳ [Rate Limit] Đụng trần API. Hệ thống ngủ đông {wait_time:.1f}s (Lần thử {attempt + 1}/{max_retries})...")

                # Báo cho thiết bị biên (Robot) biết để phát âm thanh chờ, tránh làm bé tưởng máy bị treo
                if attempt == 0:
                    try:
                        await websocket.send_text(json.dumps({
                            "type": "text_response",
                            "message": "Câu hỏi này khó quá, chú Robot đang suy nghĩ, con đợi chú một tẹo nhé!"
                        }, ensure_ascii=False))
                    except Exception:
                        pass

                # Dừng tiến trình đúng bằng thời gian Google yêu cầu
                await asyncio.sleep(wait_time)

                # Nếu đã thử hết số lần cho phép mà vẫn lỗi thì báo tải nặng
                if attempt == max_retries - 1:
                    raise Exception("Hệ thống AI đang quá tải, không thể thử lại.")
            else:
                # Nếu là lỗi khác (không phải 429), văng lỗi ngay lập tức
                raise e

# ==========================================
# WEBSOCKET ENDPOINT CHÍNH
# ==========================================
@app.websocket("/ws/robot/{user_id}")
async def robot_endpoint(websocket: WebSocket, user_id: str):
    await websocket.accept()
    print(f"🔌 Robot của bé (User ID: {user_id}) đã kết nối.")
    session_id = await create_chat_session(user_id)
    user_profile = await get_user_profile(user_id)

    # Nạp công cụ động từ MCP Registry + Công cụ nội bộ
    all_gemini_tools = [preference_tool]
    dynamic_functions = []

    for item in mcp_tools_registry:
        t = item["tool"]
        dynamic_functions.append(
            types.FunctionDeclaration(
                name=t.name,
                description=t.description,
                parameters=json_schema_to_gemini(t.inputSchema)
            )
        )

    if dynamic_functions:
        all_gemini_tools.append(types.Tool(function_declarations=dynamic_functions))

    # Cấu hình Prompt
    base_prompt = "Bạn là chú Robot thông minh, vui vẻ và thân thiện đang nói chuyện với trẻ em. Hãy trả lời ngắn gọn, xưng là 'Chú Robot'."
    if user_profile:
        name = user_profile.get("full_name", "bé")
        age = user_profile.get("age", "không rõ")
        prefs = user_profile.get("preferences", "{}")
        personalized_context = f" Hiện tại, bạn đang nói chuyện với bé tên là {name}, {age} tuổi. Sở thích của bé là: {prefs}. Hãy gọi bé là '{name}' hoặc 'con'."
        system_instruction = base_prompt + personalized_context
    else:
        system_instruction = base_prompt + " Bé mới kết nối nên bạn chưa có nhiều thông tin về bé. Hãy hỏi thăm để làm quen nhé!"

    chat = gemini_client.aio.chats.create(
        model="gemini-2.5-flash",
        config=types.GenerateContentConfig(
            temperature=0.7,
            system_instruction=system_instruction,
            tools=all_gemini_tools
        )
    )

    try:
        while True:
            message = await websocket.receive()
            payload = ""

            if "text" in message:
                payload = message["text"]
                print(f"⌨️ Thu nhận Text: {payload}")
            elif "bytes" in message:
                payload = await audio_to_text(message["bytes"])
                print(f"📝 Kết quả STT: {payload}")

            if not payload or payload == "Chú Robot không nghe rõ con nói gì.":
                await websocket.send_text(
                    json.dumps({"type": "text_response", "message": "Con nói to lên một chút nhé!"}))
                continue

            await save_chat_history(session_id, "user", payload)

            try:
                response = await safe_send_message(chat, payload, websocket)
            except Exception:
                await websocket.send_text(
                    json.dumps({"type": "text_response", "message": "Hệ thống AI đang quá tải, con hỏi lại sau nhé!"},
                               ensure_ascii=False))
                continue

            ai_text_response = response.text

            # --- XỬ LÝ ĐIỀU PHỐI TOOL CALLING ĐỘNG ---
            if response.function_calls:
                for tool_call in response.function_calls:
                    print(f"⚙️ AI Yêu cầu gọi công cụ: {tool_call.name}")

                    # Trích xuất tham số an toàn
                    try:
                        args_dict = dict(tool_call.args) if tool_call.args else {}
                    except Exception:
                        args_dict = {}

                    # Xử lý Công cụ Nội bộ
                    if tool_call.name == "update_preferences":
                        db_status = await update_user_preferences(user_id, args_dict.get("category"),
                                                                  args_dict.get("value"))
                        tool_response_part = types.Part.from_function_response(name="update_preferences",
                                                                               response={"status": db_status})
                        final_response = await safe_send_message(chat, tool_response_part, websocket)
                        ai_text_response = final_response.text
                        continue

                    # Xử lý Công cụ MCP bên ngoài
                    server_name = next(
                        (item["server"] for item in mcp_tools_registry if item["tool"].name == tool_call.name), None)

                    if server_name and server_name in mcp_sessions:
                        session = mcp_sessions[server_name]
                        try:
                            # Thực thi lệnh thông qua đường truyền mcp client
                            mcp_result = await session.call_tool(tool_call.name, arguments=args_dict)

                            # Bóc tách kết quả từ mcp_result
                            texts = [c.text for c in mcp_result.content if c.type == "text"]
                            calc_result = "\n".join(texts) if len(texts) > 1 else texts[0]

                            # Cố gắng parse lại JSON nếu nó là một chuỗi JSON
                            try:
                                calc_result = json.loads(calc_result)
                            except:
                                pass

                        except Exception as e:
                            print(f"⚠️ Lỗi MCP Execution: {e}")
                            calc_result = f"Lỗi: {e}"

                        # Trả kết quả ngược lại cho mô hình AI
                        tool_response_part = types.Part.from_function_response(
                            name=tool_call.name,
                            response={"result": calc_result}
                        )
                        final_response = await safe_send_message(chat, tool_response_part, websocket)
                        ai_text_response = final_response.text
                    else:
                        print(f"⚠️ Không tìm thấy Server nào chịu trách nhiệm cho tool: {tool_call.name}")

            print(f"🤖 Trả lời Text: {ai_text_response}")
            await save_chat_history(session_id, "robot", ai_text_response)

            if not ai_text_response or not ai_text_response.strip():
                ai_text_response = "Xin lỗi con, chú đang suy nghĩ một chút nhé."

            await websocket.send_text(
                json.dumps({"type": "text_response", "message": ai_text_response}, ensure_ascii=False))

            response_audio_bytes = await text_to_audio_bytes(ai_text_response)
            if response_audio_bytes:
                await websocket.send_bytes(response_audio_bytes)

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
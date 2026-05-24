import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.config import logger
from app.core.mcp_manager import mcp_tools_registry, mcp_sessions
import app.db.crud as crud
from app.services.audio import audio_to_text, text_to_audio_bytes
from app.services.llm import LLMProviderFactory

router = APIRouter()

# Khai báo cấu trúc định nghĩa công cụ Preference Tool (Ghi nhớ sở thích)
# Định dạng bằng cấu trúc JSON Schema chuẩn tương thích với mọi LLM Provider
PREFERENCE_TOOL_RAW = {
    "name": "update_preferences",
    "description": "Sử dụng công cụ này KHI VÀ CHỈ KHI trẻ em chủ động kể về sở thích, đồ vật yêu thích, ước mơ, hoặc những thứ trẻ không thích. Trích xuất thông tin đó để hệ thống ghi nhớ.",
    "parameters": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "Phân loại sở thích (snake_case). Ví dụ: 'favorite_food', 'favorite_animal'"
            },
            "value": {
                "type": "string",
                "description": "Giá trị cụ thể mà trẻ nhắc đến. Ví dụ: 'xúc xích'"
            }
        },
        "required": ["category", "value"]
    }
}


@router.websocket("/ws/robot/{user_id}")
async def robot_endpoint(websocket: WebSocket, user_id: str):
    await websocket.accept()
    logger.info(f"🔌 [WS] Robot của bé (User ID: {user_id}) đã kết nối.")
    session_id = await crud.create_chat_session(user_id)
    user_profile = await crud.get_user_profile(user_id)

    # 1. Tạo danh sách Tools thống nhất dạng JSON Schema để chuyển qua LLM Provider
    all_tools = [PREFERENCE_TOOL_RAW]
    for item in mcp_tools_registry:
        t = item["tool"]
        all_tools.append({
            "name": t.name,
            "description": t.description,
            "parameters": t.inputSchema
        })

    # 2. Cấu hình System Instruction (Bao gồm đề xuất lồng ghép weak_points)
    base_prompt = "Bạn là chú Robot thông minh, vui vẻ và thân thiện đang nói chuyện với trẻ em. Hãy trả lời ngắn gọn, xưng là 'Chú Robot'. " \
                  "Nếu trẻ hỏi một thông tin mà bạn không biết hoặc công cụ không thể tìm ra, tuyệt đối không được nói dối hoặc bịa chuyện. " \
                  "Hãy xin lỗi khéo léo, thừa nhận mình chưa biết và lái bé sang một chủ đề khác vui hơn."
    
    if user_profile:
        name = user_profile.get("full_name", "bé")
        age = user_profile.get("age", "không rõ")
        prefs = user_profile.get("preferences", "{}")
        weak_points = user_profile.get("weak_points", "")
        
        logger.info(f"👤 [USER] Tải thành công profile của: {name} ({age} tuổi)")
        personalized_context = f" Hiện tại, bạn đang nói chuyện với bé tên là {name}, {age} tuổi. Sở thích của bé là: {prefs}. Hãy gọi bé là '{name}' hoặc 'con'."
        
        # Đề xuất cải tiến 2: Khai thác weak_points
        if weak_points:
            personalized_context += f" Điểm bé cần cải thiện là: {weak_points}. Hãy khéo léo lồng ghép những lời khuyên tích cực, động viên bé (ví dụ: dũng cảm, chăm ngoan) một cách tự nhiên."
            
        system_instruction = base_prompt + personalized_context
    else:
        logger.info(f"👤 [USER] Khách ẩn danh mới kết nối.")
        system_instruction = base_prompt + " Bé mới kết nối nên bạn chưa có nhiều thông tin về bé. Hãy hỏi thăm để làm quen nhé!"

    # 3. Nạp lịch sử chat gần đây
    recent_history = await crud.get_recent_chat_history(user_id, limit=6)
    history_list = []
    if recent_history:
        logger.info(f"📜 [CTX] Nạp {len(recent_history)} tin nhắn cũ vào ngữ cảnh.")
        for msg in recent_history:
            # map "sender" (user/robot) sang format "role" (user/model)
            role = "user" if msg["sender"] == "user" else "model"
            history_list.append({"role": role, "content": msg["content"]})

    # 4. Khởi tạo LLM Provider thông qua Factory (Đa dạng hóa mô hình linh hoạt)
    provider = LLMProviderFactory.get_provider()
    chat = provider.create_chat_session(
        system_instruction=system_instruction,
        history=history_list,
        tools=all_tools
    )

    try:
        while True:
            message = await websocket.receive()
            payload = ""

            if "text" in message:
                payload = message["text"]
                logger.info(f"⌨️ [WS] Thu nhận Text: {payload}")
            elif "bytes" in message:
                logger.debug(f"🎤 [WS] Nhận audio ({len(message['bytes'])} bytes). Đang dịch STT...")
                payload = await audio_to_text(message["bytes"])
                logger.info(f"📝 [STT] Kết quả: {payload}")

            if not payload or payload == "Chú Robot không nghe rõ con nói gì.":
                await websocket.send_text(
                    json.dumps({"type": "text_response", "message": "Con nói to lên một chút nhé!"}))
                continue

            await crud.save_chat_history(session_id, "user", payload)

            # 5. Gọi LLM gửi tin nhắn
            try:
                response = await chat.send_user_message(payload)
            except Exception:
                await websocket.send_text(
                    json.dumps({"type": "text_response", "message": "Hệ thống AI đang quá tải, con hỏi lại sau nhé!"},
                               ensure_ascii=False))
                continue

            ai_text_response = response.text

            # --- 6. XỬ LÝ ĐIỀU PHỐI TOOL CALLING (MCP & DB) ---
            if response.function_calls:
                for tool_call in response.function_calls:
                    logger.info(f"⚙️ [LLM] Yêu cầu gọi công cụ: {tool_call.name}")
                    args_dict = tool_call.args
                    logger.debug(f"   ├─ Tham số: {args_dict}")

                    # Thực thi công cụ ghi nhớ sở thích nội bộ
                    if tool_call.name == "update_preferences":
                        db_status = await crud.update_user_preferences(
                            user_id, 
                            args_dict.get("category"),
                            args_dict.get("value")
                        )
                        # Gửi phản hồi thực thi tool lại cho LLM
                        final_response = await chat.send_tool_response(
                            tool_name="update_preferences",
                            tool_call_id=tool_call.call_id,
                            response_data={"status": db_status}
                        )
                        ai_text_response = final_response.text
                        continue

                    # Thực thi công cụ MCP từ bên ngoài
                    server_name = next(
                        (item["server"] for item in mcp_tools_registry if item["tool"].name == tool_call.name), None)

                    if server_name and server_name in mcp_sessions:
                        session = mcp_sessions[server_name]
                        try:
                            logger.info(f"🚀 [MCP] Đang chuyển tiếp thực thi cho {server_name}...")
                            mcp_result = await session.call_tool(tool_call.name, arguments=args_dict)
                            texts = [c.text for c in mcp_result.content if c.type == "text"]
                            calc_result = "\n".join(texts) if len(texts) > 1 else texts[0]
                            try:
                                calc_result = json.loads(calc_result)
                            except:
                                pass
                            logger.info(
                                f"✅ [MCP] Nhận kết quả thành công từ {server_name}: {str(calc_result)[:100]}...")

                        except Exception as e:
                            logger.exception(f"❌ [MCP] Đã xảy ra lỗi khi thực thi Tool '{tool_call.name}':")
                            calc_result = f"Lỗi: {e}"

                        # Gửi phản hồi thực thi tool lại cho LLM
                        final_response = await chat.send_tool_response(
                            tool_name=tool_call.name,
                            tool_call_id=tool_call.call_id,
                            response_data={"result": calc_result}
                        )
                        ai_text_response = final_response.text
                    else:
                        logger.warning(f"⚠️ [MCP] Yêu cầu tool chưa được đăng ký: {tool_call.name}")

            # 7. KIỂM TRA VÀ XỬ LÝ DỮ LIỆU RỖNG TỪ AI
            if ai_text_response is None:
                ai_text_response = ""
            else:
                ai_text_response = str(ai_text_response).strip()

            if not ai_text_response:
                ai_text_response = "Xin lỗi con, chú Robot đang không biết trả lời câu này thế nào. Con hỏi chú chuyện khác vui hơn nhé!"

            # 8. LƯU LỊCH SỬ CHAT ROBOT VÀO DB
            logger.info(f"🤖 [LLM] Trả lời: {ai_text_response}")
            await crud.save_chat_history(session_id, "robot", ai_text_response)

            # 9. GỬI PHẢN HỒI XUỐNG ROBOT QUA WS
            await websocket.send_text(
                json.dumps({"type": "text_response", "message": ai_text_response}, ensure_ascii=False)
            )

            response_audio_bytes = await text_to_audio_bytes(ai_text_response)
            if response_audio_bytes:
                await websocket.send_bytes(response_audio_bytes)
                logger.debug("🔊 [WS] Đã gửi Audio xuống Robot.")

    except WebSocketDisconnect:
        logger.info(f"🔌 [WS] Khách (User ID: {user_id}) đã ngắt kết nối.")
        if session_id:
            await crud.update_session_status(session_id, False)
    except Exception as e:
        logger.exception("❌ [WS] Crash không mong muốn tại luồng WebSocket:")

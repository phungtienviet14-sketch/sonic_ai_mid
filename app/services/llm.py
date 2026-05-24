import re
import json
import asyncio
from fastapi import WebSocket
from google import genai
from google.genai import types
from app.core.config import logger

gemini_client = genai.Client()

# Công cụ nội bộ (Ghi nhớ sở thích)
preference_tool = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="update_preferences",
            description="Sử dụng công cụ này KHI VÀ CHỈ KHI trẻ em chủ động kể về sở thích, đồ vật yêu thích, ước mơ, hoặc những thứ trẻ không thích. Trích xuất thông tin đó để hệ thống ghi nhớ.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "category": types.Schema(type="STRING", description="Phân loại sở thích (snake_case). Ví dụ: 'favorite_food', 'favorite_animal'"),
                    "value": types.Schema(type="STRING", description="Giá trị cụ thể mà trẻ nhắc đến. Ví dụ: 'xúc xích'")
                },
                required=["category", "value"]
            )
        )
    ]
)

def json_schema_to_gemini(schema: dict) -> types.Schema:
    if not schema: return types.Schema(type="OBJECT")
    t_map = {"string": "STRING", "integer": "INTEGER", "number": "NUMBER", "boolean": "BOOLEAN", "array": "ARRAY", "object": "OBJECT"}
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


async def safe_send_message(chat, payload, websocket: WebSocket, max_retries=3):
    for attempt in range(max_retries):
        try:
            return await chat.send_message(payload)
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                wait_time = 15.0
                match = re.search(r'retry in ([\d\.]+)s', error_msg)
                if match: wait_time = float(match.group(1)) + 2.0

                logger.warning(f"⏳ [LLM] Đụng Rate Limit. Hệ thống ngủ đông {wait_time:.1f}s (Lần thử {attempt + 1}/{max_retries})...")

                if attempt == 0:
                    try:
                        await websocket.send_text(json.dumps({"type": "text_response", "message": "Câu hỏi này khó quá, chú Robot đang suy nghĩ, con đợi chú một tẹo nhé!"}, ensure_ascii=False))
                    except Exception:
                        pass

                await asyncio.sleep(wait_time)
                if attempt == max_retries - 1:
                    logger.error("❌ [LLM] Đã hết số lần thử lại (Rate Limit).")
                    raise Exception("Hệ thống AI đang quá tải, không thể thử lại.")
            else:
                logger.exception("❌ [LLM] Lỗi nghiêm trọng khi gọi Gemini API:")
                raise e

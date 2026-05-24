import re
import json
import asyncio
import httpx
from google import genai
from google.genai import types
from app.core.config import logger, LLM_PROVIDER, LLM_MODEL, LLM_API_KEY, LLM_BASE_URL

# Client Google GenAI duy nhất nếu dùng Gemini
gemini_client = None
if LLM_PROVIDER == "gemini":
    import os
    api_key = os.getenv("GEMINI_API_KEY") or LLM_API_KEY
    if not api_key:
        logger.error("❌ [LLM] Không tìm thấy GEMINI_API_KEY hoặc LLM_API_KEY trong cấu hình!")
    gemini_client = genai.Client(api_key=api_key)


class ToolCall:
    """Đại diện cho yêu cầu thực thi công cụ thống nhất giữa các LLM."""
    def __init__(self, name: str, args: dict, call_id: str = None):
        self.name = name
        self.args = args
        self.call_id = call_id  # Dùng cho OpenAI/DeepSeek Tool Response ID


class LLMResponse:
    """Đại diện cho phản hồi văn bản và danh sách các Tool Call thống nhất."""
    def __init__(self, text: str, function_calls: list[ToolCall] = None):
        self.text = text
        self.function_calls = function_calls or []


class BaseChatSession:
    """Interface đại diện cho một phiên chat duy trì lịch sử hội thoại."""
    async def send_user_message(self, message: str) -> LLMResponse:
        raise NotImplementedError

    async def send_tool_response(self, tool_name: str, tool_call_id: str, response_data: dict) -> LLMResponse:
        raise NotImplementedError


class BaseLLMProvider:
    """Interface đại diện cho nhà cung cấp dịch vụ LLM."""
    def create_chat_session(self, system_instruction: str, history: list[dict], tools: list[dict]) -> BaseChatSession:
        raise NotImplementedError


# ==========================================
# TRIỂN KHAI CHO GOOGLE GEMINI
# ==========================================

def json_schema_to_gemini(schema: dict) -> types.Schema:
    """Chuyển đổi JSON Schema chuẩn sang schema tương thích với Gemini SDK."""
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


class GeminiChatSession(BaseChatSession):
    def __init__(self, system_instruction: str, history: list[dict], tools: list[dict]):
        # Định dạng Tools sang kiểu Gemini SDK
        gemini_tools = []
        function_declarations = []
        for t in tools:
            function_declarations.append(types.FunctionDeclaration(
                name=t["name"],
                description=t.get("description", ""),
                parameters=json_schema_to_gemini(t.get("parameters"))
            ))
        if function_declarations:
            gemini_tools.append(types.Tool(function_declarations=function_declarations))

        # Định dạng Lịch sử hội thoại sang kiểu Gemini SDK
        history_array = []
        for msg in history:
            role = "user" if msg["role"] == "user" else "model"
            history_array.append(types.Content(
                role=role, 
                parts=[types.Part.from_text(text=msg["content"])]
            ))

        self.chat = gemini_client.aio.chats.create(
            model=LLM_MODEL,
            config=types.GenerateContentConfig(
                temperature=0.7, 
                system_instruction=system_instruction,
                tools=gemini_tools if gemini_tools else None
            ),
            history=history_array if history_array else None
        )

    async def send_user_message(self, message: str) -> LLMResponse:
        return await self._send_gemini_message(message)

    async def send_tool_response(self, tool_name: str, tool_call_id: str, response_data: dict) -> LLMResponse:
        tool_response_part = types.Part.from_function_response(
            name=tool_name,
            response=response_data
        )
        return await self._send_gemini_message(tool_response_part)

    async def _send_gemini_message(self, payload) -> LLMResponse:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = await self.chat.send_message(payload)
                text = response.text or ""
                function_calls = []
                if response.function_calls:
                    for fc in response.function_calls:
                        function_calls.append(ToolCall(
                            name=fc.name,
                            args=dict(fc.args) if fc.args else {},
                            call_id=None
                        ))
                return LLMResponse(text=text, function_calls=function_calls)
            except Exception as e:
                error_msg = str(e)
                if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                    wait_time = 15.0
                    match = re.search(r'retry in ([\d\.]+)s', error_msg)
                    if match: wait_time = float(match.group(1)) + 2.0
                    
                    logger.warning(f"⏳ [LLM Gemini] Rate Limit. Ngủ {wait_time:.1f}s (Lần thử {attempt + 1}/{max_retries})...")
                    await asyncio.sleep(wait_time)
                    if attempt == max_retries - 1:
                        raise Exception("Hệ thống AI của Google đang bận, vui lòng thử lại sau.")
                else:
                    logger.exception("❌ [LLM Gemini] Lỗi nghiêm trọng khi gọi Gemini API:")
                    raise e


class GeminiProvider(BaseLLMProvider):
    def create_chat_session(self, system_instruction: str, history: list[dict], tools: list[dict]) -> BaseChatSession:
        return GeminiChatSession(system_instruction, history, tools)


# ==========================================
# TRIỂN KHAI CHO OPENAI / DEEPSEEK / OLLAMA
# ==========================================

class OpenAIChatSession(BaseChatSession):
    def __init__(self, system_instruction: str, history: list[dict], tools: list[dict]):
        self.messages = []
        if system_instruction:
            self.messages.append({"role": "system", "content": system_instruction})

        # Nạp lịch sử
        for msg in history:
            role = msg["role"]
            if role == "model":
                role = "assistant"
            self.messages.append({"role": role, "content": msg["content"]})

        # Định dạng Tools theo chuẩn OpenAI
        self.tools = []
        for t in tools:
            self.tools.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters", {"type": "object", "properties": {}})
                }
            })

    async def send_user_message(self, message: str) -> LLMResponse:
        self.messages.append({"role": "user", "content": message})
        return await self._call_completions_api()

    async def send_tool_response(self, tool_name: str, tool_call_id: str, response_data: dict) -> LLMResponse:
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": json.dumps(response_data, ensure_ascii=False)
        })
        return await self._call_completions_api()

    async def _call_completions_api(self) -> LLMResponse:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LLM_API_KEY}"
        }
        
        payload = {
            "model": LLM_MODEL,
            "messages": self.messages,
            "temperature": 0.7
        }
        if self.tools:
            payload["tools"] = self.tools

        url = f"{LLM_BASE_URL.rstrip('/')}/chat/completions"

        max_retries = 3
        async with httpx.AsyncClient() as client:
            for attempt in range(max_retries):
                try:
                    logger.debug(f"🤖 [OpenAI API] Gửi request đến {url}...")
                    response = await client.post(url, headers=headers, json=payload, timeout=40.0)
                    response.raise_for_status()
                    
                    data = response.json()
                    choice = data.get("choices", [{}])[0]
                    assistant_message = choice.get("message", {})

                    # Lưu tin nhắn của Assistant vào lịch sử hội thoại nội bộ
                    self.messages.append(assistant_message)

                    text = assistant_message.get("content") or ""
                    
                    # Trích xuất Tool Calls nếu có
                    function_calls = []
                    openai_calls = assistant_message.get("tool_calls", [])
                    for tc in openai_calls:
                        f_data = tc.get("function", {})
                        try:
                            args = json.loads(f_data.get("arguments", "{}"))
                        except:
                            args = {}
                        function_calls.append(ToolCall(
                            name=f_data.get("name"),
                            args=args,
                            call_id=tc.get("id")
                        ))

                    return LLMResponse(text=text, function_calls=function_calls)

                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429 and attempt < max_retries - 1:
                        wait_time = 5.0 * (attempt + 1)
                        logger.warning(f"⏳ [LLM OpenAI] Rate limit (429). Ngủ {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue
                    logger.exception("❌ [LLM OpenAI] Lỗi HTTP:")
                    raise e
                except Exception as e:
                    logger.exception("❌ [LLM OpenAI] Lỗi nghiêm trọng không xác định:")
                    raise e


class OpenAICompatibleProvider(BaseLLMProvider):
    def create_chat_session(self, system_instruction: str, history: list[dict], tools: list[dict]) -> BaseChatSession:
        return OpenAIChatSession(system_instruction, history, tools)


class DeepSeekProvider(OpenAICompatibleProvider):
    """
    Kế thừa OpenAICompatibleProvider nhưng cung cấp cấu hình mặc định chuyên biệt cho DeepSeek V3.
    """
    def __init__(self):
        global LLM_BASE_URL
        # Nếu dùng DeepSeek mà không khai báo URL thì gán mặc định
        if not LLM_BASE_URL:
            LLM_BASE_URL = "https://api.deepseek.com"


# ==========================================
# NHÀ MÁY KHỞI TẠO (LLM PROVIDER FACTORY)
# ==========================================

class LLMProviderFactory:
    @staticmethod
    def get_provider() -> BaseLLMProvider:
        if LLM_PROVIDER == "gemini":
            return GeminiProvider()
        elif LLM_PROVIDER == "deepseek":
            if not LLM_API_KEY:
                logger.error(f"❌ [LLM] Thiếu LLM_API_KEY cho provider: {LLM_PROVIDER}")
            return DeepSeekProvider()
        elif LLM_PROVIDER in ["openai", "ollama"]:
            # Ràng buộc cơ bản
            if not LLM_API_KEY and LLM_PROVIDER != "ollama":
                logger.error(f"❌ [LLM] Thiếu LLM_API_KEY cho provider: {LLM_PROVIDER}")
            if not LLM_BASE_URL:
                logger.error(f"❌ [LLM] Thiếu LLM_BASE_URL cho provider: {LLM_PROVIDER}")
            return OpenAICompatibleProvider()
        else:
            logger.warning(f"⚠️ [LLM] Không nhận dạng được provider '{LLM_PROVIDER}', sử dụng mặc định 'gemini'.")
            return GeminiProvider()


# File: mcp_math_server.py
import sys
import logging
from fastmcp import FastMCP

# Cấu hình logging để debug
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('MathServer')

# Fix lỗi hiển thị tiếng Việt (UTF-8) trên terminal Windows (nếu có)
if sys.platform == 'win32':
    sys.stderr.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')

# Khởi tạo MCP Server
mcp = FastMCP("MathServer")


# Khai báo Tool với decorator.
# CHÚ Ý: Docstring (phần comment """...""") và Type Hints (str, float)
# sẽ được MCP tự động dịch thành Schema JSON để gửi cho Gemini.
@mcp.tool()
def calculator(action: str, a: float, b: float) -> dict:
    """
    Công cụ thực hiện phép tính toán học cơ bản. LUÔN sử dụng công cụ này khi người dùng yêu cầu cộng hoặc trừ các con số.
    """
    result = 0
    if action == "add":
        result = a + b
    elif action == "subtract":
        result = a - b
    else:
        return {"success": False, "error": "Phép tính không được hỗ trợ"}

    logger.info(f"🧮 [MCP Math] Đã tính toán: {a} {action} {b} = {result}")

    # Trả về kết quả dưới dạng dictionary
    return {"success": True, "result": result}


if __name__ == "__main__":
    # Chạy server với chuẩn stdio (Standard Input/Output) thay vì mở cổng mạng API
    mcp.run(transport="stdio")
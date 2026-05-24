# File: mcp_math_server.py
import sys
import logging
from fastmcp import FastMCP
from pydantic import Field

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-7s | %(message)s',
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger('MathServer')

if sys.platform == 'win32':
    sys.stderr.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')

mcp = FastMCP("MathServer")


@mcp.tool()
def calculator(
        action: str = Field(..., description="BẮT BUỘC truyền vào 'add' nếu là phép cộng, 'subtract' nếu là phép trừ."),
        a: float = Field(..., description="Số hạng thứ nhất"),
        b: float = Field(..., description="Số hạng thứ hai")
) -> dict:
    """
    Công cụ thực hiện phép tính toán học cơ bản. LUÔN sử dụng công cụ này khi người dùng yêu cầu cộng hoặc trừ các con số.
    """
    try:
        result = 0
        if action == "add":
            result = a + b
        elif action == "subtract":
            result = a - b
        else:
            logger.warning(f"⚠️ [MCP Math] AI truyền sai tham số action: {action}")
            return {"success": False, "error": "Phép tính không được hỗ trợ"}

        logger.info(f"🧮 [MCP Math] Đã tính toán: {a} {action} {b} = {result}")
        return {"success": True, "result": result}

    except Exception as e:
        # Bắn ra Traceback đầy đủ nếu Python code bị chết
        logger.exception("❌ [MCP Math] Lỗi nghiêm trọng trong lúc tính toán:")
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
# File: mcp_math_server.py
from fastapi import FastAPI, Request

app = FastAPI(title="MCP Server 1 - Calculator")


@app.post("/tools/calculate")
async def calculate_tool(request: Request):
    """
    Nhận JSON payload từ Gateway và thực hiện tính toán.
    Ví dụ payload: {"tool": "calculator", "action": "add", "a": 123, "b": 456}
    """
    data = await request.json()
    action = data.get("action")
    a = data.get("a", 0)
    b = data.get("b", 0)

    result = 0
    if action == "add":
        result = a + b
    elif action == "subtract":
        result = a - b

    print(f"🧮 [MCP Math] Đã tính toán: {a} {action} {b} = {result}")

    # Trả JSON API Response về lại Gateway
    return {"success": True, "result": result}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002, log_level="info")
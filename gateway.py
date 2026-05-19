# File: gateway.py
from fastapi import FastAPI, Request, HTTPException
import httpx
import uvicorn

app = FastAPI(title="API Gateway / Load Balancer")

# Bảng định tuyến (Routing Table)
ROUTES = {
    "math": "http://127.0.0.1:8002",  # Trỏ về MCP Server Tính toán
    "weather": "http://127.0.0.1:8003",  # (Dự phòng cho MCP Thời tiết sau này)
}


@app.post("/mcp/{service}")
async def route_mcp_request(service: str, request: Request):
    """Định tuyến các request tool calling tới đúng Microservice"""
    if service not in ROUTES:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy dịch vụ: {service}")

    target_url = ROUTES[service]
    payload = await request.json()

    print(f"🚦 [Gateway] Đang chuyển tiếp request tới {service} ({target_url})")

    # Forward request tới MCP Server tương ứng
    async with httpx.AsyncClient() as client:
        try:
            # Gửi thẳng payload sang MCP Server
            response = await client.post(f"{target_url}/tools/calculate", json=payload)
            return response.json()
        except Exception as e:
            print(f"⚠️ [Gateway] Lỗi kết nối tới {service}: {e}")
            raise HTTPException(status_code=502, detail="Bad Gateway")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.core.config import logger
from app.core.database import init_db_pool, close_db_pool
from app.core.mcp_manager import init_mcp_clients, close_mcp_clients
from app.api.websockets import router as websocket_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 [SYSTEM] Đang khởi động hệ thống AI Backend...")
    
    # Khởi tạo DB
    await init_db_pool()
    
    # Khởi tạo MCP Clients
    await init_mcp_clients()
    
    yield
    
    logger.info("🛑 [SYSTEM] Đang tắt hệ thống, giải phóng tài nguyên...")
    await close_mcp_clients()
    await close_db_pool()

app = FastAPI(title="AI Backend - Robot Orchestrator", lifespan=lifespan)

# Đăng ký Router
app.include_router(websocket_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, log_level="warning")

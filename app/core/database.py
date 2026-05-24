import asyncpg
from app.core.config import logger, DSN

# Global database pool
db_pool = None

async def init_db_pool():
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(DSN)
        logger.info("✅ [DB] Kết nối Database PostgreSQL thành công.")
        return db_pool
    except Exception as e:
        logger.exception("❌ [DB] Lỗi nghiêm trọng khi kết nối Database:")
        return None

async def close_db_pool():
    global db_pool
    if db_pool:
        await db_pool.close()
        logger.info("✅ [DB] Đã đóng kết nối Database.")

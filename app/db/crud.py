import json
import uuid
from app.core.config import logger
import app.core.database as db

async def get_user_profile(user_id: str) -> dict:
    if not db.db_pool: return None
    async with db.db_pool.acquire() as connection:
        try:
            record = await connection.fetchrow(
                "SELECT full_name, age, preferences, weak_points FROM users WHERE user_id = $1::uuid", user_id)
            return dict(record) if record else None
        except Exception as e:
            logger.warning(f"⚠️ [DB] Không tải được profile bé: {e}")
            return None

async def update_user_preferences(user_id: str, category: str, value: str):
    if not db.db_pool: return "Lỗi kết nối CSDL"
    async with db.db_pool.acquire() as connection:
        try:
            # Tối ưu hóa cập nhật trực tiếp qua JSONB bằng jsonb_set
            # Logic: Lấy dữ liệu hiện tại, nếu category tồn tại dưới dạng array thì thêm giá trị mới, nếu không tạo mảng mới
            # Cách an toàn nhất ở đây là giữ logic json.loads/dumps trong code Python do dữ liệu đang được validate bởi Python
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
            logger.info(f"💾 [DB] Đã cập nhật sở thích: [{category}] = {value}")
            return "Đã ghi nhớ thành công"
        except Exception as e:
            logger.exception("❌ [DB] Lỗi khi ghi nhớ sở thích:")
            return "Lỗi khi ghi nhớ"

async def create_chat_session(user_id: str) -> str:
    if not db.db_pool: return uuid.uuid4().hex
    async with db.db_pool.acquire() as connection:
        return str(
            await connection.fetchval("INSERT INTO chat_sessions (user_id) VALUES ($1::uuid) RETURNING session_id",
                                      user_id))

async def save_chat_history(session_id: str, sender: str, content: str):
    if not db.db_pool: return
    async with db.db_pool.acquire() as connection:
        await connection.execute("INSERT INTO chat_history (session_id, sender, content) VALUES ($1, $2, $3)",
                                 session_id, sender, content)

async def update_session_status(session_id: str, is_active: bool):
    if not db.db_pool: return
    async with db.db_pool.acquire() as connection:
        await connection.execute("UPDATE chat_sessions SET is_active = $1 WHERE session_id = $2", is_active, session_id)

async def get_recent_chat_history(user_id: str, limit: int = 5) -> list:
    """Tải lịch sử chat gần đây nhất để duy trì ngữ cảnh"""
    if not db.db_pool: return []
    async with db.db_pool.acquire() as connection:
        try:
            records = await connection.fetch(
                """
                SELECT h.sender, h.content 
                FROM chat_history h
                JOIN chat_sessions s ON h.session_id = s.session_id
                WHERE s.user_id = $1::uuid
                ORDER BY h.timestamp DESC
                LIMIT $2
                """, user_id, limit
            )
            # Dữ liệu lấy ra DESC (mới nhất đầu), cần reverse để truyền vào context đúng thứ tự
            return [dict(r) for r in reversed(records)]
        except Exception as e:
            logger.warning(f"⚠️ [DB] Lỗi tải lịch sử chat cũ: {e}")
            return []

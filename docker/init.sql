-- ==========================================
-- 1. BẢNG USERS (Thông tin các bé)
-- ==========================================
CREATE TABLE IF NOT EXISTS users (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name VARCHAR(100) NOT NULL,
    age INTEGER CHECK (age > 0 AND age < 100),
    preferences JSONB DEFAULT '{}'::jsonb,
    weak_points TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- ==========================================
-- 2. BẢNG CHAT_SESSIONS (Phiên trò chuyện)
-- ==========================================
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    start_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    
    -- Ràng buộc: Xóa bé thì xóa luôn các phiên chat của bé
    CONSTRAINT fk_session_user FOREIGN KEY (user_id) 
        REFERENCES users(user_id) ON DELETE CASCADE
);

-- ==========================================
-- 3. BẢNG CHAT_HISTORY (Lịch sử tin nhắn)
-- ==========================================
CREATE TABLE IF NOT EXISTS chat_history (
    message_id BIGSERIAL PRIMARY KEY,
    session_id UUID NOT NULL,
    sender VARCHAR(10) NOT NULL CHECK (sender IN ('user', 'robot')),
    content TEXT NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    -- Ràng buộc: Xóa phiên chat thì xóa luôn tin nhắn trong đó
    CONSTRAINT fk_history_session FOREIGN KEY (session_id) 
        REFERENCES chat_sessions(session_id) ON DELETE CASCADE
);

-- ==========================================
-- ĐÁNH CHỈ MỤC (INDEX) TỐI ƯU HIỆU NĂNG
-- ==========================================
-- Index để tìm nhanh tất cả phiên chat của một bé cụ thể
CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_id ON chat_sessions(user_id);

-- Index để tải cực nhanh lịch sử chat khi bé mở lại một phiên cũ
CREATE INDEX IF NOT EXISTS idx_chat_history_session_id ON chat_history(session_id);

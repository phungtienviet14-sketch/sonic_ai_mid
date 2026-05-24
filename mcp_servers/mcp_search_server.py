# File: mcp_search_server.py
import sys
import logging
import requests
from bs4 import BeautifulSoup
from fastmcp import FastMCP
from ddgs import DDGS

# Cấu hình hệ thống Logging đồng bộ với main.py
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger('SearchServer')

# Fix lỗi hiển thị tiếng Việt trên Terminal Windows
if sys.platform == 'win32':
    sys.stderr.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')

mcp = FastMCP("SearchServer")


def scrape_webpage(url: str, max_chars: int = 2500) -> str:
    """Truy cập URL và cào văn bản. Cắt bớt nếu quá dài để tiết kiệm Token LLM."""
    try:
        # Giả lập User-Agent của Chrome để không bị các trang web chặn Bot
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        # Tăng timeout lên 8 giây để các trang web chậm ở Việt Nam kịp phản hồi
        response = requests.get(url, headers=headers, timeout=8)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        paragraphs = soup.find_all('p')
        text_content = "\n".join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])

        # Cắt bớt chuỗi nếu vượt quá giới hạn Token mong muốn
        if len(text_content) > max_chars:
            text_content = text_content[:max_chars] + "\n...[Nội dung đã được cắt bớt để tối ưu hệ thống]..."

        return text_content
    except requests.exceptions.RequestException as e:
        logger.warning(f"⚠️ [MCP Search] Không thể truy cập {url} (Lỗi mạng: {e})")
        return ""
    except Exception as e:
        logger.exception(f"❌ [MCP Search] Lỗi bóc tách HTML từ {url}:")
        return ""


@mcp.tool()
def google_search(query: str) -> dict:
    """
    Sử dụng công cụ này để tìm kiếm thông tin sự kiện thực tế, kiến thức trên internet.
    """
    logger.info(f"🔍 [MCP Search] Đang tìm kiếm qua mạng: {query}")

    try:
        results = []
        # Gọi API của DuckDuckGo
        with DDGS() as ddgs:
            # Lấy tối đa 3 kết quả để phòng trường hợp top 1 là widget/quảng cáo
            responses = ddgs.text(query, max_results=3)
            if responses:
                results = list(responses)

        if not results:
            return {"success": False, "error": "Không tìm thấy thông tin trên mạng."}

        full_text = ""
        used_result = None

        # Lặp qua các kết quả để tìm một đường link hợp lệ có thể cào được nội dung
        for res in results:
            url = res.get("href")
            if not url:
                continue

            logger.info(f"🔗 [MCP Search] Đang thử cào dữ liệu từ URL: {url}")
            full_text = scrape_webpage(url, max_chars=2500)

            if full_text:
                used_result = res
                break  # Nếu cào thành công thì thoát vòng lặp ngay lập tức
            else:
                logger.warning(f"⚠️ [MCP Search] Cào thất bại, thử link tiếp theo...")

        # Nếu cả 3 link đều cào thất bại (do bị chặn hoặc không có thẻ <p>)
        # Bắt buộc phải dùng tạm đoạn mô tả ngắn (body) của kết quả đầu tiên.
        if not full_text:
            used_result = results[0]
            full_text = used_result.get("body", "Không có nội dung mô tả chi tiết.")
            logger.warning("⚠️ [MCP Search] Không cào được link nào, sử dụng tạm đoạn mô tả ngắn.")

        result_data = {
            "title": used_result.get("title", "Không có tiêu đề"),
            "source_url": used_result.get("href", ""),
            "content": full_text
        }

        logger.info(f"✅ [MCP Search] Đã lấy thành công {len(full_text)} ký tự.")
        return {"success": True, "data": result_data}

    except Exception as e:
        logger.exception("❌ [MCP Search] Lỗi nghiêm trọng khi thực hiện tìm kiếm:")
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
# File: mcp_search_server.py
import sys
import logging
import requests
from bs4 import BeautifulSoup
from fastmcp import FastMCP
from googlesearch import search

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('SearchServer')

# Fix hiển thị tiếng Việt trên Terminal Windows
if sys.platform == 'win32':
    sys.stderr.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')

mcp = FastMCP("SearchServer")


def scrape_webpage(url: str, max_chars: int = 2500) -> str:
    """Truy cập URL và cào văn bản. Cắt bớt nếu quá dài để tiết kiệm Token LLM."""
    try:
        # Giả lập trình duyệt để tránh bị chặn 403 Forbidden
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()

        # Chỉ trích xuất text từ các thẻ <p> (paragraph) để loại bỏ rác HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        paragraphs = soup.find_all('p')
        text_content = "\n".join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])

        # Cắt chuỗi để tối ưu Token (2500 ký tự ~ khoảng 500-600 tokens)
        if len(text_content) > max_chars:
            text_content = text_content[:max_chars] + "\n...[Nội dung đã cắt bớt]..."

        return text_content
    except Exception as e:
        logger.warning(f"Không thể đọc nội dung từ {url}: {e}")
        return ""


@mcp.tool()
def google_search(query: str) -> dict:
    """
    Sử dụng công cụ này để tìm kiếm thông tin sự kiện thực tế, kiến thức trên internet.
    """
    logger.info(f"🔍 [MCP Search] Đang tìm kiếm: {query}")

    try:
        # 1. Tìm Top 1 kết quả tốt nhất
        search_results = list(search(query, num_results=1, lang="vi", advanced=True))

        if not search_results:
            return {"success": False, "error": "Không tìm thấy thông tin trên mạng."}

        top_result = search_results[0]
        url = top_result.url
        title = top_result.title
        logger.info(f"🔗 [MCP Search] Đang cào dữ liệu từ URL: {url}")

        # 2. Truy cập thẳng URL để đọc nội dung
        full_text = scrape_webpage(url, max_chars=2500)

        # Nếu cào thất bại (do web chặn bot), fallback về đoạn tóm tắt ngắn của Google
        if not full_text:
            full_text = top_result.description

        result_data = {
            "title": title,
            "source_url": url,
            "content": full_text
        }

        logger.info(f"✅ [MCP Search] Gửi về LLM {len(full_text)} ký tự.")
        return {"success": True, "data": result_data}

    except Exception as e:
        logger.error(f"⚠️ [MCP Search] Lỗi tìm kiếm: {e}")
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
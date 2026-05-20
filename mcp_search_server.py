# File: mcp_search_server.py
import sys
import logging
import requests
from bs4 import BeautifulSoup
from fastmcp import FastMCP
from duckduckgo_search import DDGS  # <-- Đổi sang dùng DuckDuckGo

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-7s | %(message)s',
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger('SearchServer')

if sys.platform == 'win32':
    sys.stderr.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')

mcp = FastMCP("SearchServer")


def scrape_webpage(url: str, max_chars: int = 2500) -> str:
    """Truy cập URL và cào văn bản. Cắt bớt nếu quá dài để tiết kiệm Token LLM."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        paragraphs = soup.find_all('p')
        text_content = "\n".join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])

        if len(text_content) > max_chars:
            text_content = text_content[:max_chars] + "\n...[Nội dung đã cắt bớt]..."

        return text_content
    except Exception as e:
        logger.exception(f"⚠️ [MCP Search] Lỗi cào dữ liệu từ {url}:")
        return ""


@mcp.tool()
def google_search(query: str) -> dict:
    """
    Sử dụng công cụ này để tìm kiếm thông tin sự kiện thực tế, thời tiết, kiến thức trên internet.
    """
    logger.info(f"🔍 [MCP Search] Đang tìm kiếm qua mạng: {query}")

    try:
        # Sử dụng thư viện DuckDuckGo thay vì Google
        with DDGS() as ddgs:
            # Lấy Top 1 kết quả
            results = list(ddgs.text(query, max_results=1))

        if not results:
            return {"success": False, "error": "Không tìm thấy thông tin trên mạng."}

        top_result = results[0]
        url = top_result.get("href")
        title = top_result.get("title")
        description = top_result.get("body")

        logger.info(f"🔗 [MCP Search] Đang cào dữ liệu từ URL: {url}")

        # Truy cập vào web để đọc tin chi tiết
        full_text = scrape_webpage(url, max_chars=2500)

        # Nếu bị web chặn (Forbidden), dùng tạm đoạn mô tả ngắn gọn
        if not full_text:
            full_text = description

        result_data = {
            "title": title,
            "source_url": url,
            "content": full_text
        }

        logger.info(f"✅ [MCP Search] Đã lấy thành công {len(full_text)} ký tự.")
        return {"success": True, "data": result_data}

    except Exception as e:
        logger.exception("❌ [MCP Search] Lỗi nghiêm trọng khi thực hiện tìm kiếm:")
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
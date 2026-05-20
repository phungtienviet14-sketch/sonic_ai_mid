# File: mcp_weather_server.py
import sys
import logging
import requests
from fastmcp import FastMCP
from pydantic import Field

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-7s | %(message)s',
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger('WeatherServer')

if sys.platform == 'win32':
    sys.stderr.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')

mcp = FastMCP("WeatherServer")


@mcp.tool()
def get_current_weather(
        city: str = Field(..., description="Tên thành phố để tra cứu thời tiết. Ví dụ: 'Hà Nội', 'Đà Nẵng'")
) -> dict:
    """
    Sử dụng LUÔN công cụ này khi trẻ em hỏi về thời tiết, nhiệt độ, mưa nắng của một địa điểm bất kỳ.
    """
    logger.info(f"🌤️ [MCP Weather] Đang tra cứu thời tiết cho: {city}")
    try:
        # 1. Gọi API Geocoding để lấy tọa độ (Lat, Lon) của thành phố
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1&language=vi"
        geo_res = requests.get(geo_url, timeout=5).json()

        if not geo_res.get("results"):
            return {"success": False, "error": f"Không tìm thấy tọa độ cho thành phố {city}"}

        location = geo_res["results"][0]
        lat, lon = location["latitude"], location["longitude"]

        # 2. Gọi API Thời tiết dựa trên tọa độ
        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
        weather_res = requests.get(weather_url, timeout=5).json()

        current = weather_res.get("current_weather", {})
        temp = current.get("temperature")
        windspeed = current.get("windspeed")
        is_day = "Ban ngày" if current.get("is_day") == 1 else "Ban đêm"

        # 3. Đóng gói kết quả gửi về cho LLM
        weather_info = f"Thời tiết tại {location['name']} ({is_day}): Nhiệt độ hiện tại là {temp}°C, tốc độ gió {windspeed} km/h."
        logger.info(f"✅ [MCP Weather] Lấy dữ liệu thành công: {temp}°C")

        return {"success": True, "result": weather_info}

    except Exception as e:
        logger.exception("❌ [MCP Weather] Lỗi khi gọi API Open-Meteo:")
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
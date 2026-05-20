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

# Bảng dịch mã thời tiết quốc tế (WMO) sang tiếng Việt thân thiện với trẻ em
WMO_CODES = {
    0: "Trời trong xanh, nắng đẹp",
    1: "Trời chủ yếu là nắng", 2: "Trời có mây cụm", 3: "Trời nhiều mây u ám",
    45: "Có sương mù", 48: "Có sương mù lạnh",
    51: "Mưa phùn nhẹ", 53: "Mưa phùn vừa", 55: "Mưa phùn dày hạt",
    61: "Mưa rào nhẹ", 63: "Mưa vừa", 65: "Mưa to",
    71: "Tuyết rơi nhẹ", 73: "Tuyết rơi vừa", 75: "Tuyết rơi dày",
    80: "Mưa rào nhẹ từng cơn", 81: "Mưa rào vừa", 82: "Mưa rào rất to",
    95: "Có sấm chớp", 96: "Có sấm chớp và mưa đá", 99: "Có bão lớn và mưa đá"
}


def get_weather_desc(code):
    return WMO_CODES.get(code, "Thời tiết diễn biến thất thường")


@mcp.tool()
def get_weather(
        city: str = Field(..., description="Tên thành phố để tra cứu thời tiết. Ví dụ: 'Hà Nội', 'Đà Nẵng'")
) -> dict:
    """
    Sử dụng công cụ này tra cứu thời tiết hiện tại và dự báo thời tiết tuần sau/những ngày tới của một địa điểm.
    """
    logger.info(f"🌤️ [MCP Weather] Đang tra cứu thời tiết cho: {city}")
    try:
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1&language=vi"
        geo_res = requests.get(geo_url, timeout=5).json()

        if not geo_res.get("results"):
            return {"success": False, "error": f"Không tìm thấy tọa độ cho thành phố {city}"}

        location = geo_res["results"][0]
        lat, lon = location["latitude"], location["longitude"]

        # Gọi API lấy cả Hiện tại VÀ Dự báo 7 ngày (lấy nhiệt độ và mã thời tiết)
        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true&daily=weathercode,temperature_2m_max,temperature_2m_min&timezone=auto"
        weather_res = requests.get(weather_url, timeout=5).json()

        # Xử lý Hiện tại
        current = weather_res.get("current_weather", {})
        temp = current.get("temperature")
        current_desc = get_weather_desc(current.get("weathercode"))

        # Xử lý Dự báo tương lai
        daily = weather_res.get("daily", {})
        forecast_list = []
        if daily and "time" in daily:
            for i in range(1, min(7, len(daily["time"]))):  # Bỏ qua ngày 0 (hôm nay), lấy 6 ngày tiếp theo
                date = daily["time"][i]
                desc = get_weather_desc(daily["weathercode"][i])
                t_max = daily["temperature_2m_max"][i]
                t_min = daily["temperature_2m_min"][i]
                forecast_list.append(f"Ngày {date}: {desc} (Nhiệt độ {t_min}-{t_max}°C)")

        forecast_text = "\n".join(forecast_list)

        weather_info = (
            f"Thông tin thời tiết tại {location['name']}:\n"
            f"[HIỆN TẠI]: Nhiệt độ {temp}°C, {current_desc}.\n"
            f"[DỰ BÁO CÁC NGÀY TỚI]:\n{forecast_text}"
        )

        logger.info(f"✅ [MCP Weather] Lấy dữ liệu thành công cho {city}")
        return {"success": True, "result": weather_info}

    except Exception as e:
        logger.exception("❌ [MCP Weather] Lỗi khi gọi API Open-Meteo:")
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
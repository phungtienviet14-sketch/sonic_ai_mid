import io
import asyncio
import edge_tts
import speech_recognition as sr
from app.core.config import logger

def sync_stt(audio_bytes: bytes) -> str:
    if not audio_bytes: return ""
    recognizer = sr.Recognizer()
    try:
        with sr.AudioFile(io.BytesIO(audio_bytes)) as source:
            audio = recognizer.record(source)
        return recognizer.recognize_google(audio, language="vi-VN")
    except sr.UnknownValueError:
        return "Chú Robot không nghe rõ con nói gì."
    except Exception as e:
        logger.warning(f"⚠️ [AUDIO] Lỗi định dạng file (STT): {e}")
        return "Chú Robot không nghe rõ con nói gì."

async def audio_to_text(audio_bytes: bytes) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, sync_stt, audio_bytes)

async def text_to_audio_bytes(text: str, max_retries=2) -> bytes:
    clean_text = text.strip() if text else ""
    if not clean_text: return b""

    # Làm sạch văn bản cho loa dễ đọc
    clean_text = clean_text.replace("°C", " độ C").replace("°", " độ")

    for attempt in range(max_retries):
        try:
            communicate = edge_tts.Communicate(clean_text, "vi-VN-HoaiMyNeural")
            audio_data = bytearray()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_data.extend(chunk["data"])

            if audio_data:
                return bytes(audio_data)

        except Exception as e:
            logger.warning(f"⚠️ [AUDIO] Lỗi TTS lần {attempt + 1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(1)
            else:
                logger.error(f"❌ [AUDIO] Đã hết số lần thử lại TTS.")
                return b""

    return b""

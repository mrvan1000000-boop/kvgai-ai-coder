import uuid
import logging
from fastapi import Request

logger = logging.getLogger(__name__)

def get_client_ip(request: Request) -> str:
    return request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown").split(",")[0].strip()

def generate_user_id() -> str:
    return str(uuid.uuid4())

def clean_ai_response(text: str) -> str:
    """Removes markdown code blocks from model output."""
    if not text:
        return ""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        if lines and lines[0].strip().lower() in ("python", "py", "javascript", "js", "html", "css"):
            lines = lines[1:]
        text = "\n".join(lines)
    return text.strip()

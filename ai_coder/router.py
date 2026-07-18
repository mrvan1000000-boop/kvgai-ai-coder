import os
import uuid
import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="templates")

AVAILABLE_MODELS = ["groq:llama-3.1-8b-instant"]

@router.get("/admin/ai-coder")
async def ai_coder_page(request: Request, user_id: str | None = None):
    uid = user_id or str(uuid.uuid4())
    
    # Extremely simple context - no complex objects
    return templates.TemplateResponse("ai_coder.html", {
        "request": request,
        "user_id": uid,
        "available_models": AVAILABLE_MODELS,
        "selected_model": AVAILABLE_MODELS[0],
        "chat_history": [],          # Empty list
        "result": None,
        "task": "",
        "download": None
    })

@router.get("/")
async def root():
    return {"status": "ok", "message": "Router is working"}

print("✅ Minimal router loaded successfully")

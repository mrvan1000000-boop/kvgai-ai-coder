import os
import tempfile
import shutil
import uuid
import logging
import zipfile
import requests
import ftplib
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from supabase import create_client, Client
from cryptography.fernet import Fernet
from github import Github

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

try:
    from groq import Groq
    groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
except:
    groq_client = None

cipher = Fernet(ENCRYPTION_KEY) if ENCRYPTION_KEY else None

def encrypt_password(pwd): 
    return cipher.encrypt(pwd.encode()).decode() if cipher else pwd

def decrypt_password(enc): 
    return cipher.decrypt(enc.encode()).decode() if cipher else enc

router = APIRouter()
templates = Jinja2Templates(directory="templates")

AVAILABLE_MODELS = ["openrouter:google/gemma-4-26b-a4b-it:free", "groq:llama-3.1-8b-instant"]  # shortened for test

# === AI CALL (placeholder - replace with your full version) ===
def call_model_with_fallback(messages, model):
    return "This is a test response from AI. Replace with real call."

# === FILE HELPERS (keep your original or use this simple version) ===
def parse_files_from_ai(content: str) -> dict:
    return {"example.py": "# Test code"}

# === MAIN PAGE - FIXED ===
@router.get("/admin/ai-coder")
async def ai_coder_page(request: Request, user_id: str | None = None):
    uid = user_id or str(uuid.uuid4())
    
    # Safe chat history
    chat_history = [{"role": "assistant", "content": "Welcome! Describe your task."}]
    
    return templates.TemplateResponse("ai_coder.html", {
        "request": request,
        "user_id": uid,
        "available_models": AVAILABLE_MODELS,
        "selected_model": AVAILABLE_MODELS[0],
        "chat_history": chat_history,
        "result": None,
        "task": "",
        "download": None
    })

# Keep your other routes here (api, deploy, etc.)
# For now, add minimal /api to test

@router.post("/admin/ai-coder/api")
async def ai_coder_api(task: str = Form(...), user_id: str = Form(None)):
    return JSONResponse({
        "result": "AI processed your request: " + task[:100],
        "download_url": None,
        "history_id": "test123",
        "fixed_files": {"test.py": "# code"}
    })

# Add your full deploy, download, history routes as before...

print("Router loaded successfully")

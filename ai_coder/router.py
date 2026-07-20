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

def encrypt_password(pwd: str) -> str:
    return cipher.encrypt(pwd.encode()).decode() if cipher else pwd

def decrypt_password(enc: str) -> str:
    return cipher.decrypt(enc.encode()).decode() if cipher else enc

router = APIRouter()
templates = Jinja2Templates(directory="templates")

AVAILABLE_MODELS = [
    "groq:llama-3.1-8b-instant",
    "openrouter:google/gemma-4-26b-a4b-it:free",
]

# Простая заглушка для теста (замени на свою полную логику)
def call_model_with_fallback(messages, model):
    return "Я проанализировал ваш запрос. Вот исправленный код:\n\n--- main.py ---\nprint('Hello from AI Coder!')"

def parse_files_from_ai(content: str) -> dict:
    return {"example.py": content}

def create_fixed_zip(fixed_files: dict):
    d = tempfile.mkdtemp()
    zp = os.path.join(d, "fixed_project.zip")
    with zipfile.ZipFile(zp, "w") as zf:
        for path, content in fixed_files.items():
            full = os.path.join(d, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)
            zf.write(full, arcname=path)
    return zp

@router.get("/admin/ai-coder")
async def ai_coder_page(request: Request, user_id: str | None = None):
    uid = user_id or str(uuid.uuid4())
    return templates.TemplateResponse("ai_coder.html", {
        "request": request,
        "user_id": uid,
        "available_models": AVAILABLE_MODELS,
        "selected_model": AVAILABLE_MODELS[0],
        "chat_history": [],
        "result": None,
        "task": "",
        "download": None
    })

@router.post("/admin/ai-coder/api")
async def ai_coder_api(
    task: str = Form(...),
    file: UploadFile = File(None),
    model: str = Form(None),
    user_id: str = Form(None)
):
    uid = user_id or str(uuid.uuid4())
    ai_response = call_model_with_fallback(None, model)
    fixed_files = parse_files_from_ai(ai_response)
    fixed_zip_path = create_fixed_zip(fixed_files)
    download_url = f"/admin/ai-coder/download?path={fixed_zip_path}"

    return JSONResponse({
        "result": ai_response,
        "download_url": download_url,
        "user_id": uid,
        "history_id": "test123",
        "fixed_files": fixed_files
    })

@router.get("/admin/ai-coder/download", response_class=FileResponse)
async def download_file(path: str):
    return FileResponse(path, filename="fixed_project.zip", media_type="application/zip")

print("Router loaded successfully")

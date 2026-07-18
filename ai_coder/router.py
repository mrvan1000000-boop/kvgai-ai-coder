import os
import tempfile
import shutil
import uuid
import logging
import zipfile
import requests
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from supabase import create_client, Client

# ===== LOGGING =====
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== ENV =====
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if (SUPABASE_URL and SUPABASE_KEY) else None

router = APIRouter()
templates = Jinja2Templates(directory="templates")

AVAILABLE_MODELS = [
    "openrouter:google/gemma-4-26b-a4b-it:free",
    "openrouter:qwen/qwen3-coder:free",
]

# ===== AI =====
def call_openrouter(model: str, messages: list, timeout: int = 15):
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://dalvideo.ru",
            "X-Title": "KVG AI Studio"
        },
        json={"model": model, "messages": messages},
        timeout=timeout
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"] if data.get("choices") else None

def call_model(messages, primary_model):
    # Упрощённо: пробуем только выбранную модель
    try:
        model_name = primary_model.split(":", 1)[1] if ":" in primary_model else primary_model
        return call_openrouter(model_name, messages)
    except Exception as e:
        logger.error(f"Model error: {e}")
        return "❌ Нейросеть временно недоступна."

# ===== ФАЙЛЫ =====
def extract_zip_and_read(zip_file: UploadFile):
    temp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(temp_dir, "uploaded.zip")
    with open(zip_path, "wb") as f:
        shutil.copyfileobj(zip_file.file, f)
    files_data = {}
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(temp_dir)
    except:
        return {"error": "not_a_zip"}
    for root, _, files in os.walk(temp_dir):
        for fn in files:
            if fn == "uploaded.zip": continue
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, temp_dir)
            try:
                with open(p, encoding="utf-8") as f:
                    files_data[rel] = f.read()
            except:
                continue
    return files_data

def parse_files_from_ai(content: str) -> dict:
    files = {}
    current_path = None
    buffer = []
    for line in content.splitlines():
        if line.startswith("--- ") and line.endswith(" ---"):
            if current_path:
                files[current_path] = "\n".join(buffer)
            current_path = line[4:-4].strip()
            buffer = []
        elif current_path:
            buffer.append(line)
    if current_path:
        files[current_path] = "\n".join(buffer)
    return files

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

# ===== БД (упрощённо) =====
def save_message(user_id, role, content):
    if not supabase: return
    try:
        supabase.table("ai_coder_messages").insert({
            "user_id": user_id,
            "role": role,
            "content": content
        }).execute()
    except Exception as e:
        logger.error(f"Save message error: {e}")

def load_messages(user_id, limit=10):
    if not supabase: return []
    try:
        data = supabase.table("ai_coder_messages") \
            .select("*") \
            .eq("user_id", user_id) \
            .order("created_at", desc=False) \
            .limit(limit) \
            .execute()
        if not data.data:
            return []
        result = []
        for i in data.data:
            result.append({
                "role": i.get("role", "unknown"),
                "content": i.get("content", "")
            })
        return result
    except Exception as e:
        logger.error(f"Load messages error: {e}")
        return []

# ===== РОУТЫ =====
@router.get("/admin/ai-coder")
async def ai_coder_page(request: Request, user_id: str | None = None):
    uid = user_id or str(uuid.uuid4())
    history = load_messages(uid, limit=10)
    chat_history = []
    for msg in history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "user" and "Task:" in content:
            parts = content.split("Task:", 1)
            if len(parts) > 1:
                content = parts[1].split("\n\nSingle file")[0].strip()
        chat_history.append({"role": role, "content": content})
    
    return templates.TemplateResponse("ai_coder.html", {
        "request": request,
        "user_id": uid,
        "available_models": AVAILABLE_MODELS,
        "selected_model": AVAILABLE_MODELS[0],
        "chat_history": chat_history,
        "result": None,
        "task": "",
        "download": None,
        "custom_model": "",
        "history_id": None
    })

@router.post("/admin/ai-coder/api")
async def ai_coder_api(
    request: Request,
    task: str = Form(...),
    file: UploadFile = File(None),
    model: str = Form(None),
    custom_model: str = Form(None),
    user_id: str = Form(None)
):
    try:
        uid = user_id or str(uuid.uuid4())
        selected_model = custom_model if (model == "custom" and custom_model) else (model if model in AVAILABLE_MODELS else AVAILABLE_MODELS[0])

        context = ""
        if file and file.filename:
            res = extract_zip_and_read(file)
            if isinstance(res, dict) and "error" in res:
                file.file.seek(0)
                content = file.file.read().decode("utf-8", errors="ignore")
                context = f"File: {file.filename}\nContent:\n{content}\n\n"
            else:
                for path, content in res.items():
                    context += f"--- {path} ---\n{content}\n\n"

        full_msg = f"User ID: {uid}\nTask: {task}\n\n{context}\n\nИсправь ошибки, оптимизируй код и верни исправленные файлы в формате:\n--- path/to/file.py ---\n<исправленный код>"
        messages = [{"role": "user", "content": full_msg}]

        ai_response = call_model(messages, selected_model)

        if not ai_response or ai_response.startswith("❌"):
            return JSONResponse({"error": ai_response or "AI failed"}, status_code=503)

        save_message(uid, "user", full_msg)
        save_message(uid, "assistant", ai_response)

        fixed = parse_files_from_ai(ai_response)
        download_url = None
        if fixed:
            zip_path = create_fixed_zip(fixed)
            download_url = f"/admin/ai-coder/download?path={zip_path}"

        return JSONResponse({
            "result": ai_response,
            "download_url": download_url,
            "user_id": uid,
            "history_id": None
        })
    except Exception as e:
        logger.exception("API error")
        return JSONResponse({"error": str(e)}, status_code=500)

# ===== DOWNLOAD =====
ALLOWED_DIR = tempfile.gettempdir()

@router.get("/admin/ai-coder/download", response_class=FileResponse)
async def download_file(path: str):
    abs_path = os.path.abspath(path)
    if not abs_path.startswith(ALLOWED_DIR) or not os.path.exists(abs_path):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    return FileResponse(abs_path, filename="fixed_project.zip", media_type="application/zip")

# ===== HISTORY (упрощённо) =====
@router.get("/admin/ai-coder/history/{user_id}")
async def ai_coder_history(request: Request, user_id: str):
    if not supabase:
        return {"error": "Supabase not configured"}
    data = supabase.table("ai_coder_history").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
    return templates.TemplateResponse("ai_coder_history.html", {
        "request": request,
        "items": data.data,
        "user_id": user_id
    })

@router.get("/admin/ai-coder/history/item/{item_id}")
async def ai_coder_history_item(request: Request, item_id: str):
    if not supabase:
        return {"error": "Supabase not configured"}
    data = supabase.table("ai_coder_history").select("*").eq("id", item_id).single().execute()
    return templates.TemplateResponse("ai_coder_history_item.html", {
        "request": request,
        "item": data.data
    })

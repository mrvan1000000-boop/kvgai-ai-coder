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

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if (SUPABASE_URL and SUPABASE_KEY) else None

try:
    from groq import Groq
    groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
except:
    groq_client = None

router = APIRouter()
templates = Jinja2Templates(directory="templates")

AVAILABLE_MODELS = [
    "openrouter:google/gemma-4-26b-a4b-it:free",
    "openrouter:qwen/qwen3-coder:free",
    "groq:llama-3.3-70b-versatile",
]

# ---------------------------
#  UTILITY FUNCTIONS
# ---------------------------
def parse_files_from_ai(content: str) -> dict:
    """Парсит ответ модели и извлекает файлы в формате --- path --- код"""
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

def extract_zip_and_read(zip_file: UploadFile):
    temp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(temp_dir, "uploaded.zip")
    with open(zip_path, "wb") as f:
        shutil.copyfileobj(zip_file.file, f)
    
    files_data = {}
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(temp_dir)
    except zipfile.BadZipFile:
        return {"error": "not_a_zip"}
    
    for root, _, files in os.walk(temp_dir):
        for fn in files:
            if fn == "uploaded.zip": continue
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, temp_dir)
            try:
                with open(p, encoding="utf-8") as f:
                    files_data[rel] = f.read()
            except UnicodeDecodeError:
                continue
    return files_data

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

def call_openrouter(model: str, messages: list, timeout: int = 30):
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

def call_groq(model: str, messages: list, timeout: int = 30):
    if not groq_client:
        raise Exception("Groq client not available")
    completion = groq_client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.6,
        max_tokens=4096,
        timeout=timeout
    )
    return completion.choices[0].message.content if completion.choices else None

def call_ai(messages: list, model: str, timeout: int = 30):
    try:
        if model.startswith("openrouter:"):
            model_name = model.split(":", 1)[1]
            return call_openrouter(model_name, messages, timeout)
        elif model.startswith("groq:"):
            model_name = model.split(":", 1)[1]
            return call_groq(model_name, messages, timeout)
        else:
            return None
    except Exception as e:
        logger.error(f"AI call failed: {e}")
        return None

# ---------------------------
#  ROUTES
# ---------------------------
@router.get("/admin/ai-coder")
async def ai_coder_page(request: Request, user_id: str | None = None):
    uid = user_id or str(uuid.uuid4())
    return templates.TemplateResponse("ai_coder.html", {
        "request": request,
        "user_id": uid,
        "available_models": AVAILABLE_MODELS,
        "selected_model": AVAILABLE_MODELS[0]
    })

@router.post("/admin/ai-coder/api")
async def ai_coder_api(
    request: Request,
    task: str = Form(...),
    file: UploadFile = File(None),
    model: str = Form(None),
    user_id: str = Form(None)
):
    try:
        uid = user_id or str(uuid.uuid4())
        selected_model = model if model in AVAILABLE_MODELS else AVAILABLE_MODELS[0]

        # Сбор контекста из файлов
        context_content = ""
        file_names = []
        zip_contents = {}

        if file and file.filename:
            # Пытаемся распаковать как ZIP
            res = extract_zip_and_read(file)
            if isinstance(res, dict) and "error" in res:
                # Не ZIP, читаем как текст
                file.file.seek(0)
                file_content = file.file.read().decode("utf-8", errors="ignore")
                context_content += f"File: {file.filename}\nContent:\n{file_content}\n\n"
                file_names.append(file.filename)
            else:
                zip_contents = res
                for path, content in zip_contents.items():
                    context_content += f"--- {path} ---\n{content}\n\n"
                    file_names.append(path)

        # Запрос к LLM
        messages = [
            {"role": "system", "content": "You are an expert coder. Return code using format: --- path/to/file.py --- \n<code>"},
            {"role": "user", "content": f"Task: {task}\n\n{context_content}"}
        ]

        ai_response = call_ai(messages, selected_model)

        if not ai_response:
            return JSONResponse({"error": "AI failed to respond"}, status_code=500)

        # Сохранение в историю
        if supabase:
            supabase.table("ai_coder_history").insert({
                "user_id": uid,
                "task": task,
                "file_names": ", ".join(file_names),
                "model_output": ai_response
            }).execute()

            # Создание задачи для git-агента (опционально)
            supabase.table("ai_coder_tasks").insert({
                "user_id": uid,
                "file_path": "project_root",
                "prompt": task,
                "status": "pending"
            }).execute()

        # Генерация ZIP с исправленными файлами
        fixed_files = parse_files_from_ai(ai_response)
        if fixed_files:
            fixed_zip_path = create_fixed_zip(fixed_files)
            download_url = f"/admin/ai-coder/download?path={fixed_zip_path}"
        else:
            download_url = None

        return JSONResponse({
            "result": ai_response,
            "download_url": download_url,
            "user_id": uid
        })

    except Exception as e:
        logger.exception("Error in ai_coder_api")
        return JSONResponse({"error": f"Server error: {e}"}, status_code=500)

# ---------------------------
#  DOWNLOAD
# ---------------------------
ALLOWED_DIR = tempfile.gettempdir()

@router.get("/admin/ai-coder/download", response_class=FileResponse)
async def download_file(path: str):
    abs_path = os.path.abspath(path)
    if not abs_path.startswith(ALLOWED_DIR) or not os.path.exists(abs_path):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    return FileResponse(abs_path, filename="fixed_project.zip", media_type="application/zip")

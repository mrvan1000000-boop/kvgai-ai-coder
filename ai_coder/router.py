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
    "openrouter:tencent/hy3:free",
    "openrouter:qwen/qwen3-coder:free",
    "openrouter:nvidia/nemotron-3-ultra-550b-a55b:free",
    "openrouter:meta-llama/llama-3.3-70b-instruct:free",
    "openrouter:microsoft/phi-3-mini-128k-instruct:free",
    "openrouter:mistralai/mistral-7b-instruct:free",
    "groq:llama-3.1-8b-instant",
    "groq:mixtral-8x7b-32768",
    "groq:llama-3.3-70b-versatile",
    "groq:gemma2-9b-it",
]

# ---------------------------
#  UTILITY FUNCTIONS
# ---------------------------
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

def call_groq(model: str, messages: list, timeout: int = 15):
    if not groq_client:
        raise Exception("Groq client not available")
    completion = groq_client.chat.completions.create(
        model=model,
        messages=[{"role": m["role"], "content": m["content"]} for m in messages],
        temperature=0.6,
        max_tokens=4096,
        top_p=0.95,
        timeout=timeout
    )
    return completion.choices[0].message.content if completion.choices else None

def call_ai(messages: list, model: str, timeout: int = 15):
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

def call_model_with_fallback(messages, primary_model):
    base = [primary_model] + AVAILABLE_MODELS
    paid_fallback = "openrouter:google/gemma-4-26b-a4b-it"
    if paid_fallback not in base:
        base.append(paid_fallback)
    seen, unique_models = set(), []
    for m in base:
        if m not in seen:
            seen.add(m)
            unique_models.append(m)

    for attempt in range(2):
        for full in unique_models:
            try:
                provider, model_name = full.split(":", 1) if ":" in full else ("openrouter", full)
                if provider == "openrouter":
                    if not OPENROUTER_API_KEY: continue
                    content = call_openrouter(model_name, messages)
                elif provider == "groq":
                    if not groq_client: continue
                    content = call_groq(model_name, messages)
                else:
                    continue
                if content: return content
            except requests.exceptions.Timeout:
                logger.warning(f"{full} timeout"); continue
            except requests.exceptions.RequestException as e:
                if getattr(e, 'response', None) and e.response.status_code in (429, 413):
                    continue
                logger.warning(f"{full} req error: {e}"); continue
            except Exception as e:
                logger.warning(f"{full} err: {e}"); continue
    return "❌ Нейросеть временно недоступна. Повторите попытку позже."

# ---------------------------
#  DB HELPERS
# ---------------------------
def save_history(user_id, task, file_names, model_output):
    if not supabase: return
    supabase.table("ai_coder_history").insert({
        "user_id": user_id,
        "task": task,
        "file_names": file_names,
        "model_output": model_output
    }).execute()

def save_message(user_id, role, content):
    if not supabase: return
    supabase.table("ai_coder_messages").insert({
        "user_id": user_id,
        "role": role,
        "content": content
    }).execute()

def load_messages(user_id, limit=10):
    if not supabase: return []
    data = supabase.table("ai_coder_messages") \
        .select("*") \
        .eq("user_id", user_id) \
        .order("created_at", desc=False) \
        .limit(limit) \
        .execute()
    return [{"role": i["role"], "content": i["content"]} for i in data.data]

# ---------------------------
#  ROUTES
# ---------------------------
@router.get("/admin/ai-coder")
async def ai_coder_page(request: Request, user_id: str | None = None):
    uid = user_id or str(uuid.uuid4())
    # Загружаем последние сообщения
    history = load_messages(uid, limit=10)
    # Преобразуем для отображения в чате
    chat_history = []
    for msg in history:
        role = msg["role"]
        content = msg["content"]
        if role == "user" and "Task:" in content:
            content = content.split("Task:", 1)[1].split("\n\nSingle file")[0].strip()
        chat_history.append({"role": role, "content": content})
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
            res = extract_zip_and_read(file)
            if isinstance(res, dict) and "error" in res:
                file.file.seek(0)
                file_content = file.file.read().decode("utf-8", errors="ignore")
                context_content += f"File: {file.filename}\nContent:\n{file_content}\n\n"
                file_names.append(file.filename)
            else:
                zip_contents = res
                for path, content in zip_contents.items():
                    context_content += f"--- {path} ---\n{content}\n\n"
                    file_names.append(path)

        # Формируем полное сообщение
        full_content = f"User ID: {uid}\nTask: {task}\n\n{context_content}\n\nИсправь ошибки, оптимизируй код и верни исправленные файлы в формате:\n--- path/to/file.py ---\n<исправленный код>"
        messages = [
            {"role": "user", "content": full_content}
        ]

        # Вызываем AI с fallback
        ai_response = call_model_with_fallback(messages, selected_model)

        if not ai_response or ai_response.startswith("❌"):
            return JSONResponse({"error": ai_response or "AI failed to respond"}, status_code=503)

        # Сохраняем в БД
        save_message(uid, "user", full_content)
        save_message(uid, "assistant", ai_response)
        save_history(uid, task, ", ".join(file_names), ai_response)

        # Генерация ZIP с исправленными файлами
        fixed_files = parse_files_from_ai(ai_response)
        download_url = None
        if fixed_files:
            fixed_zip_path = create_fixed_zip(fixed_files)
            download_url = f"/admin/ai-coder/download?path={fixed_zip_path}"

        return JSONResponse({
            "result": ai_response,
            "download_url": download_url,
            "user_id": uid
        })

    except Exception as e:
        logger.exception("Error in ai_coder_api")
        return JSONResponse({"error": f"Server error: {e}"}, status_code=500)

# ========== DOWNLOAD ==========
ALLOWED_DIR = tempfile.gettempdir()

@router.get("/admin/ai-coder/download", response_class=FileResponse)
async def download_file(path: str):
    abs_path = os.path.abspath(path)
    if not abs_path.startswith(ALLOWED_DIR) or not os.path.exists(abs_path):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    return FileResponse(abs_path, filename="fixed_project.zip", media_type="application/zip")

# ========== HISTORY ==========
@router.get("/admin/ai-coder/history/{user_id}")
async def ai_coder_history(request: Request, user_id: str):
    if not supabase:
        return {"error": "Supabase не настроен"}
    data = supabase.table("ai_coder_history").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
    items = data.data
    return templates.TemplateResponse("ai_coder_history.html", {
        "request": request,
        "items": items,
        "user_id": user_id
    })

@router.get("/admin/ai-coder/history/item/{item_id}")
async def ai_coder_history_item(request: Request, item_id: str):
    if not supabase:
        return {"error": "Supabase не настроен"}
    data = supabase.table("ai_coder_history").select("*").eq("id", item_id).single().execute()
    return templates.TemplateResponse("ai_coder_history_item.html", {
        "request": request,
        "item": data.data
    })

import os
import tempfile
import shutil
import uuid
import logging
import zipfile
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
import requests

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------
#  SUPABASE
# ---------------------------
try:
    from supabase import create_client, Client
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if (SUPABASE_URL and SUPABASE_KEY) else None
except Exception as e:
    logger.warning(f"Supabase init error: {e}")
    supabase = None

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

try:
    from groq import Groq
    groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
except Exception as e:
    groq_client = None
    logger.warning(f"Groq init error: {e}")

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
#  Провайдеры AI
# ---------------------------
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
#  Вспомогательные функции
# ---------------------------
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

def parse_fixed_files(out: str):
    fixed, path, buf = {}, None, []
    for line in out.splitlines():
        if line.startswith("--- ") and line.endswith(" ---"):
            if path: fixed[path] = "\n".join(buf)
            path, buf = line[4:-4].strip(), []
        elif path:
            buf.append(line)
    if path: fixed[path] = "\n".join(buf)
    return fixed

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

# ---------------------------
#  БД
# ---------------------------
def save_history(user_id, task, file_names, model_output):
    if not supabase: return
    supabase.table("ai_coder_history").insert({
        "user_id": user_id, "task": task,
        "file_names": file_names, "model_output": model_output
    }).execute()

def save_message(user_id, role, content):
    if not supabase: return
    supabase.table("ai_coder_messages").insert({
        "user_id": user_id, "role": role, "content": content
    }).execute()

def load_messages(user_id, limit=10):
    if not supabase: return []
    data = supabase.table("ai_coder_messages").select("*").eq("user_id", user_id).execute()
    items = sorted(data.data, key=lambda x: x["created_at"], reverse=True)[:limit]
    return [{"role": i["role"], "content": i["content"]} for i in reversed(items)]

def get_client_ip(request: Request) -> str:
    fwd = request.headers.get("X-Forwarded-For")
    if fwd: return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

def ensure_user(ip: str, user_id: str | None = None) -> str:
    if not supabase:
        return user_id or str(uuid.uuid4())
    if user_id:
        supabase.table("ai_coder_users").upsert({"user_id": user_id, "ip": ip}).execute()
        return user_id
    res = supabase.table("ai_coder_users").select("user_id").eq("ip", ip).limit(1).execute()
    if res.data:
        return res.data[0]["user_id"]
    new_id = str(uuid.uuid4())
    supabase.table("ai_coder_users").insert({"user_id": new_id, "ip": ip}).execute()
    return new_id

# ---------------------------
#  Общая логика
# ---------------------------
async def process_request(request, task, file, model, custom_model, user_id):
    ip = get_client_ip(request)
    user_id = ensure_user(ip, user_id)
    selected = custom_model if (model == "custom" and custom_model) else (model if model in AVAILABLE_MODELS else AVAILABLE_MODELS[0])

    fc, zc, fname = "", {}, ""
    if file and file.filename:
        fname = file.filename
        res = extract_zip_and_read(file)
        if isinstance(res, dict) and "error" in res:
            file.file.seek(0)
            fc = file.file.read().decode("utf-8", errors="ignore")
        else:
            zc = res

    history = load_messages(user_id)
    msg = {
        "role": "user",
        "content": f"User ID: {user_id}\nTask: {task}\n\nSingle file contents:\n{fc}\n\nZIP project contents:\n" +
                   "\n".join(f"--- {p} ---\n{c}\n" for p, c in zc.items()) +
                   "\n\nИсправь ошибки, оптимизируй код и верни исправленные файлы в формате:\n--- path/to/file.py ---\n<исправленный код>"
    }
    result = call_model_with_fallback(history + [msg], selected)
    save_message(user_id, "user", msg["content"])
    save_message(user_id, "assistant", result)
    save_history(user_id, task, fname, result)

    fixed = parse_fixed_files(result)
    zp = create_fixed_zip(fixed)
    return user_id, selected, result, f"/admin/ai-coder/download?path={zp}"

# ========== GET ==========
@router.get("/admin/ai-coder")
def ai_coder_page(request: Request, user_id: str | None = None):
    ip = get_client_ip(request)
    uid = ensure_user(ip, user_id)
    history = load_messages(uid)  # загружаем прошлые сообщения
    # Преобразуем в формат для шаблона: user/assistant
    chat_history = []
    for m in history:
        role = "user" if m["role"] == "user" else "assistant"
        # Убираем служебный префикс "User ID: ... Task: ..." для чистоты (опционально)
        content = m["content"]
        if role == "user" and "Task:" in content:
            content = content.split("Task:", 1)[1].split("\n\nSingle file")[0].strip()
        chat_history.append({"role": role, "content": content})
    return templates.TemplateResponse("ai_coder.html", {
        "request": request,
        "result": None,
        "task": "",
        "download": None,
        "user_id": uid,
        "available_models": AVAILABLE_MODELS,
        "selected_model": AVAILABLE_MODELS[0],
        "chat_history": chat_history  # передаём в шаблон
    })

# ========== POST ==========
@router.post("/admin/ai-coder")
async def ai_coder(request: Request, task: str = Form(...), file: UploadFile = File(None),
                   model: str = Form(None), custom_model: str = Form(None), user_id: str = Form(None)):
    uid, sel, result, dl = await process_request(request, task, file, model, custom_model, user_id)
    return templates.TemplateResponse("ai_coder.html", {
        "request": request, "result": result, "task": task, "download": dl,
        "user_id": uid, "available_models": AVAILABLE_MODELS, "selected_model": sel,
        "custom_model": custom_model if model == "custom" else ""
    })

# ========== API ==========
@router.post("/admin/ai-coder/api")
async def ai_coder_api(request: Request, task: str = Form(...), file: UploadFile = File(None),
                       model: str = Form(None), custom_model: str = Form(None), user_id: str = Form(None)):
    try:
        uid, sel, result, dl = await process_request(request, task, file, model, custom_model, user_id)
        if result.startswith("❌"):
            return JSONResponse({"error": result}, status_code=503)
        return JSONResponse({"result": result, "download_url": dl, "user_id": uid})
    except Exception as e:
        logger.exception("ai_coder_api error")
        return JSONResponse({"error": f"Внутренняя ошибка: {e}"}, status_code=500)

# ========== История ==========
@router.get("/admin/ai-coder/history/{user_id}")
def ai_coder_history(request: Request, user_id: str):
    if not supabase: return {"error": "Supabase не настроен"}
    data = supabase.table("ai_coder_history").select("*").eq("user_id", user_id).execute()
    items = sorted(data.data, key=lambda x: x["created_at"], reverse=True)
    return templates.TemplateResponse("ai_coder_history.html", {"request": request, "items": items, "user_id": user_id})

@router.get("/admin/ai-coder/history/item/{item_id}")
def ai_coder_history_item(request: Request, item_id: str):
    if not supabase: return {"error": "Supabase не настроен"}
    data = supabase.table("ai_coder_history").select("*").eq("id", item_id).single().execute()
    return templates.TemplateResponse("ai_coder_history_item.html", {"request": request, "item": data.data})

# ========== Download ==========
ALLOWED_DIR = tempfile.gettempdir()

@router.get("/admin/ai-coder/download")
def download_file(path: str):
    abs_path = os.path.abspath(path)
    if not abs_path.startswith(ALLOWED_DIR) or not os.path.exists(abs_path):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    return FileResponse(abs_path, filename="fixed_project.zip")
```

---

### SQL для Supabase (выполнить в SQL Editor):
```sql
create table if not exists ai_coder_users (
  user_id text primary key,
  ip text,
  created_at timestamp default now(),
  updated_at timestamp default now()
);

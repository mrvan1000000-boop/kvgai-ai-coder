import os
import tempfile
import shutil
import uuid
import logging
import zipfile
import requests
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    "openrouter:qwen/qwen3-coder:free",
    "openrouter:meta-llama/llama-3.3-70b-instruct:free",
    "groq:llama-3.3-70b-versatile",
    "groq:gemma2-9b-it",
]

# --- AI UTILS ---

def _clean_ai_response(text: str) -> str:
    if not text: return ""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            if lines and lines[0].strip().lower() in ("python", "py", "javascript", "js", "html", "css"):
                lines = lines[1:]
            text = "\n".join(lines)
    return text.strip()

def call_openrouter(model: str, messages: list, timeout: int = 30):
    """Вызов OpenRouter API"""
    model_name = model.replace("openrouter:", "")
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://dalvideo.ru",
                "X-Title": "KVG AI Studio"
            },
            json={"model": model_name, "messages": messages},
            timeout=timeout
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"] if data.get("choices") else None
    except Exception as e:
        logger.error(f"OpenRouter error: {e}")
        raise

def call_groq(model: str, messages: list, timeout: int = 30):
    """Вызов Groq API"""
    if not groq_client:
        raise Exception("Groq client not available")
    try:
        model_name = model.replace("groq:", "")
        completion = groq_client.chat.completions.create(
            model=model_name,
            messages=[{"role": m["role"], "content": m["content"]} for m in messages],
            temperature=0.2,
            max_tokens=4096,
            timeout=timeout
        )
        return completion.choices[0].message.content if completion.choices else None
    except Exception as e:
        logger.error(f"Groq error: {e}")
        raise

def call_model_with_fallback(messages, primary_model):
    """Вызов модели с fallback на другие модели"""
    # Формируем список моделей для попыток
    models_to_try = []
    
    # Если выбрана кастомная модель (не из списка), добавляем её первой
    if primary_model not in AVAILABLE_MODELS:
        models_to_try.append(primary_model)
    
    # Добавляем выбранную модель (если она из списка)
    if primary_model in AVAILABLE_MODELS:
        models_to_try.append(primary_model)
    
    # Добавляем остальные модели из списка для fallback
    for m in AVAILABLE_MODELS:
        if m not in models_to_try:
            models_to_try.append(m)

    # Пробуем каждую модель по очереди
    for full in models_to_try:
        try:
            if full.startswith("groq:"):
                content = call_groq(full, messages)
            else:
                # Обработка openrouter и кастомных моделей
                content = call_openrouter(full, messages)
            
            if content:
                logger.info(f"Success with model: {full}")
                return content
        except Exception as e:
            logger.warning(f"Model {full} failed: {e}")
            continue
    
    return "❌ Нейросеть временно недоступна. Повторите попытку позже."

# --- FILE & DATA UTILS ---

def extract_zip_and_read(zip_file: UploadFile):
    """Извлечение и чтение ZIP-архива"""
    temp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(temp_dir, "uploaded.zip")
    try:
        with open(zip_path, "wb") as f:
            shutil.copyfileobj(zip_file.file, f)
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(temp_dir)
        files_data = {}
        for root, _, files in os.walk(temp_dir):
            for fn in files:
                if fn == "uploaded.zip": continue
                p = os.path.join(root, fn)
                rel = os.path.relpath(p, temp_dir)
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        files_data[rel] = f.read()
                except (UnicodeDecodeError, IOError):
                    continue
        return files_data
    except Exception as e:
        logger.error(f"Zip error: {e}")
        return {"error": "not_a_zip"}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def parse_fixed_files(out: str):
    """Парсинг ответа AI для извлечения файлов"""
    fixed, current_path, buffer = {}, None, []
    for line in out.splitlines():
        if line.startswith("--- ") and line.endswith(" ---"):
            if current_path:
                fixed[current_path] = "\n".join(buffer)
            current_path = line[4:-4].strip()
            buffer = []
        elif current_path is not None:
            buffer.append(line)
    if current_path:
        fixed[current_path] = "\n".join(buffer)
    return fixed

def create_fixed_zip(fixed_files: dict):
    """Создание ZIP-архива с исправленными файлами"""
    temp_dir = tempfile.mkdtemp()
    zip_name = f"fixed_{uuid.uuid4().hex[:8]}.zip"
    zip_path = os.path.join(temp_dir, zip_name)
    try:
        with zipfile.ZipFile(zip_path, "w") as zf:
            for rel_path, content in fixed_files.items():
                zf.writestr(rel_path, content)
        return zip_path, zip_name
    except Exception as e:
        logger.error(f"Create zip error: {e}")
        return None, None

def save_history(user_id, task, file_names, model_output):
    """Сохранение истории запроса"""
    if not supabase: return
    try:
        supabase.table("ai_coder_history").insert({
            "user_id": user_id,
            "task": task,
            "file_names": str(file_names),
            "model_output": model_output
        }).execute()
    except Exception as e:
        logger.error(f"Save history error: {e}")

def save_message(user_id, role, content):
    """Сохранение сообщения в БД"""
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
    """Загрузка последних сообщений пользователя"""
    if not supabase: return []
    try:
        data = supabase.table("ai_coder_messages").select("*").eq("user_id", user_id).execute()
        items = sorted(data.data, key=lambda x: x["created_at"], reverse=True)[:limit]
        return [{"role": i["role"], "content": i["content"]} for i in reversed(items)]
    except Exception as e:
        logger.error(f"Load messages error: {e}")
        return []

def get_client_ip(request: Request) -> str:
    """Получение IP клиента"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

def ensure_user(ip: str, user_id: str | None = None) -> str:
    """Создание или получение пользователя"""
    if not supabase:
        return user_id or str(uuid.uuid4())
    
    try:
        if user_id:
            supabase.table("ai_coder_users").upsert({"user_id": user_id, "ip": ip}).execute()
            return user_id
        
        # Поиск по IP
        res = supabase.table("ai_coder_users").select("user_id").eq("ip", ip).limit(1).execute()
        if res.data:
            return res.data[0]["user_id"]
        
        # Создание нового пользователя
        new_id = str(uuid.uuid4())
        supabase.table("ai_coder_users").insert({"user_id": new_id, "ip": ip}).execute()
        return new_id
    except Exception as e:
        logger.error(f"Ensure user error: {e}")
        return user_id or str(uuid.uuid4())

async def process_request(request, task, file, model, custom_model, user_id):
    """Обработка запроса пользователя"""
    ip = get_client_ip(request)
    uid = ensure_user(ip, user_id)
    
    # Определяем выбранную модель
    if model == "custom" and custom_model:
        selected = custom_model  # Кастомная модель
    elif model in AVAILABLE_MODELS:
        selected = model
    else:
        selected = AVAILABLE_MODELS[0]  # По умолчанию первая модель
    
    # Чтение файла
    fc, zc, fname = "", {}, ""
    if file and file.filename:
        fname = file.filename
        res = extract_zip_and_read(file)
        if isinstance(res, dict) and "error" in res:
            # Если не ZIP, читаем как обычный файл
            try:
                file.file.seek(0)
                fc = file.file.read().decode("utf-8", errors="ignore")
            except:
                fc = ""
        else:
            zc = res
    
    # Загрузка истории
    history = load_messages(uid)
    
    # Формирование сообщения
    single_file_content = f"Single file contents:\n{fc}\n" if fc else ""
    zip_content = "\n".join(f"--- {p} ---\n{c}\n" for p, c in zc.items()) if zc else ""
    
    msg_content = (
        f"User ID: {uid}\n"
        f"Task: {task}\n\n"
        f"{single_file_content}"
        f"{zip_content}"
        f"\n\nИсправь ошибки, оптимизируй код и верни исправленные файлы в формате:\n"
        f"--- path/to/file.py ---\n"
        f"<исправленный код>"
    )
    
    # Вызов AI
    result = call_model_with_fallback(history + [{"role": "user", "content": msg_content}], selected)
    
    # Сохранение сообщений
    save_message(uid, "user", msg_content)
    save_message(uid, "assistant", result)
    
    # Сохранение задачи
    if not result.startswith("❌") and supabase:
        try:
            target_path = fname if fname else "project_root"
            supabase.table("ai_coder_tasks").insert({
                "user_id": uid,
                "file_path": target_path,
                "prompt": task,
                "status": "pending"
            }).execute()
        except Exception as e:
            logger.error(f"Save task error: {e}")
    
    # Сохранение истории
    save_history(uid, task, fname, result)
    
    # Создание ZIP с исправленными файлами
    fixed = parse_fixed_files(result)
    zip_path, zip_name = create_fixed_zip(fixed)
    download_url = f"/admin/ai-coder/download?path={zip_path}" if zip_path else None
    
    return uid, selected, result, download_url

# --- ROUTES ---

@router.get("/admin/ai-coder")
def ai_coder_page(request: Request, user_id: str | None = None):
    """Страница чата AI Coder"""
    ip = get_client_ip(request)
    uid = ensure_user(ip, user_id)
    messages = load_messages(uid)
    
    return templates.TemplateResponse("ai_coder_chat.html", {
        "request": request,
        "messages": messages,
        "user_id": uid,
        "available_models": AVAILABLE_MODELS,
        "selected_model": AVAILABLE_MODELS[0],
        "custom_model": ""
    })

@router.post("/admin/ai-coder")
async def ai_coder(
    request: Request, 
    task: str = Form(...), 
    file: UploadFile = File(None),
    model: str = Form(None), 
    custom_model: str = Form(None), 
    user_id: str = Form(None)
):
    """Обработка отправки сообщения"""
    uid, sel, result, dl = await process_request(request, task, file, model, custom_model, user_id)
    messages = load_messages(uid)
    
    return templates.TemplateResponse("ai_coder_chat.html", {
        "request": request,
        "messages": messages,
        "result": result,
        "task": task,
        "download": dl,
        "user_id": uid,
        "available_models": AVAILABLE_MODELS,
        "selected_model": sel,
        "custom_model": custom_model if model == "custom" else ""
    })

@router.get("/admin/ai-coder/history/{user_id}")
def ai_coder_history(request: Request, user_id: str):
    """Страница истории запросов"""
    if not supabase:
        return {"error": "Supabase не настроен"}
    try:
        data = supabase.table("ai_coder_history").select("*").eq("user_id", user_id).execute()
        items = sorted(data.data, key=lambda x: x["created_at"], reverse=True)
        return templates.TemplateResponse("ai_coder_history.html", {
            "request": request, 
            "items": items, 
            "user_id": user_id
        })
    except Exception as e:
        logger.error(f"History error: {e}")
        return {"error": str(e)}

@router.get("/admin/ai-coder/history/item/{item_id}")
def ai_coder_history_item(request: Request, item_id: str):
    """Детальная страница элемента истории"""
    if not supabase:
        return {"error": "Supabase не настроен"}
    try:
        data = supabase.table("ai_coder_history").select("*").eq("id", item_id).single().execute()
        return templates.TemplateResponse("ai_coder_history_item.html", {
            "request": request, 
            "item": data.data
        })
    except Exception as e:
        logger.error(f"History item error: {e}")
        return {"error": str(e)}

@router.get("/admin/ai-coder/download")
def download_file(path: str):
    """Скачивание исправленного проекта"""
    abs_path = os.path.abspath(path)
    if not abs_path.startswith(tempfile.gettempdir()) or not os.path.exists(abs_path):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    return FileResponse(abs_path, filename="fixed_project.zip")

@router.get("/admin/ai-coder/tasks")
def tasks_page(request: Request):
    """Страница задач"""
    return templates.TemplateResponse("ai_coder_tasks.html", {"request": request})

@router.post("/admin/ai-coder/tasks")
async def create_task(
    request: Request, 
    file_path: str = Form(...), 
    prompt: str = Form(...)
):
    """Создание задачи"""
    if not supabase:
        return JSONResponse({"error": "Supabase не настроен"}, status_code=500)
    try:
        supabase.table("ai_coder_tasks").insert({
            "user_id": "00000000-0000-0000-0000-000000000001",
            "file_path": file_path,
            "prompt": prompt,
            "status": "pending"
        }).execute()
        return JSONResponse({"status": "success"})
    except Exception as e:
        logger.error(f"Create task error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

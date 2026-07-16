from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
import tempfile
import shutil
import os
import requests
import zipfile
import uuid
import logging

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
    if SUPABASE_URL and SUPABASE_KEY:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    else:
        supabase = None
except Exception:
    supabase = None

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Инициализация Groq
try:
    from groq import Groq
    groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
except ImportError:
    groq_client = None
    logger.warning("Groq library not installed")
except Exception as e:
    groq_client = None
    logger.warning(f"Groq init error: {e}")

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# ---------------------------
#  Модели (с префиксами провайдеров)
# ---------------------------
AVAILABLE_MODELS = [
    # OpenRouter (бесплатные)
    "openrouter:google/gemma-4-26b-a4b-it:free",
    "openrouter:tencent/hy3:free",
    "openrouter:qwen/qwen3-coder:free",
    "openrouter:nvidia/nemotron-3-ultra-550b-a55b:free",
    "openrouter:meta-llama/llama-3.3-70b-instruct:free",
    "openrouter:microsoft/phi-3-mini-128k-instruct:free",
    "openrouter:mistralai/mistral-7b-instruct:free",
    # Groq (бесплатные)
    "groq:llama-3.1-8b-instant",
    "groq:mixtral-8x7b-32768",
    "groq:llama-3.3-70b-versatile",
    "groq:gemma2-9b-it",
]

# ---------------------------
#  Функции вызова провайдеров
# ---------------------------
def call_openrouter(model: str, messages: list, timeout: int = 15):
    """Вызов OpenRouter API"""
    response = requests.post(
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
    response.raise_for_status()
    data = response.json()
    if "choices" in data and len(data["choices"]) > 0:
        return data["choices"][0]["message"]["content"]
    return None

def call_groq(model: str, messages: list, timeout: int = 15):
    """Вызов Groq API"""
    if not groq_client:
        raise Exception("Groq client not available")
    
    groq_messages = [{"role": m["role"], "content": m["content"]} for m in messages]
    
    completion = groq_client.chat.completions.create(
        model=model,
        messages=groq_messages,
        temperature=0.6,
        max_tokens=4096,
        top_p=0.95,
        timeout=timeout
    )
    if completion.choices and len(completion.choices) > 0:
        return completion.choices[0].message.content
    return None

# ---------------------------
#  Основная функция с fallback
# ---------------------------
def call_model_with_fallback(messages, primary_model):
    # Строим список моделей: сначала выбранная пользователем,
    # затем все из AVAILABLE_MODELS (без дубликатов),
    # в конце платная fallback-модель OpenRouter
    base_models = [primary_model] + AVAILABLE_MODELS
    # Добавляем платную модель, если её нет в списке
    paid_fallback = "openrouter:google/gemma-4-26b-a4b-it"  # платная версия
    if paid_fallback not in base_models:
        base_models.append(paid_fallback)
    
    # Убираем дубликаты, сохраняя порядок
    seen = set()
    unique_models = []
    for m in base_models:
        if m not in seen:
            seen.add(m)
            unique_models.append(m)

    max_attempts = 2
    timeout = 15

    for attempt in range(max_attempts):
        logger.info(f"Attempt {attempt + 1}/{max_attempts}")

        for full_model in unique_models:
            try:
                if ":" in full_model:
                    provider, model_name = full_model.split(":", 1)
                else:
                    provider = "openrouter"
                    model_name = full_model

                logger.info(f"Calling {provider}:{model_name}")

                if provider == "openrouter":
                    if not OPENROUTER_API_KEY:
                        logger.warning("OpenRouter API key missing")
                        continue
                    content = call_openrouter(model_name, messages, timeout)
                elif provider == "groq":
                    if not groq_client:
                        logger.warning("Groq client not available")
                        continue
                    content = call_groq(model_name, messages, timeout)
                else:
                    logger.warning(f"Unknown provider: {provider}")
                    continue

                if content:
                    logger.info(f"Model {full_model} returned response")
                    return content
                else:
                    logger.warning(f"Model {full_model} returned empty response")
                    continue

            except requests.exceptions.Timeout:
                logger.warning(f"Model {full_model} timeout ({timeout}s), trying next")
                continue
            except requests.exceptions.RequestException as e:
                # Обработка HTTP-ошибок
                if hasattr(e, 'response') and e.response is not None:
                    status = e.response.status_code
                    if status == 429:
                        logger.warning(f"Model {full_model} rate limited (429), trying next")
                        continue
                    elif status == 413:
                        logger.warning(f"Model {full_model} payload too large (413), trying next")
                        continue
                logger.warning(f"Model {full_model} request error: {e}, trying next")
                continue
            except Exception as e:
                # Проверяем текст ошибки на 413 (например, от Groq)
                error_str = str(e)
                if "413" in error_str or "Payload Too Large" in error_str:
                    logger.warning(f"Model {full_model} payload too large, trying next")
                    continue
                logger.warning(f"Model {full_model} unexpected error: {e}, trying next")
                continue

        if attempt == 0:
            logger.info("Все модели не ответили на первой попытке, начинаем второй проход...")

    # Если ни одна модель не ответила после двух проходов
    error_msg = "❌ Нейросеть временно недоступна. Повторите попытку позже."
    logger.error(error_msg)
    return error_msg

# ---------------------------
#  Вспомогательные функции (без изменений)
# ---------------------------
def extract_zip_and_read(zip_file: UploadFile):
    temp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(temp_dir, "uploaded.zip")
    with open(zip_path, "wb") as f:
        shutil.copyfileobj(zip_file.file, f)

    files_data = {}
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(temp_dir)

    for root, dirs, files in os.walk(temp_dir):
        for filename in files:
            full_path = os.path.join(root, filename)
            relative_path = os.path.relpath(full_path, temp_dir)
            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    files_data[relative_path] = content
            except Exception:
                continue
    return files_data

def parse_fixed_files(model_output: str):
    fixed = {}
    current_path = None
    current_content = []
    for line in model_output.splitlines():
        if line.startswith("--- ") and line.endswith(" ---"):
            if current_path:
                fixed[current_path] = "\n".join(current_content)
            current_path = line[4:-4].strip()
            current_content = []
        else:
            if current_path:
                current_content.append(line)
    if current_path:
        fixed[current_path] = "\n".join(current_content)
    return fixed

def create_fixed_zip(fixed_files: dict):
    temp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(temp_dir, "fixed_project.zip")
    with zipfile.ZipFile(zip_path, "w") as zipf:
        for path, content in fixed_files.items():
            full_path = os.path.join(temp_dir, path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            zipf.write(full_path, arcname=path)
    return zip_path

# ---------------------------
#  Работа с БД (Supabase)
# ---------------------------
def save_history(user_id, task, file_names, zip_files, model_output):
    if not supabase:
        return
    supabase.table("ai_coder_history").insert({
        "user_id": user_id,
        "task": task,
        "file_names": file_names,
        "zip_files": zip_files,
        "model_output": model_output
    }).execute()

def save_message(user_id: str, role: str, content: str):
    if not supabase:
        return
    supabase.table("ai_coder_messages").insert({
        "user_id": user_id,
        "role": role,
        "content": content
    }).execute()

def load_messages(user_id: str, limit: int = 10):
    if not supabase:
        return []
    data = supabase.table("ai_coder_messages").select("*").eq("user_id", user_id).execute()
    items = sorted(data.data, key=lambda x: x["created_at"], reverse=True)[:limit]
    return [{"role": msg["role"], "content": msg["content"]} for msg in reversed(items)]

# ========== GET /admin/ai-coder ==========
@router.get("/admin/ai-coder")
def ai_coder_page(request: Request, user_id: str | None = None):
    if not user_id:
        user_id = str(uuid.uuid4())
    return templates.TemplateResponse("ai_coder.html", {
        "request": request,
        "result": None,
        "task": "",
        "download": None,
        "user_id": user_id,
        "available_models": AVAILABLE_MODELS,
        "selected_model": AVAILABLE_MODELS[0]
    })

# ========== POST /admin/ai-coder (синхронный) ==========
@router.post("/admin/ai-coder")
async def ai_coder(
    request: Request,
    task: str = Form(...),
    file: UploadFile = File(None),
    model: str = Form(None),
    custom_model: str = Form(None),
    user_id: str = Form(None)
):
    if not user_id:
        user_id = str(uuid.uuid4())

    if model == "custom" and custom_model:
        selected_model = custom_model
    elif model in AVAILABLE_MODELS:
        selected_model = model
    else:
        selected_model = AVAILABLE_MODELS[0]

    file_contents = ""
    zip_contents = {}
    file_name = ""
    is_zip = False

    if file and file.filename:
        file_name = file.filename
        if file.filename.lower().endswith('.zip') or file.content_type == 'application/zip':
            is_zip = True
            try:
                zip_contents = extract_zip_and_read(file)
            except zipfile.BadZipFile:
                zip_contents = {"error": "Файл не является ZIP-архивом"}
        else:
            file_contents = file.file.read().decode("utf-8", errors="ignore")

    history = load_messages(user_id)
    current_message = {
        "role": "user",
        "content": (
            f"User ID: {user_id}\n"
            f"Task: {task}\n\n"
            f"Single file contents:\n{file_contents}\n\n"
            f"ZIP project contents:\n" +
            "\n".join(f"--- {path} ---\n{content}\n" for path, content in zip_contents.items()) +
            "\n\nИсправь ошибки, оптимизируй код и верни исправленные файлы в формате:\n--- path/to/file.py ---\n<исправленный код>"
        )
    }
    messages = history + [current_message]
    result = call_model_with_fallback(messages, selected_model)
    save_message(user_id, "user", current_message["content"])
    save_message(user_id, "assistant", result)
    save_history(user_id, task, file_name if not is_zip else "", file_name if is_zip else "", result)

    fixed_files = parse_fixed_files(result)
    fixed_zip_path = create_fixed_zip(fixed_files)
    download_url = f"/admin/ai-coder/download?path={fixed_zip_path}"

    return templates.TemplateResponse("ai_coder.html", {
        "request": request,
        "result": result,
        "task": task,
        "download": download_url,
        "user_id": user_id,
        "available_models": AVAILABLE_MODELS,
        "selected_model": selected_model,
        "custom_model": custom_model if model == "custom" else ""
    })

# ========== API для AJAX (асинхронный) ==========
@router.post("/admin/ai-coder/api")
async def ai_coder_api(
    task: str = Form(...),
    file: UploadFile = File(None),
    model: str = Form(None),
    custom_model: str = Form(None),
    user_id: str = Form(None)
):
    try:
        if not user_id:
            user_id = str(uuid.uuid4())

        if model == "custom" and custom_model:
            selected_model = custom_model
        elif model in AVAILABLE_MODELS:
            selected_model = model
        else:
            selected_model = AVAILABLE_MODELS[0]

        file_contents = ""
        zip_contents = {}
        file_name = ""
        is_zip = False

        if file and file.filename:
            file_name = file.filename
            if file.filename.lower().endswith('.zip') or file.content_type == 'application/zip':
                is_zip = True
                try:
                    zip_contents = extract_zip_and_read(file)
                except zipfile.BadZipFile:
                    zip_contents = {"error": "Файл не является ZIP-архивом"}
            else:
                file_contents = file.file.read().decode("utf-8", errors="ignore")

        history = load_messages(user_id)
        current_message = {
            "role": "user",
            "content": (
                f"User ID: {user_id}\n"
                f"Task: {task}\n\n"
                f"Single file contents:\n{file_contents}\n\n"
                f"ZIP project contents:\n" +
                "\n".join(f"--- {path} ---\n{content}\n" for path, content in zip_contents.items()) +
                "\n\nИсправь ошибки, оптимизируй код и верни исправленные файлы в формате:\n--- path/to/file.py ---\n<исправленный код>"
            )
        }

        logger.info(f"ZIP contents keys: {list(zip_contents.keys())}")
        logger.info(f"Message content length: {len(current_message['content'])}")

        messages = history + [current_message]
        result = call_model_with_fallback(messages, selected_model)

        if result.startswith("❌"):
            logger.error(f"Returning error to client: {result}")
            return JSONResponse({"error": result}, status_code=503)

        save_message(user_id, "user", current_message["content"])
        save_message(user_id, "assistant", result)
        save_history(user_id, task, file_name if not is_zip else "", file_name if is_zip else "", result)

        fixed_files = parse_fixed_files(result)
        fixed_zip_path = create_fixed_zip(fixed_files)
        download_url = f"/admin/ai-coder/download?path={fixed_zip_path}"

        return JSONResponse({
            "result": result,
            "download_url": download_url,
            "user_id": user_id
        })
    except Exception as e:
        logger.exception("Ошибка в ai_coder_api")
        return JSONResponse({"error": f"Внутренняя ошибка сервера: {str(e)}"}, status_code=500)

# ========== История ==========
@router.get("/admin/ai-coder/history/{user_id}")
def ai_coder_history(request: Request, user_id: str):
    if not supabase:
        return {"error": "Supabase не настроен"}
    data = supabase.table("ai_coder_history").select("*").eq("user_id", user_id).execute()
    items = sorted(data.data, key=lambda x: x["created_at"], reverse=True)
    return templates.TemplateResponse("ai_coder_history.html", {
        "request": request,
        "items": items,
        "user_id": user_id
    })

@router.get("/admin/ai-coder/history/item/{item_id}")
def ai_coder_history_item(request: Request, item_id: str):
    if not supabase:
        return {"error": "Supabase не настроен"}
    data = supabase.table("ai_coder_history").select("*").eq("id", item_id).single().execute()
    return templates.TemplateResponse("ai_coder_history_item.html", {
        "request": request,
        "item": data.data
    })

@router.get("/admin/ai-coder/download")
def download_file(path: str):
    with open(path, "rb") as f:
        return f.read()

import os
import tempfile
import shutil
import uuid
import logging
import zipfile
import requests
import ftplib
import base64
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from supabase import create_client, Client
from cryptography.fernet import Fernet
from github import Github

# ===== LOGGING =====
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== ENV & CONFIG =====
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")

# Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if (SUPABASE_URL and SUPABASE_KEY) else None

# Groq
try:
    from groq import Groq
    groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
except:
    groq_client = None

# Шифрование
cipher = Fernet(ENCRYPTION_KEY) if ENCRYPTION_KEY else None

def encrypt_password(pwd: str) -> str:
    if not cipher: return pwd
    return cipher.encrypt(pwd.encode()).decode()

def decrypt_password(encrypted: str) -> str:
    if not cipher: return encrypted
    return cipher.decrypt(encrypted.encode()).decode()

# ===== ROUTER =====
router = APIRouter()
templates = Jinja2Templates(directory="templates")

# ===== МОДЕЛИ =====
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

# ===== AI ВЫЗОВЫ =====
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

# ===== ФАЙЛОВЫЕ ОПЕРАЦИИ =====
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

# ===== ДЕПЛОЙ =====
def deploy_to_ftp(config: dict, files: dict):
    ftp = ftplib.FTP()
    ftp.connect(config["host"])
    ftp.login(config["username"], config["password"])
    for path, content in files.items():
        dirs = os.path.dirname(path)
        if dirs:
            try:
                ftp.mkd(dirs)
            except:
                pass
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write(content)
            f.flush()
            with open(f.name, 'rb') as fp:
                ftp.storbinary(f'STOR {path}', fp)
        os.unlink(f.name)
    ftp.quit()
    return {"status": "success", "message": f"Загружено {len(files)} файлов на FTP"}

def deploy_to_github(config: dict, files: dict, commit_message="AI-Coder: Auto-fix"):
    g = Github(config["password"])  # password = GitHub токен
    repo = g.get_repo(config["repo"])
    branch = config.get("branch", "main")
    for path, content in files.items():
        try:
            file = repo.get_contents(path, ref=branch)
            repo.update_file(
                path=path,
                message=commit_message,
                content=content,
                sha=file.sha,
                branch=branch
            )
        except:
            repo.create_file(
                path=path,
                message=f"AI-Coder: Create {path}",
                content=content,
                branch=branch
            )
    return {"status": "success", "message": f"Обновлено {len(files)} файлов в GitHub"}

# ===== БД =====
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

# ===== РОУТЫ =====
@router.get("/admin/ai-coder")
async def ai_coder_page(request: Request, user_id: str | None = None):
    uid = user_id or str(uuid.uuid4())
    history = load_messages(uid, limit=10)
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

        full_content = f"User ID: {uid}\nTask: {task}\n\n{context_content}\n\nИсправь ошибки, оптимизируй код и верни исправленные файлы в формате:\n--- path/to/file.py ---\n<исправленный код>"
        messages = [{"role": "user", "content": full_content}]

        ai_response = call_model_with_fallback(messages, selected_model)

        if not ai_response or ai_response.startswith("❌"):
            return JSONResponse({"error": ai_response or "AI failed to respond"}, status_code=503)

        save_message(uid, "user", full_content)
        save_message(uid, "assistant", ai_response)
        save_history(uid, task, ", ".join(file_names), ai_response)

        fixed_files = parse_files_from_ai(ai_response)
        download_url = None
        history_id = None
        if fixed_files:
            fixed_zip_path = create_fixed_zip(fixed_files)
            download_url = f"/admin/ai-coder/download?path={fixed_zip_path}"
            # Сохраняем ID последней записи истории для деплоя
            hist_res = supabase.table("ai_coder_history") \
                .select("id") \
                .eq("user_id", uid) \
                .order("created_at", desc=True) \
                .limit(1) \
                .execute()
            if hist_res.data:
                history_id = hist_res.data[0]["id"]

        return JSONResponse({
            "result": ai_response,
            "download_url": download_url,
            "user_id": uid,
            "history_id": history_id
        })

    except Exception as e:
        logger.exception("Error in ai_coder_api")
        return JSONResponse({"error": f"Server error: {e}"}, status_code=500)

# ===== ДЕПЛОЙ КОНФИГУРАЦИЯ =====
@router.post("/admin/ai-coder/deploy/config")
async def save_deploy_config(
    request: Request,
    user_id: str = Form(...),
    provider: str = Form(...),
    host: str = Form(None),
    username: str = Form(None),
    password: str = Form(None),
    repo: str = Form(None),
    branch: str = Form("main"),
    path: str = Form("/")
):
    try:
        existing = supabase.table("ai_coder_deploy_configs") \
            .select("*") \
            .eq("user_id", user_id) \
            .execute()
        
        data = {
            "user_id": user_id,
            "provider": provider,
            "host": host,
            "username": username,
            "repo": repo,
            "branch": branch,
            "path": path
        }
        if password:
            data["password"] = encrypt_password(password)
        
        if existing.data:
            supabase.table("ai_coder_deploy_configs") \
                .update(data) \
                .eq("user_id", user_id) \
                .execute()
        else:
            supabase.table("ai_coder_deploy_configs") \
                .insert(data) \
                .execute()
        
        return JSONResponse({"status": "ok", "message": "Конфигурация сохранена"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@router.get("/admin/ai-coder/deploy/config/{user_id}")
async def get_deploy_config(user_id: str):
    if not supabase:
        return JSONResponse({"error": "Supabase не настроен"}, status_code=500)
    data = supabase.table("ai_coder_deploy_configs") \
        .select("*") \
        .eq("user_id", user_id) \
        .limit(1) \
        .execute()
    if data.data:
        config = data.data[0]
        if config.get("password"):
            config["password"] = decrypt_password(config["password"])
        return JSONResponse(config)
    return JSONResponse({"error": "Конфигурация не найдена"}, status_code=404)

# ===== ВЫПОЛНЕНИЕ ДЕПЛОЯ =====
@router.post("/admin/ai-coder/deploy")
async def execute_deploy(
    request: Request,
    user_id: str = Form(...),
    history_id: str = Form(None),
    provider: str = Form(None),
    host: str = Form(None),
    username: str = Form(None),
    password: str = Form(None),
    repo: str = Form(None),
    branch: str = Form("main"),
    path: str = Form("/")
):
    try:
        # 1. Получаем конфигурацию
        if provider:
            config = {
                "provider": provider,
                "host": host,
                "username": username,
                "password": password,
                "repo": repo,
                "branch": branch,
                "path": path
            }
        else:
            config_res = supabase.table("ai_coder_deploy_configs") \
                .select("*") \
                .eq("user_id", user_id) \
                .limit(1) \
                .execute()
            config = config_res.data[0] if config_res.data else None
            if config and config.get("password"):
                config["password"] = decrypt_password(config["password"])

        if not config:
            return JSONResponse({"error": "Не найдена конфигурация деплоя"}, status_code=404)

        # 2. Получаем последний ответ AI
        if history_id:
            hist_res = supabase.table("ai_coder_history") \
                .select("*") \
                .eq("id", history_id) \
                .single() \
                .execute()
            history = hist_res.data
        else:
            hist_res = supabase.table("ai_coder_history") \
                .select("*") \
                .eq("user_id", user_id) \
                .order("created_at", desc=True) \
                .limit(1) \
                .execute()
            history = hist_res.data[0] if hist_res.data else None

        if not history:
            return JSONResponse({"error": "Не найдена история для деплоя"}, status_code=404)

        files = parse_files_from_ai(history["model_output"])
        if not files:
            return JSONResponse({"error": "Не найдены файлы в ответе AI"}, status_code=400)

        # 3. Выполняем деплой
        if config["provider"] == "ftp":
            result = deploy_to_ftp(config, files)
        elif config["provider"] == "github":
            result = deploy_to_github(config, files)
        else:
            return JSONResponse({"error": f"Неподдерживаемый провайдер: {config['provider']}"}, status_code=400)

        supabase.table("ai_coder_deploy_tasks").insert({
            "user_id": user_id,
            "status": "success",
            "result": result["message"]
        }).execute()

        return JSONResponse(result)

    except Exception as e:
        logger.exception("Deploy error")
        return JSONResponse({"error": str(e)}, status_code=500)

# ===== СКАЧИВАНИЕ ZIP =====
ALLOWED_DIR = tempfile.gettempdir()

@router.get("/admin/ai-coder/download", response_class=FileResponse)
async def download_file(path: str):
    abs_path = os.path.abspath(path)
    if not abs_path.startswith(ALLOWED_DIR) or not os.path.exists(abs_path):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    return FileResponse(abs_path, filename="fixed_project.zip", media_type="application/zip")

# ===== ИСТОРИЯ =====
@router.get("/admin/ai-coder/history/{user_id}")
async def ai_coder_history(request: Request, user_id: str):
    if not supabase:
        return {"error": "Supabase не настроен"}
    data = supabase.table("ai_coder_history") \
        .select("*") \
        .eq("user_id", user_id) \
        .order("created_at", desc=True) \
        .execute()
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
    data = supabase.table("ai_coder_history") \
        .select("*") \
        .eq("id", item_id) \
        .single() \
        .execute()
    return templates.TemplateResponse("ai_coder_history_item.html", {
        "request": request,
        "item": data.data
    })

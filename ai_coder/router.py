from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.templating import Jinja2Templates
import tempfile
import shutil
import os
import requests
import zipfile
import uuid

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

router = APIRouter()
templates = Jinja2Templates(directory="templates")


# ---------------------------
#  GET — HTML страница
# ---------------------------
@router.get("/admin/ai-coder")
def ai_coder_page(request: Request, user_id: str | None = None):
    if not user_id:
        user_id = str(uuid.uuid4())

    return templates.TemplateResponse("ai_coder.html", {
        "request": request,
        "result": None,
        "task": "",
        "download": None,
        "user_id": user_id
    })


# ---------------------------
#  Вызов модели OpenRouter
# ---------------------------
def call_model(messages):
    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://dalvideo.ru",
            "X-Title": "KVG AI Studio"
        },
        json={
            "model": "google/gemma-4-26b-a4b-it:free",
            "messages": messages
        }
    )

    data = response.json()

    if "error" in data:
        return f"❌ Ошибка OpenRouter:\n{data}"

    if "choices" not in data:
        return f"❌ Неверный ответ от OpenRouter:\n{data}"

    return data["choices"][0]["message"]["content"]


# ---------------------------
#  ZIP — распаковка и чтение всех файлов
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
            except Exception:
                continue

            files_data[relative_path] = content

    return files_data


# ---------------------------
#  Парсер исправленных файлов из ответа модели
# ---------------------------
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


# ---------------------------
#  Создание ZIP с исправленными файлами
# ---------------------------
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
#  Сохранение истории в Supabase
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


# ---------------------------
#  POST — обработка формы
# ---------------------------
@router.post("/admin/ai-coder")
async def ai_coder(
    request: Request,
    task: str = Form(...),
    file: UploadFile = File(None),
    zip: UploadFile = File(None),
    user_id: str = Form(None)
):
    if not user_id:
        user_id = str(uuid.uuid4())

    file_contents = ""
    zip_contents = {}

    if file:
        file_contents = file.file.read().decode("utf-8", errors="ignore")

    if zip and zip.filename:
        try:
            zip_contents = extract_zip_and_read(zip)
        except zipfile.BadZipFile:
            zip_contents = {"error": "Файл не является ZIP-архивом"}

    messages = [
        {
            "role": "user",
            "content": (
                f"User ID: {user_id}\n"
                f"Task: {task}\n\n"
                f"Single file contents:\n{file_contents}\n\n"
                f"ZIP project contents:\n" +
                "\n".join(
                    f"--- {path} ---\n{content}\n"
                    for path, content in zip_contents.items()
                ) +
                "\n\n"
                "Исправь ошибки, оптимизируй код и верни исправленные файлы "
                "в формате:\n--- path/to/file.py ---\n<исправленный код>"
            )
        }
    ]

    result = call_model(messages)

    save_history(
        user_id=user_id,
        task=task,
        file_names=file.filename if file else "",
        zip_files=zip.filename if zip else "",
        model_output=result
    )

    fixed_files = parse_fixed_files(result)
    fixed_zip_path = create_fixed_zip(fixed_files)

    download_url = f"/admin/ai-coder/download?path={fixed_zip_path}"

    return templates.TemplateResponse("ai_coder.html", {
        "request": request,
        "result": result,
        "task": task,
        "download": download_url,
        "user_id": user_id
    })


# ---------------------------
#  История — список
# ---------------------------
@router.get("/admin/ai-coder/history/{user_id}")
def ai_coder_history(request: Request, user_id: str):
    if not supabase:
        return {"error": "Supabase не настроен"}

    data = (
        supabase.table("ai_coder_history")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", ascending=False)
        .execute()
    )

    return templates.TemplateResponse("ai_coder_history.html", {
        "request": request,
        "items": data.data,
        "user_id": user_id
    })


# ---------------------------
#  История — один элемент
# ---------------------------
@router.get("/admin/ai-coder/history/item/{item_id}")
def ai_coder_history_item(request: Request, item_id: str):
    if not supabase:
        return {"error": "Supabase не настроен"}

    data = (
        supabase.table("ai_coder_history")
        .select("*")
        .eq("id", item_id)
        .single()
        .execute()
    )

    return templates.TemplateResponse("ai_coder_history_item.html", {
        "request": request,
        "item": data.data
    })


# ---------------------------
#  Скачивание результата
# ---------------------------
@router.get("/admin/ai-coder/download")
def download_file(path: str):
    with open(path, "rb") as f:
        return f.read()

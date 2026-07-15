from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.templating import Jinja2Templates
import tempfile
import shutil
import os
import requests
import zipfile

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

router = APIRouter()
templates = Jinja2Templates(directory="templates")


# ---------------------------
#  GET — HTML страница
# ---------------------------
@router.get("/admin/ai-coder")
def ai_coder_page(request: Request):
    return templates.TemplateResponse("admin_ai_coder.html", {
        "request": request,
        "result": None,
        "task": "",
        "download": None
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
    """
    Распаковывает ZIP во временную директорию и возвращает словарь:
    {
        "path/inside/zip.py": "file contents...",
        "folder/utils.py": "file contents..."
    }
    """
    temp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(temp_dir, "uploaded.zip")

    # Сохраняем ZIP во временный файл
    with open(zip_path, "wb") as f:
        shutil.copyfileobj(zip_file.file, f)

    files_data = {}

    # Распаковка
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(temp_dir)

    # Обход всех файлов внутри ZIP
    for root, dirs, files in os.walk(temp_dir):
        for filename in files:
            full_path = os.path.join(root, filename)
            relative_path = os.path.relpath(full_path, temp_dir)

            # Пробуем прочитать как текст
            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except:
                # Бинарные файлы пропускаем
                continue

            files_data[relative_path] = content

    return files_data


# ---------------------------
#  POST — обработка формы
# ---------------------------
@router.post("/admin/ai-coder")
async def ai_coder(
    request: Request,
    task: str = Form(...),
    file: UploadFile = File(None),
    zip: UploadFile = File(None)
):
    file_contents = ""
    zip_contents = {}

    # Чтение одиночного файла
    if file:
        file_contents = file.file.read().decode("utf-8", errors="ignore")

    # Чтение ZIP — распаковка и анализ всех файлов
    zip_contents = {}

if zip and zip.filename:
    try:
        zip_contents = extract_zip_and_read(zip)
    except zipfile.BadZipFile:
        zip_contents = {"error": "Файл не является ZIP-архивом"}

    # Формируем сообщение для модели
    messages = [
        {
            "role": "user",
            "content": (
                f"Task: {task}\n\n"
                f"Single file contents:\n{file_contents}\n\n"
                f"ZIP project contents:\n" +
                "\n".join(
                    f"--- {path} ---\n{content}\n"
                    for path, content in zip_contents.items()
                )
            )
        }
    ]

    result = call_model(messages)

    # Сохраняем результат в файл
    result_file = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
    result_file.write(result.encode("utf-8"))
    result_file.close()

    download_url = f"/admin/ai-coder/download?path={result_file.name}"

    return templates.TemplateResponse("admin_ai_coder.html", {
        "request": request,
        "result": result,
        "task": task,
        "download": download_url
    })


# ---------------------------
#  Скачивание результата
# ---------------------------
@router.get("/admin/ai-coder/download")
def download_file(path: str):
    with open(path, "rb") as f:
        return f.read()

from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.templating import Jinja2Templates
import tempfile
import shutil
import os
import requests

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

    # Ошибка от OpenRouter
    if "error" in data:
        return f"❌ Ошибка OpenRouter:\n{data}"

    if "choices" not in data:
        return f"❌ Неверный ответ от OpenRouter:\n{data}"

    return data["choices"][0]["message"]["content"]


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
    temp_paths = []

    # Обработка одиночного файла
    if file:
        temp_file = tempfile.NamedTemporaryFile(delete=False)
        shutil.copyfileobj(file.file, temp_file)
        temp_paths.append(temp_file.name)

    # Обработка ZIP
    if zip:
        temp_zip = tempfile.NamedTemporaryFile(delete=False)
        shutil.copyfileobj(zip.file, temp_zip)
        temp_paths.append(temp_zip.name)

    # Формируем сообщение для модели
    messages = [
        {
            "role": "user",
            "content": f"Task: {task}\nFiles: {temp_paths}"
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

from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.templating import Jinja2Templates
import tempfile
import shutil
import os
import requests

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/admin/ai-coder")
def ai_coder_page(request: Request):
    return templates.TemplateResponse("admin_ai_coder.html", {
        "request": request,
        "result": None,
        "task": "",
        "download": None
    })


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


@router.post("/admin/ai-coder")
async def ai_coder(
    request: Request,
    task: str = Form(...),
    file: UploadFile = File(None),
    zip: UploadFile = File(None)
):
    file_contents = ""
    zip_contents = ""

    # Чтение одиночного файла
    if file:
        file_contents = file.file.read().decode("utf-8", errors="ignore")

    # Чтение ZIP как текст (позже сделаем распаковку)
    if zip:
        zip_contents = zip.file.read().decode("utf-8", errors="ignore")

    messages = [
        {
            "role": "user",
            "content": f"Task: {task}\n\nFile contents:\n{file_contents}\n\nZIP contents:\n{zip_contents}"
        }
    ]

    result = call_model(messages)

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


@router.get("/admin/ai-coder/download")
def download_file(path: str):
    with open(path, "rb") as f:
        return f.read()

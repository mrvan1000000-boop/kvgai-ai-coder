import os
import tempfile
import zipfile
import requests

from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import FileResponse
from dotenv import load_dotenv

load_dotenv()

router = APIRouter(prefix="/admin/ai-coder", tags=["AI-Coder"])

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")


def call_qwen(messages):
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

    # Обработка ошибок
    if "error" in data:
        return f"❌ Ошибка OpenRouter:\n{data}"

    if "choices" not in data:
        return f"❌ Неверный ответ от OpenRouter:\n{data}"

    return data["choices"][0]["message"]["content"]





def read_uploaded_file(upload: UploadFile):
    return upload.file.read().decode("utf-8")


def extract_zip(upload: UploadFile):
    temp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(temp_dir, "archive.zip")

    with open(zip_path, "wb") as f:
        f.write(upload.file.read())

    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(temp_dir)

    files = {}
    for root, _, filenames in os.walk(temp_dir):
        for name in filenames:
            path = os.path.join(root, name)
            rel_path = os.path.relpath(path, temp_dir)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    files[rel_path] = f.read()
            except Exception:
                continue

    return files, temp_dir


@router.post("")
async def ai_coder(
    task: str = Form(...),
    file: UploadFile = File(None),
    zip_file: UploadFile = File(None)
):
    context = ""
    temp_dir = tempfile.mkdtemp()

    if file:
        context = read_uploaded_file(file)

    elif zip_file:
        files, temp_dir = extract_zip(zip_file)
        context = "\n\n".join([f"### {name}\n{content}" for name, content in files.items()])

    prompt = f"""
Ты — Senior-разработчик.
Проанализируй код и выполни задачу.

Задача:
{task}

Контекст:
{context}

Если нужно — верни полный исправленный файл.
Если задача требует нового файла — создай его полностью.
"""

    result = call_qwen([
        {"role": "user", "content": prompt}
    ])

    output_path = os.path.join(temp_dir, "result.txt")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(result)

    return {
        "result": result,
        "download": f"/admin/ai-coder/download?path={output_path}"
    }


@router.get("/download")
def download_file(path: str):
    return FileResponse(path, filename=os.path.basename(path))

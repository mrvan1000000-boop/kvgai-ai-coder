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

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if (SUPABASE_URL and SUPABASE_KEY) else None

# Mock/Real Clients
from groq import Groq
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

router = APIRouter()
templates = Jinja2Templates(directory="templates")

AVAILABLE_MODELS = [
    "openrouter:google/gemma-4-26b-a4b-it:free",
    "openrouter:qwen/qwen3-coder:free",
    "groq:llama-3.3-70b-versatile",
]

# --- UTILS ---

def parse_files_from_ai(content: str) -> dict:
    """Парсит контент вида --- path/to/file.py --- <code}"""
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

async def call_ai(messages: list, model: str):
    # Упрощенная логика выбора провайдера
    if model.startswith("openrouter:"):
        model_name = model.split(":", 1)[1]
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            json={"model": model_name, "messages": messages},
            timeout=60
        )
        return resp.json()["choices"][0]["message"]["content"]
    elif model.startswith("groq:"):
        model_name = model.split(":", 1)[1]
        completion = groq_client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=0.2
        )
        return completion.choices[0].message.content
    return None

# --- ROUTES ---

@router.get("/admin/ai-coder")
async def ai_coder_page(request: Request, user_id: str | None = None):
    # Генерация UID если его нет (для чата)
    uid = user_id or str(uuid.uuid4())
    return templates.TemplateResponse("ai_coder.html", {
        "request": request,
        "user_id": uid,
        "available_models": AVAILABLE_MODELS,
        "selected_model": AVAILABLE_MODELS[0]
    })

@router.post("/admin/ai-coder/api")
async def ai_coder_api(
    request: Request, 
    task: str = Form(...), 
    file: UploadFile = File(None), 
    model: str = Form(None), 
    user_id: str = Form(None)
):
    uid = user_id or str(uuid.uuid4())
    selected_model = model if model in AVAILABLE_MODELS else AVAILABLE_MODELS[0]
    
    # 1. Сбор контекста из файлов
    context_content = ""
    file_names = []
    
    if file:
        # Читаем одиночный файл
        content = await file.read()
        context_content += f"File: {file.filename}\nContent:\n{content.decode('utf-8', errors='ignore')}\n\n"
        file_names.append(file.filename)
    
    # Здесь можно добавить логику распаковки ZIP (аналогично вашему extract_zip_and_read)

    # 2. Запрос к LLM
    messages = [
        {"role": "system", "content": "You are an expert coder. Return code using format: --- path/to/file.py --- \n<code>"},
        {"role": "user", "content": f"Task: {task}\n\n{context_content}"}
    ]
    
    ai_response = await call_ai(messages, selected_model)
    
    if not ai_response:
        return JSONResponse({"error": "AI failed to respond"}, status_code=500)

    # 3. Сохранение в историю (для агента)
    # Мы записываем не только ответ, но и помечаем, что это ПРЯМОЙ запрос на изменение кода
    supabase.table("ai_coder_history").insert({
        "user_id": uid,
        "task": task,
        "file_names": str(file_names),
        "model_output": ai_response
    }).execute()

    # 4. КРИТИЧЕСКИЙ ШАГ: Создание задачи для Git-агента
    # Мы передаем задачу в Supabase, чтобы git_agent.py её подхватил
    supabase.table("ai_coder_tasks").insert({
        "user_id": uid,
        "file_path": "project_root", # Для инструкций агенту
        "prompt": task,
        "status": "pending"
    }).execute()

    return JSONResponse({
        "result": ai_response,
        "user_id": uid,
        "status": "task_queued"
    })

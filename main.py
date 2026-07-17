import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from ai_coder.router import router as ai_coder_router

app = FastAPI()

# Подключаем роутер
app.include_router(ai_coder_router)

# Монтируем статику только если папка существует
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")
else:
    # Создаем пустую папку, чтобы FastAPI не ругался при запуске
    os.makedirs("static", exist_ok=True)

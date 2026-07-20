import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from ai_coder.router import router as ai_coder_router

app = FastAPI()

# Подключаем роутер
app.include_router(ai_coder_router)

# Если нужны статики — можно подключить
# app.mount("/static", StaticFiles(directory="static"), name="static")

# Railway запускает uvicorn сам, поэтому блок if __name__ == "__main__" НЕ нужен

import os
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from ai_coder.router import router as ai_coder_router

app = FastAPI()

# Настройка статики и шаблонов
app.mount("/static", StaticFiles(directory="static"), name="static")

if not os.path.exists("templates"):
    os.makedirs("templates")
Jinja2Templates.mount("templates", directory="templates")

# Подключаем основной роутер (где вся логика AI)
app.include_router(ai_coder_router)

@app.get("/")
async def read_root(request: Request):
    # Перенаправляем на основной интерфейс чата
    return await ai_coder_router.ai_coder_page(request, user_id=None)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))

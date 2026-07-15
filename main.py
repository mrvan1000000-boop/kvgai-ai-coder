from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

from ai_coder_router import router as ai_coder_router

app = FastAPI()

# подключаем роутер
app.include_router(ai_coder_router)

# подключаем шаблоны
templates = Jinja2Templates(directory="templates")

# страница админки
@app.get("/admin/ai-coder/page")
def admin_ai_coder_page(request: Request):
    return templates.TemplateResponse("admin_ai_coder.html", {"request": request})

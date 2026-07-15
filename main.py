from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

from ai_coder.router import router as ai_coder_router

app = FastAPI()

app.include_router(ai_coder_router)

templates = Jinja2Templates(directory="templates")

@app.get("/admin/ai-coder/page")
def admin_ai_coder_page(request: Request):
    return templates.TemplateResponse("admin_ai_coder.html", {"request": request})

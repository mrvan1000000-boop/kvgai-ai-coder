import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from ai_coder.router import router as ai_coder_router

app = FastAPI()

app.include_router(ai_coder_router)

if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

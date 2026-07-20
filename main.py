import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from ai_coder.router import router as ai_coder_router

app = FastAPI(title="KVG AI Coder")

app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(ai_coder_router)

@app.get("/")
async def root():
    return RedirectResponse(url="/admin/ai-coder")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

import os
import time
import base64
import logging
import requests
from supabase import create_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("git_agent")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GH_TOKEN = os.getenv("GITHUB_TOKEN")
REPO = os.getenv("GITHUB_REPO", "mrvan1000000-boop/kvgai-ai-coder")
BRANCH = os.getenv("GITHUB_BRANCH", "main")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

try:
    from groq import Groq
    groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
except Exception:
    groq_client = None

sb = create_client(SUPABASE_URL, SUPABASE_KEY) if (SUPABASE_URL and SUPABASE_KEY) else None

AVAILABLE_MODELS = [
    "openrouter:google/gemma-4-26b-a4b-it:free",
    "openrouter:qwen/qwen3-coder:free",
    "groq:llama-3.3-70b-versatile"
]

GH_HEADERS = {
    "Authorization": f"Bearer {GH_TOKEN}",
    "User-Agent": "kvgai-ai-coder-agent"
}

def call_openrouter(model, messages, timeout=20):
    r = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "kvgai-agent"
        },
        json={"model": model, "messages": messages},
        timeout=timeout
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def call_groq(model, messages, timeout=20):
    c = groq_client.chat.completions.create(
        model=model, messages=messages, temperature=0.3, max_tokens=4096, timeout=timeout
    )
    return c.choices[0].message.content

def _clean_code(text: str) -> str:
    if not text:
        return ""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("python"):
            text = text[len("python"):]
        text = text.strip()
    return text

def fix_with_ai(file_text, prompt):
    msgs = [{
        "role": "user",
        "content": f"{prompt}\n\n--- FILE ---\n{file_text}\n\nВерни только исправленный код без пояснений."
    }]
    for m in AVAILABLE_MODELS:
        try:
            prov, name = m.split(":", 1)
            if prov == "openrouter" and OPENROUTER_API_KEY:
                return _clean_code(call_openrouter(name, msgs))
            if prov == "groq" and groq_client:
                return _clean_code(call_groq(name, msgs))
        except Exception as e:
            logger.warning(f"{m} fail: {e}")
    return None

def get_task():
    if not sb:
        return None
    r = sb.table("ai_coder_tasks").select("*").eq("status", "pending").limit(1).execute()
    return r.data[0] if r.data else None

def gh_get(path):
    url = f"https://api.github.com/repos/{REPO}/contents/{path}?ref={BRANCH}"
    return requests.get(url, headers=GH_HEADERS, timeout=15).json()

def gh_put(path, content_b64, sha):
    url = f"https://api.github.com/repos/{REPO}/contents/{path}"
    data = {
        "message": f"AI auto-fix: {path}",
        "content": content_b64,
        "sha": sha,
        "branch": BRANCH
    }
    return requests.put(url, headers=GH_HEADERS, json=data, timeout=15)

def process():
    t = get_task()
    if not t:
        return
    sb.table("ai_coder_tasks").update({"status": "running"}).eq("id", t["id"]).execute()
    try:
        f = gh_get(t["file_path"])
        if "content" not in f:
            raise Exception(f.get("message", "file not found"))
        old_text = base64.b64decode(f["content"]).decode("utf-8")
        new_text = fix_with_ai(old_text, t["prompt"])
        if not new_text:
            raise Exception("AI return empty")
        b64 = base64.b64encode(new_text.encode("utf-8")).decode()
        gh_put(t["file_path"], b64, f["sha"])
        sb.table("ai_coder_tasks").update({"status": "done"}).eq("id", t["id"]).execute()
        logger.info(f"Fixed {t['file_path']}")
    except Exception as e:
        logger.exception("task error")
        sb.table("ai_coder_tasks").update({"status": "error", "prompt": str(e)}).eq("id", t["id"]).execute()

if __name__ == "__main__":
    while True:
        try:
            process()
        except Exception:
            logger.exception("loop error")
        time.sleep(25)

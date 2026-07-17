import os, requests, time
from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GH_TOKEN = os.getenv("GITHUB_TOKEN")
REPO = os.getenv("GITHUB_REPO")
BRANCH = os.getenv("GITHUB_BRANCH", "main")

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_task():
    r = sb.table("ai_coder_tasks").select("*").eq("status", "pending").limit(1).execute()
    return r.data[0] if r.data else None

def github_get(path):
    url = f"https://api.github.com/repos/{REPO}/contents/{path}?ref={BRANCH}"
    h = {"Authorization": f"Bearer {GH_TOKEN}"}
    return requests.get(url, headers=h).json()

def github_put(path, content_b64, sha):
    url = f"https://api.github.com/repos/{REPO}/contents/{path}"
    h = {"Authorization": f"Bearer {GH_TOKEN}"}
    data = {
        "message": f"AI auto-fix: {path}",
        "content": content_b64,
        "sha": sha,
        "branch": BRANCH
    }
    return requests.put(url, headers=h, json=data)

def process():
    t = get_task()
    if not t: return
    sb.table("ai_coder_tasks").update({"status":"running"}).eq("id", t["id"]).execute()
    try:
        f = github_get(t["file_path"])
        # тут должен быть вызов ИИ (Groq/OpenRouter) для правки f["content"]
        # упрощённо: просто пушим обратно (пример)
        github_put(t["file_path"], f["content"], f["sha"])
        sb.table("ai_coder_tasks").update({"status":"done"}).eq("id", t["id"]).execute()
    except Exception as e:
        sb.table("ai_coder_tasks").update({"status":"error","prompt":str(e)}).eq("id", t["id"]).execute()

if __name__ == "__main__":
    while True:
        process()
        time.sleep(30)

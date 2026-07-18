import os
import time
import logging
from github import Github
from supabase import create_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GitAgent")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = os.getenv("GITHUB_REPO")
BRANCH = os.getenv("GITHUB_BRANCH", "main")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
github_client = Github(GITHUB_TOKEN)

def process_task(task_id, task_data):
    try:
        repo = github_client.get_repo(REPO_NAME)
        
        # Получаем последний ответ нейросети для этого пользователя
        # Это гарантирует, что агент берет именно то, что выдал чат
        history = supabase.table("ai_coder_history") \
            select("*") \
            eq("user_id", task_data['user_id']) \
            order("created_at", desc=True) \
            limit(1).execute()

        if not history.data:
            return "No history found"

        model_output = history.data[0]["model_output"]
        
        # Парсинг файлов
        files_to_update = {}
        current_path = None
        buffer = []
        for line in model_output.splitlines():
            if line.startswith("--- ") and line.endswith(" ---"):
                if current_path: files_to_update[current_path] = "\n".join(buffer)
                current_path = line[4:-4].strip()
                buffer = []
            elif current_path:
                buffer.append(line)
        if current_path: files_to_update[current_path] = "\n".join(buffer)

        # Применяем изменения в GitHub
        for path, content in files_to_update.items():
            try:
                file = repo.get_contents(path, ref=BRANCH)
                repo.update_file(path=path, message=f"AI-Coder: Auto-fix", content=content, sha=file.sha, branch=BRANCH)
                logger.info(f"Updated {path}")
            except:
                repo.create_file(path=path, message=f"AI-Coder: Create", content=content, branch=BRANCH)
                logger.info(f"Created {path}")

        return "Success"
    except Exception as e:
        logger.error(f"Agent error: {e}")
        return str(e)

def main():
    logger.info("Agent started...")
    while True:
        # Ищем задачи со статусом pending
        response = supabase.table("ai_coder_tasks").select("*").eq("status", "pending").limit(1).execute()
        
        if response.data:
            task = response.data[0]
            task_id = task['id']
            
            # Ставим статус в обработку
            supabase.table("ai_coder_tasks").update({"status": "processing"}).eq("id", task_id).execute()
            
            result = process_task(task_id, task)
            
            # Ставим статус completed
            status = "done" if "Success" in result or "Updated" in result or "Created" in result else "failed"
            supabase.table("ai_coder_tasks").update({"status": status, "result": result}).eq("id", task_id).execute()
            
        time.sleep(5)

if __name__ == "__main__":
    main()

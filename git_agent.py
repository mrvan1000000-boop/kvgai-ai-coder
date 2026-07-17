import os
import time
import logging
import requests
from github import Github, GithubException
from supabase import create_client, Client

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = os.getenv("GITHUB_REPO", "user/repo")
BRANCH = os.getenv("GITHUB_BRANCH", "main")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if (SUPABASE_URL and SUPABASE_KEY) else None
github_client = Github(GITHUB_TOKEN) if GITHUB_TOKEN else None

try:
    from groq import Groq
    groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
except:
    groq_client = None

def get_repo():
    if not github_client:
        raise Exception("GitHub token not configured")
    return github_client.get_repo(REPO_NAME)

def call_neural_network(prompt: str) -> str:
    """Генерирует код по промпту (fallback openrouter -> groq)."""
    # Пробуем OpenRouter
    if OPENROUTER_API_KEY:
        try:
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "google/gemma-4-26b-a4b-it:free",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "max_tokens": 4096
                },
                timeout=60
            )
            resp.raise_for_status()
            data = resp.json()
            if data["choices"]:
                return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning(f"OpenRouter failed: {e}")
    # Пробуем Groq
    if groq_client:
        try:
            completion = groq_client.chat.completions.create(
                model="gemma2-9b-it",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=4096
            )
            return completion.choices[0].message.content
        except Exception as e:
            logger.warning(f"Groq failed: {e}")
    raise Exception("No AI backend available")

def parse_content(content: str) -> dict:
    files = {}
    current_path = None
    buffer = []
    for line in content.split('\n'):
        if line.startswith('--- ') and line.endswith(' ---'):
            if current_path and buffer:
                files[current_path] = '\n'.join(buffer)
            current_path = line[4:-4].strip()
            buffer = []
        elif current_path is not None:
            buffer.append(line)
    if current_path and buffer:
        files[current_path] = '\n'.join(buffer)
    return files

def process_task(task):
    task_id = task["id"]
    user_id = task["user_id"]
    file_path = task.get("file_path", "project")
    prompt = task.get("prompt", "")

    logger.info(f"Processing task {task_id} for user {user_id}")
    logger.info(f"Target file: {file_path}")
    logger.info(f"Prompt: {prompt}")

    # Пытаемся получить ответ из истории (если задача создана через UI)
    model_output = None
    if supabase:
        history = supabase.table("ai_coder_history").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(1).execute()
        if history.data:
            model_output = history.data[0]["model_output"]
            logger.info("Found existing model output in history")

    # Если нет – генерируем сами
    if not model_output:
        logger.info("No history found, generating code via neural network...")
        ai_prompt = f"""You are a code generator. The user wants to create or modify file '{file_path}'.
Task: {prompt}

Return ONLY the corrected code in the following format:
--- {file_path} ---
<code content>

Do not include any additional text."""
        model_output = call_neural_network(ai_prompt)
        logger.info(f"Generated code of length {len(model_output)}")

    files_to_update = parse_content(model_output)
    if not files_to_update:
        logger.warning("No file paths found, using whole response as single file")
        files_to_update = {file_path: model_output}

    repo = get_repo()
    updated_files = []

    for rel_path, content in files_to_update.items():
        try:
            try:
                existing = repo.get_contents(rel_path, ref=BRANCH)
                repo.update_file(
                    path=rel_path,
                    message=f"AI Coder: Update {rel_path}",
                    content=content,
                    sha=existing.sha,
                    branch=BRANCH
                )
                logger.info(f"✅ Updated: {rel_path}")
                updated_files.append(f"Updated: {rel_path}")
            except GithubException as e:
                if e.status == 404:
                    # Создаём файл (директории создаются автоматически через create_file)
                    repo.create_file(
                        path=rel_path,
                        message=f"AI Coder: Create {rel_path}",
                        content=content,
                        branch=BRANCH
                    )
                    logger.info(f"✅ Created: {rel_path}")
                    updated_files.append(f"Created: {rel_path}")
                else:
                    raise
        except Exception as e:
            logger.error(f"❌ Failed to process {rel_path}: {e}")
            updated_files.append(f"Failed: {rel_path} - {str(e)}")

    result_text = "\n".join(updated_files) if updated_files else "No files were modified"
    if supabase:
        supabase.table("ai_coder_tasks").update({
            "status": "done",
            "result": result_text
        }).eq("id", task_id).execute()
    logger.info(f"Task {task_id} completed: {result_text}")

def main():
    logger.info(f"Starting Git Agent for repo: {REPO_NAME}, branch: {BRANCH}")
    while True:
        try:
            if supabase:
                response = supabase.table("ai_coder_tasks").select("*").eq("status", "pending").limit(1).execute()
                tasks = response.data if response.data else []
            else:
                tasks = []

            if tasks:
                task = tasks[0]
                logger.info(f"Found task: id={task['id']}")
                supabase.table("ai_coder_tasks").update({"status": "processing"}).eq("id", task["id"]).execute()
                process_task(task)
            else:
                logger.debug("No pending tasks, sleeping...")
        except Exception as e:
            logger.error(f"Main loop error: {e}", exc_info=True)
            time.sleep(5)
        time.sleep(10)

if __name__ == "__main__":
    main()

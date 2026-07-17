import os
import time
import logging
from github import Github, GithubException
from supabase import create_client, Client

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Конфигурация
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = os.getenv("GITHUB_REPO", "user/repo")  # Например "ivan/project"
BRANCH = os.getenv("GITHUB_BRANCH", "main")

# Инициализация клиентов
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if (SUPABASE_URL and SUPABASE_KEY) else None
github_client = Github(GITHUB_TOKEN) if GITHUB_TOKEN else None

def get_repo():
    """Получает репозиторий GitHub"""
    if not github_client:
        raise Exception("GitHub token not configured")
    try:
        return github_client.get_repo(REPO_NAME)
    except Exception as e:
        logger.error(f"Cannot access repo {REPO_NAME}: {e}")
        raise

def parse_content(content: str) -> dict:
    """Парсит ответ нейросети в словарь {путь: содержимое}"""
    files = {}
    current_path = None
    buffer = []
    
    for line in content.split('\n'):
        if line.startswith('--- ') and line.endswith(' ---'):
            # Сохраняем предыдущий файл
            if current_path and buffer:
                files[current_path] = '\n'.join(buffer)
            current_path = line[4:-4].strip()
            buffer = []
        elif current_path is not None:
            buffer.append(line)
    
    # Сохраняем последний файл
    if current_path and buffer:
        files[current_path] = '\n'.join(buffer)
    
    return files

def process_task(task):
    """Обрабатывает одну задачу"""
    task_id = task["id"]
    user_id = task["user_id"]
    file_path = task.get("file_path", "project")
    prompt = task.get("prompt", "")
    
    logger.info(f"Processing task {task_id} for user {user_id}")
    logger.info(f"File path: {file_path}")
    logger.info(f"Prompt: {prompt}")
    
    # Получаем ответ нейросети из истории
    history = supabase.table("ai_coder_history").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(1).execute()
    if not history.data:
        logger.error(f"No history found for user {user_id}")
        return
    
    model_output = history.data[0]["model_output"]
    logger.info(f"Model output length: {len(model_output)} chars")
    
    # Парсим файлы из ответа
    files_to_update = parse_content(model_output)
    logger.info(f"Parsed {len(files_to_update)} files from response")
    
    if not files_to_update:
        logger.warning("No files found in response, using whole response as single file")
        files_to_update = {file_path: model_output}
    
    repo = get_repo()
    updated_files = []
    
    for rel_path, content in files_to_update.items():
        try:
            # Проверяем, существует ли файл в репозитории
            try:
                existing = repo.get_contents(rel_path, ref=BRANCH)
                # Файл существует - обновляем
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
                    # Файл не существует - создаём
                    # Определяем путь до директории
                    dir_path = os.path.dirname(rel_path)
                    if dir_path:
                        # Создаём директории если нужно (через пустой файл .gitkeep)
                        try:
                            repo.create_file(
                                path=f"{dir_path}/.gitkeep",
                                message=f"AI Coder: Create directory {dir_path}",
                                content="",
                                branch=BRANCH
                            )
                        except:
                            pass  # Директория уже существует
                    
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
    
    # Обновляем статус задачи
    result_text = "\n".join(updated_files) if updated_files else "No files were modified"
    supabase.table("ai_coder_tasks").update({
        "status": "done",
        "result": result_text
    }).eq("id", task_id).execute()
    
    logger.info(f"Task {task_id} completed: {result_text}")

def main():
    """Главный цикл агента"""
    logger.info(f"Starting Git Agent for repo: {REPO_NAME}, branch: {BRANCH}")
    logger.info(f"Supabase URL: {SUPABASE_URL}")
    logger.info(f"GitHub configured: {bool(GITHUB_TOKEN)}")
    
    while True:
        try:
            # Проверяем подключение к GitHub
            if github_client:
                try:
                    user = github_client.get_user()
                    logger.debug(f"GitHub authenticated as: {user.login}")
                except Exception as e:
                    logger.error(f"GitHub auth failed: {e}")
            
            # Ищем новые задачи
            response = supabase.table("ai_coder_tasks").select("*").eq("status", "pending").limit(1).execute()
            tasks = response.data if response.data else []
            
            if tasks:
                task = tasks[0]
                logger.info(f"Found task: id={task['id']}")
                
                # Меняем статус на processing
                supabase.table("ai_coder_tasks").update({"status": "processing"}).eq("id", task["id"]).execute()
                
                # Обрабатываем
                process_task(task)
            else:
                logger.debug("No pending tasks, sleeping...")
                
        except Exception as e:
            logger.error(f"Main loop error: {e}")
            time.sleep(5)
        
        time.sleep(10)  # Проверяем каждые 10 секунд

if __name__ == "__main__":
    main()

import os
import subprocess
import sys

def run_command(command):
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    if result.returncode!= 0:
        print(f"Error: {result.stderr}")
        return False
    return True

def update_repo(file_path, new_content):
    """
    Записывает новый контент в файл и пушит изменения в GitHub
    """
    # 1. Записываем новый контент в файл
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    print(f"✅ Файл {file_path} обновлен локально.")
    
    # 2. Git команды
    print("🚀 Отправка изменений в GitHub...")
    commands = [
        "git add.",
        "git commit -m 'AI-Coder: автоматическое обновление кода'",
        "git push"
    ]
    
    for cmd in commands:
        if not run_command(cmd):
            print(f"❌ Ошибка при выполнении: {cmd}")
            return False
            
    print("🎉 Успешно обновлено в репозитории!")
    return True

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Использование: python ai_updater.py <путь_к_файлу> '<новый_код>'")
    else:
        target_file = sys.argv[1]
        content = sys.argv[2]
        update_repo(target_file, content)

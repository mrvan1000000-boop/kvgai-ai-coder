import os

def read_file_content(path: str) -> str:
    """Reads a single file and returns its content."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def write_file_content(path: str, content: str):
    """Writes content to a file, creating directories if needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

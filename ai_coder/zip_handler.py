import zipfile
import tempfile
import os
import shutil

def extract_zip_to_dict(zip_file) -> dict:
    """Extracts a ZIP file and returns a dict {relative_path: content}."""
    temp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(temp_dir, "uploaded.zip")
    files = {}
    try:
        with open(zip_path, "wb") as f:
            shutil.copyfileobj(zip_file.file, f)
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(temp_dir)
        for root, _, filenames in os.walk(temp_dir):
            for fn in filenames:
                if fn == "uploaded.zip":
                    continue
                p = os.path.join(root, fn)
                rel = os.path.relpath(p, temp_dir)
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        files[rel] = f.read()
                except (UnicodeDecodeError, IOError):
                    continue
    except Exception as e:
        raise e
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return files

def create_fixed_zip(files: dict) -> tuple:
    """Creates a ZIP archive from a dict {path: content}. Returns (file_path, filename)."""
    import uuid
    temp_dir = tempfile.mkdtemp()
    zip_name = f"fixed_{uuid.uuid4().hex[:8]}.zip"
    zip_path = os.path.join(temp_dir, zip_name)
    with zipfile.ZipFile(zip_path, "w") as zf:
        for rel_path, content in files.items():
            zf.writestr(rel_path, content)
    return zip_path, zip_name

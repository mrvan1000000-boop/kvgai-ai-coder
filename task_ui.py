import os
from flask import Flask, request, redirect, render_template_string
from supabase import create_client

app = Flask(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

HTML = """
<!doctype html>
<title>KVG AI Coder — задачи</title>
<h2>Новая задача для AI</h2>
<form method=post>
  Путь к файлу:<br>
  <input name=file_path value="ai_coder/router.py" size=40><br><br>
  Что сделать:<br>
  <textarea name=prompt rows=4 cols=50>исправь ошибки и оптимизируй</textarea><br><br>
  <button type=submit>Отправить</button>
</form>
<hr>
<h3>Последние задачи</h3>
<ul>
{% for t in tasks %}
  <li>[{{t.status}}] {{t.file_path}} — {{t.prompt[:40]}}</li>
{% endfor %}
</ul>
"""

@app.route("/", methods=["GET"])
def index():
    tasks = sb.table("ai_coder_tasks").select("*").order("created_at", desc=True).limit(10).execute().data
    return render_template_string(HTML, tasks=tasks)

@app.route("/", methods=["POST"])
def add():
    fp = request.form["file_path"]
    pr = request.form["prompt"]
    sb.table("ai_coder_tasks").insert({"file_path": fp, "prompt": pr, "status": "pending"}).execute()
    return redirect("/")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

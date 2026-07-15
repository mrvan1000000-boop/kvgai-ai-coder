from fastapi.responses import JSONResponse

@router.post("/admin/ai-coder/api")
async def ai_coder_api(
    task: str = Form(...),
    file: UploadFile = File(None),
    model: str = Form(None),
    user_id: str = Form(None)
):
    if not user_id:
        user_id = str(uuid.uuid4())

    if not model:
        model = AVAILABLE_MODELS[0]

    file_contents = ""
    zip_contents = {}
    file_name = ""
    is_zip = False

    if file and file.filename:
        file_name = file.filename
        if file.filename.lower().endswith('.zip') or file.content_type == 'application/zip':
            is_zip = True
            try:
                zip_contents = extract_zip_and_read(file)
            except zipfile.BadZipFile:
                zip_contents = {"error": "Файл не является ZIP-архивом"}
        else:
            file_contents = file.file.read().decode("utf-8", errors="ignore")

    history = load_messages(user_id)

    current_message = {
        "role": "user",
        "content": (
            f"User ID: {user_id}\n"
            f"Task: {task}\n\n"
            f"Single file contents:\n{file_contents}\n\n"
            f"ZIP project contents:\n" +
            "\n".join(
                f"--- {path} ---\n{content}\n"
                for path, content in zip_contents.items()
            ) +
            "\n\n"
            "Исправь ошибки, оптимизируй код и верни исправленные файлы "
            "в формате:\n--- path/to/file.py ---\n<исправленный код>"
        )
    }

    messages = history + [current_message]

    result = call_model_with_fallback(messages, model)

    save_message(user_id, "user", current_message["content"])
    save_message(user_id, "assistant", result)

    save_history(
        user_id=user_id,
        task=task,
        file_names=file_name if not is_zip else "",
        zip_files=file_name if is_zip else "",
        model_output=result
    )

    fixed_files = parse_fixed_files(result)
    fixed_zip_path = create_fixed_zip(fixed_files)

    download_url = f"/admin/ai-coder/download?path={fixed_zip_path}"

    return JSONResponse({
        "result": result,
        "download_url": download_url,
        "user_id": user_id
    })

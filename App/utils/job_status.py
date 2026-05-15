# Helpers de status dos jobs com escrita atômica e API compatível com o app.py
import json
import os
import tempfile
from pathlib import Path


def get_status_file(project_path, job_type):
    return Path(project_path) / f"{job_type}_status.json"


def build_idle_status(job_type=None):
    return {
        "job_type": job_type or "",
        "state": "idle",
        "progress": 0,
        "message": "Aguardando.",
        "detail": "",
    }


def build_running_status(job_type=None, progress=0, message="Processando...", detail=""):
    return {
        "job_type": job_type or "",
        "state": "running",
        "progress": int(progress),
        "message": message,
        "detail": detail,
    }


def build_success_status(job_type=None, message="Concluído com sucesso.", detail=""):
    return {
        "job_type": job_type or "",
        "state": "success",
        "progress": 100,
        "message": message,
        "detail": detail,
    }


def build_error_status(job_type=None, message="Ocorreu um erro.", detail=""):
    return {
        "job_type": job_type or "",
        "state": "error",
        "progress": 100,
        "message": message,
        "detail": detail,
    }


def save_job_status(project_path, job_type, status):
    status_file = get_status_file(project_path, job_type)
    status_file.parent.mkdir(parents=True, exist_ok=True)

    payload = dict(status or {})
    payload.setdefault("job_type", job_type)
    payload.setdefault("state", "idle")
    payload.setdefault("progress", 0)
    payload.setdefault("message", "Aguardando.")
    payload.setdefault("detail", "")

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=status_file.parent,
        delete=False,
        suffix=".tmp",
    ) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_name = tmp.name

    os.replace(temp_name, status_file)


def load_job_status(project_path, job_type):
    status_file = get_status_file(project_path, job_type)

    if not status_file.exists():
        return build_idle_status(job_type)

    try:
        with open(status_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return build_error_status(job_type, "O arquivo de status está inválido.", "Conteúdo não é um objeto JSON.")

        data.setdefault("job_type", job_type)
        data.setdefault("state", "idle")
        data.setdefault("progress", 0)
        data.setdefault("message", "Aguardando.")
        data.setdefault("detail", "")
        return data

    except json.JSONDecodeError:
        return build_running_status(job_type, 0, "Atualizando status...", "")
    except Exception as e:
        return build_error_status(job_type, "O arquivo de status está inválido.", str(e))
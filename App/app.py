import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, url_for
from openai import OpenAI

from utils.error_handlers import build_error_title, humanize_error
from utils.job_status import (
    build_error_status,
    build_idle_status,
    build_running_status,
    build_success_status,
    load_job_status,
    save_job_status,
)

load_dotenv()

ENV_OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ENV_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4")
ENV_HUGGINGFACE_TOKEN = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN") or ""
ENV_PYANNOTE_MODEL = os.getenv("PYANNOTE_MODEL", "pyannote/speaker-diarization-community-1")

OPENAI_MODEL_OPTIONS = [
    {"value": "gpt-5.4", "label": "GPT-5.4"},
    {"value": "gpt-5.4-mini", "label": "GPT-5.4 Mini"},
    {"value": "gpt-5.5", "label": "GPT-5.5"},
    {"value": "gpt-5.5-thinking", "label": "GPT-5.5 Thinking"},
]

PYANNOTE_MODEL_OPTIONS = [
    {"value": "pyannote/speaker-diarization-community-1", "label": "Speaker Diarization Community 1"},
]

# Os modos de desempenho abaixo foram desenhados para macOS/MacBook.
# Eles usam prioridades Unix/macOS como `nice`, limites de threads para FFmpeg,
# whisper.cpp e PyTorch, além de variáveis de ambiente comuns em macOS.
# Em uma futura versão para Windows, esta camada precisa ser portada para
# APIs/estratégias equivalentes do Windows antes de ser tratada como compatível.
PERFORMANCE_MODE_OPTIONS = [
    {
        "value": "default",
        "label": "Padrão",
        "description": "Não altera prioridade, threads ou variáveis do sistema. Usa o comportamento normal das ferramentas.",
    },
    {
        "value": "eco",
        "label": "Econômico",
        "description": "Processa com calma e menor prioridade. Recomendado para deixar processando à noite.",
    },
    {
        "value": "balanced",
        "label": "Equilibrado",
        "description": "Meio-termo entre velocidade e conforto térmico. Recomendado para continuar trabalhando enquanto o EVR processa seus vídeos.",
    },
    {
        "value": "max",
        "label": "Máximo",
        "description": "Usa o MEGABRAIN EM FORÇA TOTAL e mais prioridade de processamento para terminar mais rápido. Pode aumentar a temperatura do processador.",
    },
]
DEFAULT_PERFORMANCE_MODE = "default"
JOB_CANCELLED_MESSAGE = "processamento cancelado pelo usuário"

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"

# Garante que o app aberto pelo Finder encontre os binários empacotados e,
# em ambiente de desenvolvimento, os binários instalados via Homebrew.
BUNDLED_BIN_DIR = BASE_DIR / "bin"
PATH_PREFIXES = [str(BUNDLED_BIN_DIR), "/opt/homebrew/bin", "/usr/local/bin"]
os.environ["PATH"] = ":".join(PATH_PREFIXES) + ":" + os.environ.get("PATH", "")

DEFAULT_STORAGE_FOLDER_NAME = "VideosEditados"
PROCESSING_FOLDER_NAME = "Estrutura de Processamento"
PROJECT_PROCESSING_SUBFOLDERS = ["Audios", "Arquivo Video Bruto", "Dados de Processamento", "Transcrições"]
PROJECT_PUBLIC_SUBFOLDERS = ["Videos Finalizados"]

WHISPER_CLI_PATH = BASE_DIR / "whisper.cpp" / "build" / "bin" / "whisper-cli"
WHISPER_MODEL_PATH = BASE_DIR / "Modelos" / "ggml-base.bin"

JOB_CONTEXT = threading.local()
ACTIVE_PROCESS_LOCK = threading.Lock()
ACTIVE_PROCESSES: dict[tuple[str, str], subprocess.Popen] = {}
ACTIVE_KEEP_AWAKE_PROCESSES: dict[tuple[str, str], subprocess.Popen] = {}
CANCELLED_JOBS: set[tuple[str, str]] = set()

JOB_TYPES = ["generate_transcription", "identify_speakers", "suggest_cuts", "process_cuts", "split_video"]
JOB_HISTORY_LIMIT = 12
DEFAULT_AI_CUT_OPTION_COUNT = 6
DEFAULT_AI_HOOK_OPTION_COUNT = 1
MIN_AI_CUT_OPTION_COUNT = 1
MAX_AI_CUT_OPTION_COUNT = 15
MIN_AI_HOOK_OPTION_COUNT = 1
MAX_AI_HOOK_OPTION_COUNT = 5

SPLIT_SOCIAL_PRESETS = {
    "instagram": {
        "label": "Instagram",
        "formats": {
            "reels": {"label": "Reels", "durations": [15, 30, 60, 90]},
            "stories": {"label": "Stories", "durations": [15, 30, 60]},
        },
    },
    "youtube": {
        "label": "YouTube",
        "formats": {
            "shorts": {"label": "Shorts", "durations": [15, 30, 60, 180]},
        },
    },
    "tiktok": {
        "label": "TikTok",
        "formats": {
            "tiktok": {"label": "TikTok", "durations": [15, 30, 60, 180]},
        },
    },
    "linkedin": {
        "label": "LinkedIn",
        "formats": {
            "feed": {"label": "Feed profissional", "durations": [15, 30, 60, 90]},
        },
    },
}


# Normaliza nomes de pasta digitados pelo usuário.
def sanitize_folder_name(value: str) -> str:
    cleaned = "".join(c for c in value if c.isalnum() or c in (" ", "_", "-")).strip()
    cleaned = " ".join(cleaned.split())
    return cleaned or DEFAULT_STORAGE_FOLDER_NAME


# Carrega a configuração local do app.
def get_default_app_config() -> dict:
    return {
        "storage_folder_name": DEFAULT_STORAGE_FOLDER_NAME,
        "performance_mode": DEFAULT_PERFORMANCE_MODE,
        "onboarding": {
            "home_tips_seen": False,
            "home_tour_seen": False,
            "settings_tour_seen": False,
            "ai_cuts_tour_seen": False,
            "video_splitter_tour_seen": False,
        },
        "api_health": {
            "openai_ok": False,
            "huggingface_ok": False,
            "openai_checked_at": "",
            "huggingface_checked_at": "",
        },
        "glossary": [],
        "progress_history": {},
        "api_settings": {
            "openai_api_key": "",
            "openai_model": ENV_OPENAI_MODEL,
            "huggingface_token": "",
            "pyannote_model": ENV_PYANNOTE_MODEL,
        },
    }


def load_app_config() -> dict:
    config = get_default_app_config()

    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                config.update({key: value for key, value in data.items() if key not in {"api_settings", "onboarding", "api_health"}})
                if isinstance(data.get("api_settings"), dict):
                    config["api_settings"].update(data["api_settings"])
                if isinstance(data.get("onboarding"), dict):
                    config["onboarding"].update(data["onboarding"])
                if isinstance(data.get("api_health"), dict):
                    config["api_health"].update(data["api_health"])
        except json.JSONDecodeError:
            pass

    return config


# Salva a configuração local do app preservando campos já existentes.
def save_app_config(config: dict):
    current = load_app_config()
    for key, value in config.items():
        if key == "api_settings" and isinstance(value, dict):
            current.setdefault("api_settings", {})
            current["api_settings"].update(value)
        elif key == "onboarding" and isinstance(value, dict):
            current.setdefault("onboarding", {})
            current["onboarding"].update(value)
        elif key == "api_health" and isinstance(value, dict):
            current.setdefault("api_health", {})
            current["api_health"].update(value)
        else:
            current[key] = value

    CONFIG_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")


def mask_secret(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= 10:
        return "••••••"
    return f"{value[:6]}••••••{value[-4:]}"


def get_api_settings() -> dict:
    settings = load_app_config().get("api_settings", {})

    return {
        "openai_api_key": settings.get("openai_api_key") or "",
        "openai_model": settings.get("openai_model") or ENV_OPENAI_MODEL,
        "huggingface_token": settings.get("huggingface_token") or "",
        "pyannote_model": settings.get("pyannote_model") or ENV_PYANNOTE_MODEL,
    }


def get_public_api_settings() -> dict:
    settings = get_api_settings()
    health = load_app_config().get("api_health", {})
    openai_configured = bool(settings.get("openai_api_key"))
    huggingface_configured = bool(settings.get("huggingface_token"))
    openai_ok = openai_configured and bool(health.get("openai_ok"))
    huggingface_ok = huggingface_configured and bool(health.get("huggingface_ok"))
    return {
        "openai_configured": openai_configured,
        "openai_ok": openai_ok,
        "openai_checked_at": health.get("openai_checked_at", ""),
        "openai_api_key_masked": mask_secret(settings.get("openai_api_key", "")),
        "openai_model": settings.get("openai_model", ""),
        "huggingface_configured": huggingface_configured,
        "huggingface_ok": huggingface_ok,
        "huggingface_checked_at": health.get("huggingface_checked_at", ""),
        "huggingface_token_masked": mask_secret(settings.get("huggingface_token", "")),
        "pyannote_model": settings.get("pyannote_model", ""),
    }


def get_ai_integrations_alert() -> dict:
    api_settings = get_public_api_settings()
    pending = []
    if not api_settings["openai_ok"]:
        pending.append("OpenAI")
    if not api_settings["huggingface_ok"]:
        pending.append("Hugging Face")

    return {
        "ok": not pending,
        "pending": pending,
        "severity": "danger" if len(pending) == 2 else "warning",
        "message": "Conecte as ferramentas de inteligência artificial para processar transcrições, participantes, cortes e ganchos.",
    }


def normalize_glossary_entries(entries) -> list[dict]:
    normalized = []
    if not isinstance(entries, list):
        return normalized

    seen_terms = set()
    for item in entries:
        if not isinstance(item, dict):
            continue
        term = str(item.get("term", "")).strip()
        if not term:
            continue
        variants = str(item.get("variants", "")).strip()
        context = str(item.get("context", "")).strip()
        dedupe_key = term.casefold()
        if dedupe_key in seen_terms:
            continue
        seen_terms.add(dedupe_key)
        normalized.append(
            {
                "term": term,
                "variants": variants,
                "context": context,
            }
        )
    return normalized


def get_glossary_entries() -> list[dict]:
    return normalize_glossary_entries(load_app_config().get("glossary", []))


def get_public_glossary_settings() -> dict:
    entries = get_glossary_entries()
    return {
        "entries": entries,
        "count": len(entries),
    }


def format_glossary_for_prompt(entries: list[dict]) -> str:
    if not entries:
        return "Nenhum termo cadastrado."

    lines = []
    for item in entries:
        parts = [f"Termo correto: {item['term']}"]
        if item.get("variants"):
            parts.append(f"Variações erradas comuns: {item['variants']}")
        if item.get("context"):
            parts.append(f"Contexto: {item['context']}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def get_performance_mode() -> str:
    mode = load_app_config().get("performance_mode", DEFAULT_PERFORMANCE_MODE)
    allowed_modes = {item["value"] for item in PERFORMANCE_MODE_OPTIONS}
    return mode if mode in allowed_modes else DEFAULT_PERFORMANCE_MODE


def get_performance_mode_label(mode: str | None = None) -> str:
    mode = mode or get_performance_mode()
    options_by_value = {item["value"]: item for item in PERFORMANCE_MODE_OPTIONS}
    return options_by_value.get(mode, options_by_value[DEFAULT_PERFORMANCE_MODE])["label"]


def get_public_performance_settings() -> dict:
    mode = get_performance_mode()
    return {
        "mode": mode,
        "label": get_performance_mode_label(mode),
        "options": [
            {
                **item,
                "details": get_performance_mode_details(item["value"]),
            }
            for item in PERFORMANCE_MODE_OPTIONS
        ],
    }


def get_performance_profile_for_mode(mode: str) -> dict:
    cpu_count = os.cpu_count() or 4

    profiles = {
        "default": {
            "mode": "default",
            "threads": None,
            "nice": 0,
            "managed": False,
            "keep_awake": False,
            "display_sleep_after_seconds": None,
        },
        "eco": {
            "mode": "eco",
            "threads": max(1, min(2, cpu_count)),
            "nice": 15,
            "managed": True,
            "keep_awake": True,
            "display_sleep_after_seconds": 60,
        },
        "balanced": {
            "mode": "balanced",
            "threads": max(2, min(4, max(1, cpu_count // 2))),
            "nice": 8,
            "managed": True,
            "keep_awake": True,
            "display_sleep_after_seconds": None,
        },
        "max": {
            "mode": "max",
            "threads": max(2, cpu_count),
            "nice": 0,
            "managed": True,
            "keep_awake": True,
            "display_sleep_after_seconds": None,
        },
    }
    return profiles.get(mode, profiles[DEFAULT_PERFORMANCE_MODE])


def get_performance_profile() -> dict:
    context_profile = getattr(JOB_CONTEXT, "performance_profile", None)
    if isinstance(context_profile, dict):
        return context_profile
    return get_performance_profile_for_mode(get_performance_mode())


def get_performance_mode_details(mode: str) -> list[str]:
    profile = get_performance_profile_for_mode(mode)
    threads = profile.get("threads")
    nice = int(profile.get("nice", 0) or 0)

    if not profile.get("managed"):
        return [
            "Prioridade: padrão do sistema, sem nice.",
            "FFmpeg: comando original, sem -threads adicionado pelo EVR.",
            "whisper.cpp: comando original, sem -t adicionado pelo EVR.",
            "Pyannote/PyTorch: sem limite de threads aplicado pelo EVR.",
            "Energia: não usa caffeinate; o macOS decide sozinho quando dormir.",
            "Variáveis de ambiente: OMP_NUM_THREADS, MKL_NUM_THREADS, OPENBLAS_NUM_THREADS, VECLIB_MAXIMUM_THREADS e NUMEXPR_NUM_THREADS não são alteradas.",
        ]

    command_prefix = f"nice -n {nice}" if nice > 0 else "sem nice"
    details = [
        f"Prioridade dos subprocessos: {command_prefix}.",
        f"FFmpeg: adiciona -threads {threads} nos comandos de leitura e renderização.",
        f"whisper.cpp: adiciona -t {threads} ao whisper-cli.",
        f"Pyannote/PyTorch: aplica torch.set_num_threads({threads}) e limite de interop conservador.",
        "Energia: mantém caffeinate -i ativo apenas durante o job para impedir o Mac de dormir enquanto processa, permitindo que a tela apague normalmente.",
        f"Variáveis de ambiente: OMP_NUM_THREADS={threads}, MKL_NUM_THREADS={threads}, OPENBLAS_NUM_THREADS={threads}, VECLIB_MAXIMUM_THREADS={threads}, NUMEXPR_NUM_THREADS={threads}.",
    ]
    if profile.get("display_sleep_after_seconds"):
        details.insert(
            5,
            f"Tela: solicita ao macOS apagar a tela após {profile['display_sleep_after_seconds']} segundos de processamento, sem alterar a configuração permanente do sistema.",
        )
    return details


def get_processing_env(profile: dict | None = None) -> dict | None:
    profile = profile or get_performance_profile()
    if not profile.get("threads"):
        return None

    env = os.environ.copy()
    thread_count = str(profile["threads"])
    env.update(
        {
            "OMP_NUM_THREADS": thread_count,
            "VECLIB_MAXIMUM_THREADS": thread_count,
            "MKL_NUM_THREADS": thread_count,
            "NUMEXPR_NUM_THREADS": thread_count,
            "OPENBLAS_NUM_THREADS": thread_count,
        }
    )
    return env


def with_low_priority(command: list[str], profile: dict | None = None) -> list[str]:
    profile = profile or get_performance_profile()
    nice_level = int(profile.get("nice", 0) or 0)
    if nice_level <= 0:
        return command
    return ["nice", "-n", str(nice_level), *command]


def get_current_job_key() -> tuple[str, str] | None:
    project_id = getattr(JOB_CONTEXT, "project_id", None)
    job_type = getattr(JOB_CONTEXT, "job_type", None)
    if not project_id or not job_type:
        return None
    return (project_id, job_type)


def clear_job_cancel(project_id: str, job_type: str):
    with ACTIVE_PROCESS_LOCK:
        CANCELLED_JOBS.discard((project_id, job_type))


def is_job_cancelled(project_id: str, job_type: str) -> bool:
    with ACTIVE_PROCESS_LOCK:
        return (project_id, job_type) in CANCELLED_JOBS


def raise_if_current_job_cancelled():
    key = get_current_job_key()
    if key and is_job_cancelled(*key):
        raise RuntimeError(JOB_CANCELLED_MESSAGE)


def register_active_process(key: tuple[str, str] | None, process: subprocess.Popen):
    if not key:
        return
    with ACTIVE_PROCESS_LOCK:
        ACTIVE_PROCESSES[key] = process


def unregister_active_process(key: tuple[str, str] | None, process: subprocess.Popen):
    if not key:
        return
    with ACTIVE_PROCESS_LOCK:
        if ACTIVE_PROCESSES.get(key) is process:
            ACTIVE_PROCESSES.pop(key, None)


def stop_process(process: subprocess.Popen | None):
    if not process or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()


def start_job_keep_awake(key: tuple[str, str], profile: dict | None = None) -> subprocess.Popen | None:
    profile = profile or get_performance_profile()
    if not profile.get("keep_awake") or shutil.which("caffeinate") is None:
        return None

    process = subprocess.Popen(
        ["caffeinate", "-i"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with ACTIVE_PROCESS_LOCK:
        ACTIVE_KEEP_AWAKE_PROCESSES[key] = process
    return process


def stop_job_keep_awake(key: tuple[str, str], process: subprocess.Popen | None = None):
    with ACTIVE_PROCESS_LOCK:
        active_process = ACTIVE_KEEP_AWAKE_PROCESSES.get(key)
        if process is None or active_process is process:
            process = ACTIVE_KEEP_AWAKE_PROCESSES.pop(key, None)
    stop_process(process)


def schedule_display_sleep(key: tuple[str, str], profile: dict, keep_awake_process: subprocess.Popen | None):
    delay_seconds = profile.get("display_sleep_after_seconds")
    if not delay_seconds or not keep_awake_process or shutil.which("pmset") is None:
        return

    def request_display_sleep():
        with ACTIVE_PROCESS_LOCK:
            still_same_job = ACTIVE_KEEP_AWAKE_PROCESSES.get(key) is keep_awake_process
        if not still_same_job or keep_awake_process.poll() is not None or is_job_cancelled(*key):
            return

        subprocess.run(
            ["pmset", "displaysleepnow"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    timer = threading.Timer(float(delay_seconds), request_display_sleep)
    timer.daemon = True
    timer.start()


def request_job_cancel(project_id: str, job_type: str):
    key = (project_id, job_type)
    with ACTIVE_PROCESS_LOCK:
        CANCELLED_JOBS.add(key)
        process = ACTIVE_PROCESSES.get(key)
        keep_awake_process = ACTIVE_KEEP_AWAKE_PROCESSES.pop(key, None)

    stop_process(process)
    stop_process(keep_awake_process)


def terminate_active_processes():
    with ACTIVE_PROCESS_LOCK:
        processes = list(ACTIVE_PROCESSES.values())
        keep_awake_processes = list(ACTIVE_KEEP_AWAKE_PROCESSES.values())
        ACTIVE_PROCESSES.clear()
        ACTIVE_KEEP_AWAKE_PROCESSES.clear()

    for process in processes + keep_awake_processes:
        stop_process(process)


atexit.register(terminate_active_processes)


def run_processing_command(command: list[str], **kwargs):
    profile = get_performance_profile()
    env = get_processing_env(profile)
    if env:
        merged_env = os.environ.copy()
        merged_env.update(kwargs.pop("env", {}) or {})
        merged_env.update(env)
        kwargs["env"] = merged_env

    key = get_current_job_key()
    raise_if_current_job_cancelled()

    check = kwargs.pop("check", False)
    capture_output = kwargs.pop("capture_output", False)
    timeout = kwargs.pop("timeout", None)
    input_data = kwargs.pop("input", None)
    if capture_output:
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)

    final_command = with_low_priority(command, profile)
    if key:
        append_project_log(
            key[0],
            key[1],
            "comando",
            " ".join(str(part) for part in final_command),
        )
    process = subprocess.Popen(final_command, **kwargs)
    register_active_process(key, process)

    try:
        stdout, stderr = process.communicate(input=input_data, timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(final_command, timeout, output=stdout, stderr=stderr)
    finally:
        unregister_active_process(key, process)

    if key and is_job_cancelled(*key):
        raise RuntimeError(JOB_CANCELLED_MESSAGE)

    completed = subprocess.CompletedProcess(final_command, process.returncode, stdout, stderr)
    if key:
        append_project_log(key[0], key[1], "comando_finalizado", f"exit={process.returncode}")
    if check and process.returncode:
        raise subprocess.CalledProcessError(process.returncode, final_command, output=stdout, stderr=stderr)
    return completed


def ffmpeg_command(*args) -> list[str]:
    profile = get_performance_profile()
    if not profile.get("threads"):
        return ["ffmpeg", *args]
    return ["ffmpeg", "-threads", str(profile["threads"]), *args]


def ffmpeg_thread_args() -> list[str]:
    threads = get_performance_profile().get("threads")
    if not threads:
        return []
    return ["-threads", str(threads)]


def get_openai_client():
    api_key = get_api_settings().get("openai_api_key", "")
    if not api_key:
        return None
    return OpenAI(api_key=api_key)


def get_openai_model() -> str:
    return get_api_settings().get("openai_model") or ENV_OPENAI_MODEL


def get_openai_model_display_label(model_value: str | None = None) -> str:
    value = model_value or get_openai_model()
    option = next((item for item in OPENAI_MODEL_OPTIONS if item["value"] == value), None)
    label = option["label"] if option else value
    if label.upper().startswith("GPT-"):
        return f"ChatGPT {label[4:]}"
    return label


def get_huggingface_token() -> str:
    return get_api_settings().get("huggingface_token") or ""


def get_pyannote_model() -> str:
    return get_api_settings().get("pyannote_model") or ENV_PYANNOTE_MODEL


# Retorna o nome da pasta de armazenamento.
def get_storage_folder_name() -> str:
    config = load_app_config()
    return sanitize_folder_name(config.get("storage_folder_name", DEFAULT_STORAGE_FOLDER_NAME))


# Retorna a pasta raiz externa de armazenamento.
def get_storage_root() -> Path:
    return Path.home() / "Desktop" / get_storage_folder_name()


# Cria a pasta raiz externa, se necessário.
def ensure_storage_root(folder_name: str | None = None) -> Path:
    if folder_name is not None:
        folder_name = sanitize_folder_name(folder_name)
        save_app_config({"storage_folder_name": folder_name})

    storage_root = get_storage_root()
    storage_root.mkdir(parents=True, exist_ok=True)
    return storage_root


# Retorna informações da pasta externa para a Home.
def get_storage_status() -> dict:
    storage_root = get_storage_root()
    return {
        "folder_name": get_storage_folder_name(),
        "path": str(storage_root),
        "exists": storage_root.exists(),
    }


# Retorna o caminho raiz de um projeto.
def get_project_path(project_id: str) -> Path:
    return get_storage_root() / project_id


# Retorna a pasta interna de processamento de um projeto.
def get_processing_path(project_id: str) -> Path:
    return get_project_path(project_id) / PROCESSING_FOLDER_NAME


# Retorna subpasta padronizada de um projeto.
def get_project_subdir(project_id: str, folder_name: str) -> Path:
    if folder_name == "Videos Finalizados":
        return get_project_path(project_id) / "Videos Finalizados"
    return get_processing_path(project_id) / folder_name


# Cria um ID único, limpo e numerado para o projeto.
def build_unique_project_id(project_slug: str) -> str:
    storage_root = get_storage_root()
    storage_root.mkdir(parents=True, exist_ok=True)

    used_numbers = set()
    pattern = re.compile(r"^(\d{2})_")

    for item in storage_root.iterdir():
        if not item.is_dir():
            continue

        match = pattern.match(item.name)
        if match:
            used_numbers.add(int(match.group(1)))

    for number in range(1, 100):
        if number not in used_numbers:
            return f"{number:02d}_{project_slug}"

    raise ValueError("Limite de 99 projetos atingido nesta pasta de armazenamento.")


# Cria a estrutura padrão de um projeto.
def ensure_project_structure(project_id: str) -> Path:
    project_path = get_project_path(project_id)

    for folder_name in PROJECT_PUBLIC_SUBFOLDERS:
        (project_path / folder_name).mkdir(parents=True, exist_ok=True)

    for folder_name in PROJECT_PROCESSING_SUBFOLDERS:
        (get_processing_path(project_id) / folder_name).mkdir(parents=True, exist_ok=True)

    return project_path


# Retorna o caminho do metadata do projeto.
def get_metadata_path(project_id: str) -> Path:
    return get_project_subdir(project_id, "Dados de Processamento") / "metadata.txt"


# Retorna o caminho do registro principal dos cortes.
def get_cuts_registry_path(project_id: str) -> Path:
    return get_project_subdir(project_id, "Dados de Processamento") / "cuts.json"


# Retorna o caminho do arquivo texto compatível com o legado.
def get_cuts_txt_path(project_id: str) -> Path:
    return get_project_subdir(project_id, "Dados de Processamento") / "cuts.txt"


# Retorna o caminho das sugestões selecionadas para a seção 5.
def get_selected_ai_cuts_path(project_id: str) -> Path:
    return get_project_subdir(project_id, "Dados de Processamento") / "selected_ai_cuts.json"


# Retorna o caminho das sugestões da IA.
def get_ai_suggestions_path(project_id: str) -> Path:
    return get_project_subdir(project_id, "Dados de Processamento") / "ai_suggestions.json"


# Retorna o caminho do pedido livre enviado para a IA.
def get_ai_request_path(project_id: str) -> Path:
    return get_project_subdir(project_id, "Dados de Processamento") / "ai_request.json"


# Retorna o caminho da transcrição enriquecida com speakers.
def get_speaker_transcript_path(project_id: str) -> Path:
    return get_project_subdir(project_id, "Dados de Processamento") / "speaker_transcript.json"


# Retorna as configurações usadas na identificação de speakers.
def get_speaker_settings_path(project_id: str) -> Path:
    return get_project_subdir(project_id, "Dados de Processamento") / "speaker_settings.json"


# Retorna a pasta de amostras de áudio dos speakers.
def get_speaker_samples_dir(project_id: str) -> Path:
    path = get_project_subdir(project_id, "Dados de Processamento") / "Amostras de Speakers"
    path.mkdir(parents=True, exist_ok=True)
    return path


# Retorna o caminho cacheado de uma amostra de speaker.
def get_speaker_sample_path(project_id: str, speaker_id: str) -> Path:
    safe_speaker = sanitize_filename(speaker_id).replace(" ", "_")
    return get_speaker_samples_dir(project_id) / f"{safe_speaker}.wav"


def get_transcription_samples_dir(project_id: str) -> Path:
    path = get_project_subdir(project_id, "Dados de Processamento") / "Amostras Transcricao"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_transcription_sample_path(project_id: str, uid: int) -> Path:
    return get_transcription_samples_dir(project_id) / f"trecho_{uid:04d}.wav"


def get_ai_suggestion_samples_dir(project_id: str) -> Path:
    path = get_project_subdir(project_id, "Dados de Processamento") / "Amostras de Sugestoes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_ai_suggestion_sample_path(project_id: str, suggestion_index: int, segment_type: str, start_time: str, end_time: str) -> Path:
    safe_start = sanitize_filename(start_time.replace(":", "-"))
    safe_end = sanitize_filename(end_time.replace(":", "-"))
    safe_segment = sanitize_filename(segment_type)
    return get_ai_suggestion_samples_dir(project_id) / f"sugestao_{suggestion_index}_{safe_segment}_{safe_start}_{safe_end}.wav"


# Retorna o caminho dos trechos manuais do Video Splitter.
def get_split_manual_segments_path(project_id: str) -> Path:
    return get_project_subdir(project_id, "Dados de Processamento") / "split_manual_segments.json"


# Retorna o caminho base dos arquivos de transcrição.
def get_transcript_txt_path(project_id: str) -> Path:
    return get_project_subdir(project_id, "Transcrições") / f"{project_id}_transcricao.txt"


# Retorna o caminho SRT da transcrição.
def get_transcript_srt_path(project_id: str) -> Path:
    return get_project_subdir(project_id, "Transcrições") / f"{project_id}_transcricao.srt"


# Retorna o caminho TXT da transcrição revisada automaticamente.
def get_revised_transcript_txt_path(project_id: str) -> Path:
    return get_project_subdir(project_id, "Transcrições") / f"{project_id}_transcricao_revisada.txt"


# Retorna o caminho SRT da transcrição revisada automaticamente.
def get_revised_transcript_srt_path(project_id: str) -> Path:
    return get_project_subdir(project_id, "Transcrições") / f"{project_id}_transcricao_revisada.srt"


def get_transcript_revision_error_path(project_id: str) -> Path:
    return get_project_subdir(project_id, "Dados de Processamento") / "transcription_revision_error.txt"


def get_preferred_transcript_txt_path(project_id: str) -> Path:
    revised_path = get_revised_transcript_txt_path(project_id)
    return revised_path if revised_path.exists() else get_transcript_txt_path(project_id)


def get_preferred_transcript_srt_path(project_id: str) -> Path:
    revised_path = get_revised_transcript_srt_path(project_id)
    return revised_path if revised_path.exists() else get_transcript_srt_path(project_id)


# Salva o metadata simples do projeto.
def save_metadata(project_id: str, data: dict):
    ensure_project_structure(project_id)
    metadata_path = get_metadata_path(project_id)

    with metadata_path.open("w", encoding="utf-8") as f:
        for key, value in data.items():
            f.write(f"{key}={value}\n")


# Carrega o metadata simples do projeto.
def load_metadata(project_id: str) -> dict:
    metadata = {}
    metadata_path = get_metadata_path(project_id)

    if metadata_path.exists():
        with metadata_path.open("r", encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    key, value = line.strip().split("=", 1)
                    metadata[key] = value
    return metadata


def get_project_technical_log_path(project_id: str) -> Path:
    return get_project_subdir(project_id, "Dados de Processamento") / "log_tecnico.txt"


def sanitize_log_text(value) -> str:
    text = str(value or "")
    replacements = [
        (r"sk-[A-Za-z0-9_\-]{8,}", "sk-***"),
        (r"hf_[A-Za-z0-9_\-]{8,}", "hf_***"),
        (r"(OPENAI_API_KEY|HF_TOKEN|HUGGINGFACE_TOKEN)\s*=\s*[^ \n\r]+", r"\1=***"),
        (r"(api[_-]?key|token|authorization)\s*[:=]\s*[^ \n\r]+", r"\1=***"),
        (r"Bearer\s+[A-Za-z0-9._\-]+", "Bearer ***"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text.replace("\r", "").strip()


def append_project_log(project_id: str, job_type: str, event: str, message: str = "", detail: str = ""):
    try:
        ensure_project_structure(project_id)
        log_path = get_project_technical_log_path(project_id)
        timestamp = datetime.now().isoformat(timespec="seconds")
        parts = [timestamp, job_type or "app", event or "evento"]
        cleaned_message = sanitize_log_text(message)
        cleaned_detail = sanitize_log_text(detail)
        if cleaned_message:
            parts.append(cleaned_message)
        if cleaned_detail:
            parts.append(cleaned_detail)
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(" | ".join(parts) + "\n")
    except Exception:
        pass


def read_project_technical_log(project_id: str, max_chars: int = 40000) -> str:
    log_path = get_project_technical_log_path(project_id)
    if not log_path.exists():
        return ""
    text = log_path.read_text(encoding="utf-8", errors="replace")
    return sanitize_log_text(text[-max_chars:])


def project_is_removed_from_app(metadata: dict) -> bool:
    return metadata.get("removed_from_app") == "yes"


def project_is_archived(metadata: dict) -> bool:
    return metadata.get("archived") == "yes"


def get_home_project_status_group(project_id: str, metadata: dict) -> str:
    if project_is_archived(metadata):
        return "archived"
    if get_processed_files(project_id):
        return "done"
    if metadata.get("status") and metadata.get("status") != "Vídeo carregado":
        return "in_progress"
    return "new"


def copy_path_if_exists(source: Path, destination: Path):
    if not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)
    else:
        shutil.copy2(source, destination)


# Normaliza um nome para filename sem prefixo do projeto.
def sanitize_filename(value: str) -> str:
    cleaned = "".join(c for c in value if c.isalnum() or c in (" ", "_", "-")).strip()
    cleaned = " ".join(cleaned.split())
    return cleaned or "corte"


# Gera um nome de arquivo final único sem usar o id do projeto.
def build_unique_output_name(output_dir: Path, name: str) -> str:
    base_name = sanitize_filename(name)
    candidate = f"{base_name}.mp4"
    index = 2
    while (output_dir / candidate).exists():
        candidate = f"{base_name}_{index}.mp4"
        index += 1
    return candidate


# Retorna a duração do vídeo com ffprobe.
def get_video_duration(video_path: Path | str):
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return round(float(result.stdout.strip()), 2)
    except Exception:
        return None


# Detecta chamadas AJAX do frontend.
def is_ajax_request() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


# Converte HH:MM:SS em segundos.
def parse_time_to_seconds(value: str) -> int:
    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError("tempo inválido")
    hours, minutes, seconds = [int(part) for part in parts]
    return hours * 3600 + minutes * 60 + seconds


# Converte segundos em HH:MM:SS.


# Converte tempo de speaker/SRT em segundos com suporte a milissegundos.
def parse_timestamp_to_float_seconds(value: str) -> float:
    value = (value or "").strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError("tempo inválido")
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = float(parts[2])
    return hours * 3600 + minutes * 60 + seconds

def format_seconds_to_time(total_seconds: int) -> str:
    total_seconds = max(0, int(total_seconds))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


SRT_TIME_RANGE_PATTERN = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}[,.]\d{3})\s+-->\s+(?P<end>\d{2}:\d{2}:\d{2}[,.]\d{3})"
)


# Converte tempo SRT HH:MM:SS,mmm em segundos.
def parse_srt_time_to_seconds(value: str) -> float:
    value = value.strip().replace(",", ".")
    time_part, millis_part = value.split(".", 1)
    hours, minutes, seconds = [int(part) for part in time_part.split(":")]
    return hours * 3600 + minutes * 60 + seconds + (int(millis_part[:3]) / 1000)


# Lê o SRT do Whisper e transforma em segmentos estruturados.
def parse_srt_segments(srt_text: str) -> list[dict]:
    segments = []
    blocks = re.split(r"\n\s*\n", srt_text.strip())

    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue

        time_line_index = next((idx for idx, line in enumerate(lines) if "-->" in line), None)
        if time_line_index is None:
            continue

        match = SRT_TIME_RANGE_PATTERN.search(lines[time_line_index])
        if not match:
            continue

        text = " ".join(lines[time_line_index + 1:]).strip()
        if not text:
            continue

        start_seconds = parse_srt_time_to_seconds(match.group("start"))
        end_seconds = parse_srt_time_to_seconds(match.group("end"))
        if end_seconds <= start_seconds:
            continue

        segments.append(
            {
                "start_seconds": round(start_seconds, 3),
                "end_seconds": round(end_seconds, 3),
                "start": format_seconds_to_time(start_seconds),
                "end": format_seconds_to_time(end_seconds),
                "text": text,
            }
        )

    return segments


def parse_srt_blocks_for_revision(srt_text: str) -> list[dict]:
    blocks = []
    raw_blocks = re.split(r"\n\s*\n", srt_text.strip())

    for fallback_index, block in enumerate(raw_blocks, start=1):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue

        time_line_index = next((idx for idx, line in enumerate(lines) if "-->" in line), None)
        if time_line_index is None:
            continue

        time_line = lines[time_line_index]
        if not SRT_TIME_RANGE_PATTERN.search(time_line):
            continue

        number = lines[0] if time_line_index > 0 and lines[0].isdigit() else str(fallback_index)
        text = " ".join(lines[time_line_index + 1:]).strip()
        if not text:
            continue

        blocks.append(
            {
                "uid": len(blocks) + 1,
                "number": number,
                "time_line": time_line,
                "text": text,
            }
        )

    return blocks


def build_srt_from_revision_blocks(blocks: list[dict]) -> str:
    output_blocks = []
    for position, block in enumerate(blocks, start=1):
        number = str(block.get("number") or position)
        time_line = str(block.get("time_line", "")).strip()
        text = str(block.get("revised_text") or block.get("text") or "").strip()
        if not time_line or not text:
            continue
        output_blocks.append(f"{number}\n{time_line}\n{text}")
    return "\n\n".join(output_blocks).strip() + "\n"


def build_txt_from_revision_blocks(blocks: list[dict]) -> str:
    return "\n".join(str(block.get("revised_text") or block.get("text") or "").strip() for block in blocks if str(block.get("revised_text") or block.get("text") or "").strip()).strip() + "\n"


def normalize_text_for_comparison(value: str) -> str:
    return " ".join((value or "").strip().casefold().split())


def build_transcription_review_blocks(project_id: str) -> list[dict]:
    original_srt_path = get_transcript_srt_path(project_id)
    revised_srt_path = get_revised_transcript_srt_path(project_id)
    if not original_srt_path.exists():
        return []

    original_blocks = parse_srt_blocks_for_revision(original_srt_path.read_text(encoding="utf-8"))
    revised_blocks = []
    if revised_srt_path.exists():
        revised_blocks = parse_srt_blocks_for_revision(revised_srt_path.read_text(encoding="utf-8"))

    revised_by_uid = {block["uid"]: block for block in revised_blocks}
    review_blocks = []
    for block in original_blocks:
        revised_text = revised_by_uid.get(block["uid"], {}).get("text", block["text"])
        review_blocks.append(
            {
                "uid": block["uid"],
                "number": block["number"],
                "time_line": block["time_line"],
                "original_text": block["text"],
                "revised_text": revised_text,
                "is_different": normalize_text_for_comparison(block["text"]) != normalize_text_for_comparison(revised_text),
            }
        )
    return review_blocks


def chunk_revision_blocks(blocks: list[dict], max_chars: int = 9000, max_items: int = 70) -> list[list[dict]]:
    chunks = []
    current = []
    current_chars = 0

    for block in blocks:
        text_length = len(block.get("text", ""))
        if current and (len(current) >= max_items or current_chars + text_length > max_chars):
            chunks.append(current)
            current = []
            current_chars = 0

        current.append(block)
        current_chars += text_length

    if current:
        chunks.append(current)
    return chunks


def cleanup_transcription_revision_artifacts(project_id: str):
    for path in (
        get_revised_transcript_txt_path(project_id),
        get_revised_transcript_srt_path(project_id),
        get_transcript_revision_error_path(project_id),
    ):
        if path.exists():
            path.unlink()


def mark_transcription_revision_failed(project_id: str, reason: str) -> dict:
    cleanup_transcription_revision_artifacts(project_id)
    get_transcript_revision_error_path(project_id).write_text(reason, encoding="utf-8")
    return {
        "applied": False,
        "status": "failed",
        "detail": reason,
        "glossary_count": len(get_glossary_entries()),
    }


def revise_transcription_with_ai(project_id: str) -> dict:
    client = get_openai_client()
    if not client:
        raise RuntimeError(
            "OpenAI API Key ausente. Configure e teste a OpenAI em Configurações antes de gerar a transcrição revisada."
        )

    srt_path = get_transcript_srt_path(project_id)
    if not srt_path.exists():
        raise RuntimeError("SRT original ausente. Não foi possível revisar a transcrição com IA.")

    srt_text = srt_path.read_text(encoding="utf-8")
    blocks = parse_srt_blocks_for_revision(srt_text)
    if not blocks:
        raise RuntimeError("Nenhum bloco SRT válido para revisão com IA.")

    glossary_entries = get_glossary_entries()
    glossary_text = format_glossary_for_prompt(glossary_entries)
    revised_by_uid = {}

    system_prompt = """
Você corrige transcrições de áudio em português brasileiro para um editor de vídeos local.

Regras obrigatórias:
- Corrija erros prováveis de reconhecimento de fala.
- Use o glossário como referência forte para nomes próprios, marcas, empresas, produtos e termos técnicos.
- Corrija palavras ou expressões quebradas quando o contexto deixar claro.
- Remova palavras sem sentido apenas quando forem ruído evidente de transcrição.
- Ajuste pontuação e capitalização.
- Preserve linguagem oral, natural e próxima do que foi dito.
- Não formalize demais.
- Não resuma.
- Não acrescente informação.
- Não invente frases, dados, nomes ou contexto.
- Preserve o sentido e a ordem do conteúdo.
- Responda somente JSON válido.
"""

    chunks = chunk_revision_blocks(blocks)
    for chunk_index, chunk in enumerate(chunks, start=1):
        raise_if_current_job_cancelled()
        progress = min(98, 92 + int(((chunk_index - 1) / max(1, len(chunks))) * 6))
        update_job(
            project_id,
            "generate_transcription",
            build_running_status(
                "generate_transcription",
                progress,
                "Revisando transcrição com IA...",
                f"Bloco {chunk_index} de {len(chunks)} · glossário com {len(glossary_entries)} termo(s).",
            ),
        )
        payload = [
            {
                "uid": block["uid"],
                "text": block["text"],
            }
            for block in chunk
        ]
        user_prompt = f"""
Glossário do EVR Deluxe:
{glossary_text}

Corrija os textos abaixo. Preserve exatamente os mesmos uids.
Retorne neste formato:
{{
  "segments": [
    {{"uid": 1, "text": "texto corrigido"}}
  ]
}}

Bloco {chunk_index} de {len(chunks)}:
{json.dumps(payload, ensure_ascii=False)}
"""
        response = client.chat.completions.create(
            model=get_openai_model(),
            temperature=0.1,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
        raise_if_current_job_cancelled()

        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("resposta vazia na revisão da transcrição")

        data = json.loads(content)
        revised_segments = data.get("segments")
        if not isinstance(revised_segments, list):
            raise RuntimeError("JSON de revisão sem lista segments")

        for item in revised_segments:
            if not isinstance(item, dict):
                continue
            try:
                uid = int(item.get("uid"))
            except (TypeError, ValueError):
                continue
            revised_text = str(item.get("text", "")).strip()
            if revised_text:
                revised_by_uid[uid] = revised_text

    for block in blocks:
        block["revised_text"] = revised_by_uid.get(block["uid"], block["text"])

    get_revised_transcript_srt_path(project_id).write_text(build_srt_from_revision_blocks(blocks), encoding="utf-8")
    get_revised_transcript_txt_path(project_id).write_text(build_txt_from_revision_blocks(blocks), encoding="utf-8")

    error_path = get_transcript_revision_error_path(project_id)
    if error_path.exists():
        error_path.unlink()

    return {
        "applied": True,
        "status": "applied",
        "detail": f"Revisão aplicada em {len(blocks)} bloco(s) de legenda.",
        "glossary_count": len(glossary_entries),
    }


# Calcula a intersecção entre dois intervalos em segundos.
def calculate_overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


# Normaliza labels retornados pela diarização para SPEAKER_01, SPEAKER_02 etc.
def normalize_speaker_labels(diarization_segments: list[dict]) -> tuple[list[dict], dict]:
    mapping = {}
    next_index = 1

    for segment in diarization_segments:
        raw_speaker = str(segment.get("speaker") or "SPEAKER")
        if raw_speaker not in mapping:
            mapping[raw_speaker] = f"SPEAKER_{next_index:02d}"
            next_index += 1
        segment["speaker"] = mapping[raw_speaker]

    return diarization_segments, mapping


# Worker isolado para a diarização. Manter Pyannote em subprocesso permite
# interromper a etapa 3 de verdade quando o usuário clica em parar.
PYANNOTE_WORKER_CODE = r'''
import json
import os
import sys


def format_seconds_to_time(seconds):
    seconds = float(seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    milliseconds = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{milliseconds:03d}"


def main():
    payload = json.loads(sys.stdin.read() or "{}")
    wav_path = payload["wav_path"]
    output_path = payload["output_path"]
    token = payload["huggingface_token"]
    model = payload["pyannote_model"]
    threads = payload.get("threads")

    if threads:
        threads = int(threads)
        for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
            os.environ[key] = str(threads)
        os.environ["TOKENIZERS_PARALLELISM"] = "false"

    try:
        from pyannote.audio import Pipeline
        try:
            import torch
            if threads:
                torch.set_num_threads(threads)
                torch.set_num_interop_threads(max(1, min(2, threads)))
        except Exception:
            pass
    except ImportError as exc:
        print("pyannote.audio não está instalado. Instale as dependências de diarização antes de mapear participantes.", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        raise SystemExit(21)

    try:
        try:
            pipeline = Pipeline.from_pretrained(model, token=token)
        except TypeError:
            pipeline = Pipeline.from_pretrained(model, use_auth_token=token)
    except Exception as exc:
        print(f"não foi possível carregar o modelo de diarização ({model}). Verifique token, acesso ao modelo e internet.", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        raise SystemExit(22)

    diarization_kwargs = {}
    for key in ("num_speakers", "min_speakers", "max_speakers"):
        value = payload.get(key)
        if value:
            diarization_kwargs[key] = int(value)

    try:
        diarization_output = pipeline(wav_path, **diarization_kwargs)
    except Exception as exc:
        print(f"falha ao rodar diarização no áudio: {exc}", file=sys.stderr)
        raise SystemExit(23)

    diarization = getattr(diarization_output, "speaker_diarization", diarization_output)
    segments = []

    if hasattr(diarization, "itertracks"):
        iterable = diarization.itertracks(yield_label=True)
        for turn, _, speaker in iterable:
            if turn.end <= turn.start:
                continue
            segments.append(
                {
                    "speaker": str(speaker),
                    "start_seconds": round(float(turn.start), 3),
                    "end_seconds": round(float(turn.end), 3),
                    "start": format_seconds_to_time(turn.start),
                    "end": format_seconds_to_time(turn.end),
                }
            )
    else:
        for item in diarization:
            if len(item) == 2:
                turn, speaker = item
            elif len(item) == 3:
                turn, _, speaker = item
            else:
                continue

            if turn.end <= turn.start:
                continue
            segments.append(
                {
                    "speaker": str(speaker),
                    "start_seconds": round(float(turn.start), 3),
                    "end_seconds": round(float(turn.end), 3),
                    "start": format_seconds_to_time(turn.start),
                    "end": format_seconds_to_time(turn.end),
                }
            )

    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(segments, file, ensure_ascii=False)


if __name__ == "__main__":
    main()
'''


# Roda diarização real de áudio com pyannote.audio.
def run_pyannote_diarization(wav_path: Path, num_speakers=None, min_speakers=None, max_speakers=None) -> list[dict]:
    huggingface_token = get_huggingface_token()
    pyannote_model = get_pyannote_model()
    profile = get_performance_profile()

    if not huggingface_token:
        raise RuntimeError(
            "Token da Hugging Face ausente. Configure o token em Configurações > APIs e Integrações."
        )

    with tempfile.TemporaryDirectory(prefix="evr_pyannote_") as temp_dir:
        worker_path = Path(temp_dir) / "pyannote_worker.py"
        output_path = Path(temp_dir) / "diarization_segments.json"
        worker_path.write_text(PYANNOTE_WORKER_CODE, encoding="utf-8")

        payload = {
            "wav_path": str(wav_path),
            "output_path": str(output_path),
            "huggingface_token": huggingface_token,
            "pyannote_model": pyannote_model,
            "threads": profile.get("threads"),
            "num_speakers": int(num_speakers) if num_speakers else None,
            "min_speakers": int(min_speakers) if min_speakers and not num_speakers else None,
            "max_speakers": int(max_speakers) if max_speakers and not num_speakers else None,
        }

        run_processing_command(
            [sys.executable, str(worker_path)],
            input=json.dumps(payload, ensure_ascii=False),
            check=True,
            capture_output=True,
            text=True,
        )

        if not output_path.exists():
            raise RuntimeError("Pyannote não retornou segmentos de participantes.")

        segments = json.loads(output_path.read_text(encoding="utf-8"))
        if not isinstance(segments, list):
            raise RuntimeError("Pyannote retornou um formato inválido de segmentos.")

    segments, _ = normalize_speaker_labels(segments)
    return segments


# Encontra o speaker mais provável para um segmento de texto por sobreposição de tempo.
def find_best_speaker_for_text_segment(text_segment: dict, diarization_segments: list[dict]) -> tuple[str, float]:
    scores = {}
    for diar_segment in diarization_segments:
        overlap = calculate_overlap(
            float(text_segment["start_seconds"]),
            float(text_segment["end_seconds"]),
            float(diar_segment["start_seconds"]),
            float(diar_segment["end_seconds"]),
        )
        if overlap > 0:
            speaker = diar_segment.get("speaker", "SPEAKER_00")
            scores[speaker] = scores.get(speaker, 0.0) + overlap

    if not scores:
        return "SPEAKER_UNKNOWN", 0.0

    best_speaker, best_overlap = max(scores.items(), key=lambda item: item[1])
    segment_duration = max(0.001, float(text_segment["end_seconds"]) - float(text_segment["start_seconds"]))
    confidence = min(1.0, best_overlap / segment_duration)
    return best_speaker, round(confidence, 3)


# Combina segmentos SRT com a diarização de áudio.
def build_speaker_transcript_from_segments(srt_segments: list[dict], diarization_segments: list[dict]) -> dict:
    merged_segments = []
    speaker_stats = {}

    for text_segment in srt_segments:
        speaker, confidence = find_best_speaker_for_text_segment(text_segment, diarization_segments)
        segment = dict(text_segment)
        segment["speaker"] = speaker
        segment["speaker_name"] = ""
        segment["confidence"] = confidence
        merged_segments.append(segment)

        duration = max(0.0, float(segment["end_seconds"]) - float(segment["start_seconds"]))
        if speaker not in speaker_stats:
            speaker_stats[speaker] = {"id": speaker, "name": "", "segments": 0, "seconds": 0.0}
        speaker_stats[speaker]["segments"] += 1
        speaker_stats[speaker]["seconds"] += duration

    total_seconds = sum(item["seconds"] for item in speaker_stats.values()) or 1.0
    speakers = []
    for speaker_id in sorted(speaker_stats):
        item = speaker_stats[speaker_id]
        item["seconds"] = round(item["seconds"], 2)
        item["percentage"] = round((item["seconds"] / total_seconds) * 100, 1)
        speakers.append(item)

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "tool": "pyannote.audio",
        "model": get_pyannote_model(),
        "speakers": speakers,
        "segments": merged_segments,
    }


# Atualiza nomes amigáveis dos speakers dentro do JSON.
def apply_speaker_names(speaker_transcript: dict, names_by_id: dict) -> dict:
    names_by_id = {key: value.strip() for key, value in names_by_id.items() if value.strip()}

    for speaker in speaker_transcript.get("speakers", []):
        speaker_id = speaker.get("id", "")
        speaker["name"] = names_by_id.get(speaker_id, speaker.get("name", ""))

    for segment in speaker_transcript.get("segments", []):
        speaker_id = segment.get("speaker", "")
        segment["speaker_name"] = names_by_id.get(speaker_id, segment.get("speaker_name", ""))

    return speaker_transcript


# Monta transcrição com speakers para a IA.
def build_ai_transcript_from_speakers(speaker_transcript: dict, selected_speakers: list[str] | None = None) -> str:
    selected = set(selected_speakers or [])
    speaker_names = {speaker.get("id"): speaker.get("name", "") for speaker in speaker_transcript.get("speakers", [])}
    lines = []

    for segment in speaker_transcript.get("segments", []):
        speaker_id = segment.get("speaker", "")
        if selected and speaker_id not in selected:
            continue

        display_name = speaker_names.get(speaker_id) or segment.get("speaker_name") or speaker_id
        lines.append(f"[{segment.get('start')} - {segment.get('end')}] {display_name}: {segment.get('text', '')}")

    return "\n".join(lines).strip()


# Lê uma margem numérica em segundos.
def parse_margin_seconds(value: str) -> int:
    value = (value or "").strip().replace(",", ".")
    if not value:
        return 0
    margin = float(value)
    if margin < 0:
        raise ValueError("margem inválida")
    return int(round(margin))


# Aplica margem inicial e final respeitando o limite do vídeo.
def apply_cut_margin(start_time: str, end_time: str, initial_margin: int, final_margin: int, max_duration: float | None = None):
    start_seconds = parse_time_to_seconds(start_time)
    end_seconds = parse_time_to_seconds(end_time)

    adjusted_start = max(0, start_seconds - initial_margin)
    adjusted_end = end_seconds + final_margin

    if max_duration is not None:
        adjusted_end = min(int(max_duration), adjusted_end)

    if adjusted_end <= adjusted_start:
        raise ValueError("margem inválida")

    return format_seconds_to_time(adjusted_start), format_seconds_to_time(adjusted_end)


# Carrega as sugestões da IA geradas para o projeto.
def load_ai_suggestions(project_id: str) -> dict:
    suggestions_path = get_ai_suggestions_path(project_id)
    if suggestions_path.exists():
        with suggestions_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {"cuts": []}


def normalize_ai_suggestions_payload(suggestions: dict, cut_option_count: int, hook_option_count: int) -> dict:
    raw_cuts = suggestions.get("cuts", []) if isinstance(suggestions, dict) else []
    if not isinstance(raw_cuts, list):
        return {"cuts": []}

    normalized_cuts = []
    for raw_item in raw_cuts[:cut_option_count]:
        if not isinstance(raw_item, dict):
            continue

        item = dict(raw_item)
        primary_hook = {
            "hook_start": str(item.get("hook_start", "")).strip(),
            "hook_end": str(item.get("hook_end", "")).strip(),
            "hook_title": str(item.get("hook_title", "")).strip() or "Gancho principal",
            "reason": str(item.get("hook_reason") or item.get("reason") or "").strip(),
        }

        hook_options = []
        seen_hooks = set()

        def append_hook_option(option: dict):
            if not isinstance(option, dict):
                return
            hook_start = str(option.get("hook_start", "")).strip()
            hook_end = str(option.get("hook_end", "")).strip()
            if not hook_start or not hook_end:
                return
            dedupe_key = (hook_start, hook_end, str(option.get("hook_title", "")).strip().casefold())
            if dedupe_key in seen_hooks:
                return
            seen_hooks.add(dedupe_key)
            hook_options.append(
                {
                    "hook_start": hook_start,
                    "hook_end": hook_end,
                    "hook_title": str(option.get("hook_title", "")).strip() or f"Gancho {len(hook_options) + 1:02d}",
                    "reason": str(option.get("reason", "")).strip(),
                }
            )

        append_hook_option(primary_hook)
        raw_hook_options = item.get("hook_options", [])
        if not isinstance(raw_hook_options, list):
            raw_hook_options = []
        for option in raw_hook_options:
            append_hook_option(option)

        if not item.get("content_start") or not item.get("content_end") or not hook_options:
            continue

        if hook_options:
            selected_primary = hook_options[0]
            item["hook_start"] = selected_primary["hook_start"]
            item["hook_end"] = selected_primary["hook_end"]
            item["hook_title"] = selected_primary["hook_title"]
            item["hook_options"] = hook_options[:hook_option_count]

        normalized_cuts.append(item)

    return {"cuts": normalized_cuts}


# Carrega o último pedido livre enviado para a IA.
def load_ai_request(project_id: str) -> dict:
    request_path = get_ai_request_path(project_id)
    if request_path.exists():
        try:
            with request_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


# Salva o pedido livre enviado para a IA.
def save_ai_request(project_id: str, data: dict):
    request_path = get_ai_request_path(project_id)
    with request_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# Carrega a transcrição enriquecida com speakers.
def load_speaker_transcript(project_id: str) -> dict:
    speaker_path = get_speaker_transcript_path(project_id)
    if speaker_path.exists():
        try:
            with speaker_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


# Salva a transcrição enriquecida com speakers.
def save_speaker_transcript(project_id: str, data: dict):
    speaker_path = get_speaker_transcript_path(project_id)
    with speaker_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# Carrega as configurações usadas na diarização.
def load_speaker_settings(project_id: str) -> dict:
    path = get_speaker_settings_path(project_id)
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


# Salva as configurações usadas na diarização.
def save_speaker_settings_data(project_id: str, data: dict):
    path = get_speaker_settings_path(project_id)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# Lê inteiro opcional vindo de formulário/config.
def parse_optional_positive_int(value):
    value = str(value or "").strip()
    if not value:
        return None
    number = int(value)
    if number <= 0:
        return None
    return number


def parse_bounded_int(value, default_value: int, min_value: int, max_value: int) -> int:
    try:
        number = int(float(str(value or "").strip().replace(",", ".")))
    except (TypeError, ValueError):
        number = default_value
    return max(min_value, min(max_value, number))


# Carrega as sugestões selecionadas para a seção 5.
def load_selected_ai_cuts(project_id: str) -> list:
    selected_path = get_selected_ai_cuts_path(project_id)
    if selected_path.exists():
        with selected_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return []


# Salva as sugestões selecionadas para a seção 5.
def save_selected_ai_cuts(project_id: str, cuts: list):
    selected_path = get_selected_ai_cuts_path(project_id)
    with selected_path.open("w", encoding="utf-8") as f:
        json.dump(cuts, f, ensure_ascii=False, indent=2)


# Limpa apenas a seção 5 do projeto.
def clear_selected_ai_cuts(project_id: str):
    selected_path = get_selected_ai_cuts_path(project_id)
    if selected_path.exists():
        selected_path.unlink()


# Carrega o registro principal dos cortes com fallback legado.
def load_cuts_registry(project_id: str) -> list:
    json_path = get_cuts_registry_path(project_id)
    txt_path = get_cuts_txt_path(project_id)

    if json_path.exists():
        with json_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []

    cuts = []
    if txt_path.exists():
        batch_id = "legacy"
        with txt_path.open("r", encoding="utf-8") as f:
            for line in f:
                parts = [part.strip() for part in line.strip().split("|")]
                if len(parts) >= 3 and parts[0] and parts[1] and parts[2]:
                    cuts.append(
                        {
                            "start": parts[0],
                            "end": parts[1],
                            "name": parts[2],
                            "batch_id": batch_id,
                            "status": "pending",
                            "output_name": "",
                        }
                    )
    return cuts


# Salva o registro principal e também atualiza o arquivo txt legado.
def save_cuts_registry(project_id: str, cuts: list):
    json_path = get_cuts_registry_path(project_id)
    txt_path = get_cuts_txt_path(project_id)

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(cuts, f, ensure_ascii=False, indent=2)

    with txt_path.open("w", encoding="utf-8") as f:
        for cut in cuts:
            f.write(f"{cut.get('start', '')}|{cut.get('end', '')}|{cut.get('name', '')}\n")


# Retorna os cortes para a tela já ordenados por lote e nome.
def load_cuts(project_id: str) -> list:
    return load_cuts_registry(project_id)


# Descobre o próximo número sequencial de Conteúdo/Gancho.
def get_next_cut_number(project_id: str) -> int:
    highest_number = 0
    for cut in load_cuts_registry(project_id):
        name = cut.get("name", "")
        if name.startswith("Conteúdo ") or name.startswith("Gancho "):
            try:
                prefix_removed = name.split(" - ")[0]
                number_part = prefix_removed.split(" ")[1]
                highest_number = max(highest_number, int(number_part))
            except (IndexError, ValueError):
                pass
    return highest_number + 1


# Retorna a lista dos arquivos processados do projeto.
def get_processed_files(project_id: str) -> list:
    files = []
    for cut in load_cuts_registry(project_id):
        output_name = cut.get("output_name", "").strip()
        if cut.get("status") == "processed" and output_name:
            output_path = get_project_subdir(project_id, "Videos Finalizados") / output_name
            if output_path.exists() and output_name not in files:
                files.append(output_name)
    return files


def get_processed_file_path(project_id: str, filename: str) -> Path | None:
    if filename not in get_processed_files(project_id):
        return None
    output_path = get_project_subdir(project_id, "Videos Finalizados") / filename
    if output_path.exists():
        return output_path
    return None


# Retorna o batch_id pendente mais recente.
def get_latest_pending_batch_id(project_id: str) -> str | None:
    pending_ids = [cut.get("batch_id") for cut in load_cuts_registry(project_id) if cut.get("status") == "pending" and cut.get("batch_id")]
    if not pending_ids:
        return None
    return sorted(pending_ids)[-1]


# Atualiza o metadata de acordo com o estado atual do registro de cortes.
def refresh_cuts_defined_flag(project_id: str):
    metadata = load_metadata(project_id)
    cuts = load_cuts_registry(project_id)
    metadata["cuts_defined"] = "yes" if cuts else "no"
    save_metadata(project_id, metadata)


# Atualiza o status de um job.
def update_job(project_id: str, job_type: str, status: dict):
    if is_job_cancelled(project_id, job_type) and status.get("state") != "error":
        return
    project_path = get_project_path(project_id)
    previous = load_job_status(project_path, job_type)
    payload = dict(status or {})
    if payload.get("state") in {"running", "success", "error"}:
        previous_running_mode = previous.get("performance_mode") if previous.get("state") == "running" else None
        performance_mode = payload.get("performance_mode") or previous_running_mode
        if not performance_mode:
            performance_mode = get_performance_profile().get("mode") or get_performance_mode()
        previous_running_label = previous.get("performance_label") if previous.get("state") == "running" else None
        payload["performance_mode"] = performance_mode
        payload["performance_label"] = payload.get("performance_label") or previous_running_label or get_performance_mode_label(performance_mode)

    save_job_status(project_path, job_type, payload)
    saved = load_job_status(project_path, job_type)
    if (
        previous.get("state") != saved.get("state")
        or previous.get("progress") != saved.get("progress")
        or previous.get("message") != saved.get("message")
    ):
        append_project_log(
            project_id,
            job_type,
            f"status:{saved.get('state', 'indefinido')}",
            f"{saved.get('progress', 0)}% · {saved.get('message', '')}",
            saved.get("detail", ""),
        )
    if previous.get("state") != "success" and saved.get("state") == "success":
        record_job_history(project_id, job_type, saved)


# Marca falha amigável de um job.
def fail_job(project_id: str, job_type: str, raw_error: str):
    append_project_log(project_id, job_type, "erro_bruto", raw_error)
    if str(raw_error) == JOB_CANCELLED_MESSAGE:
        update_job(
            project_id,
            job_type,
            build_error_status(
                job_type,
                "Processamento cancelado",
                "O job foi interrompido antes da conclusão. Execute esta etapa novamente quando quiser continuar.",
            ),
        )
        metadata = load_metadata(project_id)
        metadata["status"] = "Processamento cancelado"
        save_metadata(project_id, metadata)
        return

    friendly = humanize_error(job_type, raw_error)
    update_job(project_id, job_type, build_error_status(job_type, friendly["message"], friendly["detail"]))
    metadata = load_metadata(project_id)
    metadata["status"] = friendly["message"]
    save_metadata(project_id, metadata)


def parse_status_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def get_elapsed_seconds(status):
    if not status:
        return None

    elapsed_seconds = status.get("elapsed_seconds")
    if elapsed_seconds is None:
        started_at = parse_status_datetime(status.get("started_at"))
        finished_at = parse_status_datetime(status.get("finished_at"))
        if started_at and not finished_at and status.get("state") == "running":
            finished_at = datetime.now(started_at.tzinfo) if started_at.tzinfo else datetime.now()
        if started_at and finished_at:
            elapsed_seconds = max(0, int((finished_at - started_at).total_seconds()))

    if elapsed_seconds is None:
        return None

    try:
        return max(0, int(float(elapsed_seconds)))
    except (TypeError, ValueError):
        return None


def format_seconds_as_time(elapsed_seconds):
    if elapsed_seconds is None:
        return ""

    hours = elapsed_seconds // 3600
    minutes = (elapsed_seconds % 3600) // 60
    seconds = elapsed_seconds % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def format_elapsed_time(status):
    return format_seconds_as_time(get_elapsed_seconds(status))


def get_total_processing_time(job_statuses):
    total_seconds = 0
    for status in job_statuses.values():
        elapsed_seconds = get_elapsed_seconds(status)
        if elapsed_seconds is not None:
            total_seconds += elapsed_seconds
    return format_seconds_as_time(total_seconds) if total_seconds else ""


def get_job_history_samples(job_type: str) -> list[dict]:
    history = load_app_config().get("progress_history", {})
    samples = history.get(job_type, []) if isinstance(history, dict) else []
    return [sample for sample in samples if isinstance(sample, dict)]


def parse_metadata_duration(metadata: dict) -> float | None:
    try:
        duration = float(metadata.get("duration") or 0)
    except (TypeError, ValueError):
        return None
    return duration if duration > 0 else None


def record_job_history(project_id: str, job_type: str, status: dict):
    elapsed_seconds = get_elapsed_seconds(status)
    if not elapsed_seconds or elapsed_seconds < 1:
        return

    metadata = load_metadata(project_id)
    config = load_app_config()
    history = config.get("progress_history", {})
    if not isinstance(history, dict):
        history = {}

    samples = history.get(job_type, [])
    if not isinstance(samples, list):
        samples = []

    samples.append(
        {
            "project_id": project_id,
            "elapsed_seconds": elapsed_seconds,
            "video_duration_seconds": parse_metadata_duration(metadata),
            "performance_mode": status.get("performance_mode") or get_performance_mode(),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    history[job_type] = samples[-JOB_HISTORY_LIMIT:]
    save_app_config({"progress_history": history})


def estimate_job_total_seconds(project_id: str, job_type: str) -> tuple[int | None, int]:
    metadata = load_metadata(project_id)
    current_duration = parse_metadata_duration(metadata)
    samples = get_job_history_samples(job_type)
    valid_samples = []

    for sample in samples:
        try:
            elapsed_seconds = float(sample.get("elapsed_seconds") or 0)
            sample_duration = float(sample.get("video_duration_seconds") or 0)
        except (TypeError, ValueError):
            continue

        if elapsed_seconds <= 0:
            continue
        valid_samples.append((elapsed_seconds, sample_duration if sample_duration > 0 else None))

    if len(valid_samples) < 2:
        return None, len(valid_samples)

    duration_samples = [(elapsed, duration) for elapsed, duration in valid_samples if current_duration and duration]
    if duration_samples:
        average_ratio = sum(elapsed / duration for elapsed, duration in duration_samples) / len(duration_samples)
        return max(1, int(current_duration * average_ratio)), len(valid_samples)

    average_elapsed = sum(elapsed for elapsed, _ in valid_samples) / len(valid_samples)
    return max(1, int(average_elapsed)), len(valid_samples)


def annotate_job_status(project_id: str, job_type: str, status: dict) -> dict:
    enriched = dict(status or {})
    elapsed_seconds = get_elapsed_seconds(enriched)
    if elapsed_seconds is not None:
        enriched["elapsed_seconds"] = elapsed_seconds
        enriched["elapsed_label"] = format_seconds_as_time(elapsed_seconds)

    updated_at = parse_status_datetime(enriched.get("updated_at"))
    if updated_at:
        now = datetime.now(updated_at.tzinfo) if updated_at.tzinfo else datetime.now()
        enriched["updated_ago_seconds"] = max(0, int((now - updated_at).total_seconds()))

    if not enriched.get("performance_mode"):
        performance_settings = get_public_performance_settings()
        enriched["performance_mode"] = performance_settings["mode"]
        enriched["performance_label"] = performance_settings["label"]
    else:
        enriched["performance_label"] = enriched.get("performance_label") or get_performance_mode_label(enriched.get("performance_mode"))

    historical_total, sample_count = estimate_job_total_seconds(project_id, job_type)
    enriched["history_sample_count"] = sample_count

    if enriched.get("state") != "running" or elapsed_seconds is None:
        return enriched

    progress = max(0, min(100, int(enriched.get("progress") or 0)))
    progress_total = None
    if progress >= 5:
        progress_total = max(elapsed_seconds, int(elapsed_seconds / max(progress / 100, 0.01)))

    estimated_total = None
    estimate_source = ""
    if historical_total:
        estimated_total = historical_total
        estimate_source = "history"
        if progress_total and progress >= 20:
            estimated_total = int((historical_total * 0.55) + (progress_total * 0.45))
            estimate_source = "history_and_progress"
    elif progress_total:
        estimated_total = progress_total
        estimate_source = "current_progress"

    if estimated_total:
        estimated_total = max(elapsed_seconds, estimated_total)
        enriched["estimated_total_seconds"] = estimated_total
        enriched["estimated_remaining_seconds"] = max(0, estimated_total - elapsed_seconds)
        enriched["estimate_source"] = estimate_source

    return enriched


def get_project_step_elapsed(job_statuses):
    return {
        "step_2": format_elapsed_time(job_statuses.get("generate_transcription")),
        "step_3": format_elapsed_time(job_statuses.get("identify_speakers")),
        "step_5": format_elapsed_time(job_statuses.get("suggest_cuts")),
        "step_9": format_elapsed_time(job_statuses.get("process_cuts")),
    }


def mark_speakers_reviewed(project_id: str, status: str | None = None):
    metadata = load_metadata(project_id)
    metadata["speakers_reviewed"] = "yes"
    if status:
        metadata["status"] = status
    save_metadata(project_id, metadata)


# Inicia um job em thread se ainda não houver outro rodando.
def start_background_job(project_id: str, job_type: str, target):
    project_path = get_project_path(project_id)
    current = load_job_status(project_path, job_type)
    if current.get("state") == "running":
        return False

    clear_job_cancel(project_id, job_type)
    captured_profile = dict(get_performance_profile())
    append_project_log(
        project_id,
        job_type,
        "inicio",
        f"Modo de desempenho: {captured_profile.get('label') or captured_profile.get('mode') or get_performance_mode()}",
    )
    update_job(project_id, job_type, build_running_status(job_type, 3, "Iniciando..."))

    def run_with_job_context():
        key = (project_id, job_type)
        keep_awake_process = None
        JOB_CONTEXT.project_id = project_id
        JOB_CONTEXT.job_type = job_type
        JOB_CONTEXT.performance_profile = captured_profile
        try:
            profile = captured_profile
            keep_awake_process = start_job_keep_awake(key, profile)
            schedule_display_sleep(key, profile, keep_awake_process)
            target(project_id)
        finally:
            stop_job_keep_awake(key, keep_awake_process)
            with ACTIVE_PROCESS_LOCK:
                ACTIVE_PROCESSES.pop(key, None)
                ACTIVE_KEEP_AWAKE_PROCESSES.pop(key, None)
                CANCELLED_JOBS.discard(key)
            JOB_CONTEXT.project_id = None
            JOB_CONTEXT.job_type = None
            JOB_CONTEXT.performance_profile = None

    thread = threading.Thread(target=run_with_job_context, daemon=True)
    thread.start()
    return True


# Executa a transcrição local em segundo plano.
def run_generate_transcription_job(project_id: str):
    metadata = load_metadata(project_id)
    video_path = metadata.get("video_path", "")

    try:
        if not video_path or not Path(video_path).exists():
            raise FileNotFoundError("vídeo ausente")
        if not WHISPER_CLI_PATH.exists():
            raise FileNotFoundError("whisper-cli não encontrado")
        if not WHISPER_MODEL_PATH.exists():
            raise FileNotFoundError("modelo whisper não encontrado")
        if not get_openai_client():
            raise RuntimeError(
                "OpenAI API Key ausente. A transcrição revisada por IA é obrigatória; configure e teste a OpenAI em Configurações antes de começar."
            )

        project_path = get_project_path(project_id)
        wav_path = get_project_subdir(project_id, "Audios") / f"{project_id}.wav"
        transcript_base = get_project_subdir(project_id, "Transcrições") / f"{project_id}_transcricao"
        transcript_txt_path = get_transcript_txt_path(project_id)
        transcript_srt_path = get_transcript_srt_path(project_id)
        cleanup_transcription_revision_artifacts(project_id)

        update_job(project_id, "generate_transcription", build_running_status("generate_transcription", 10, "Preparando áudio..."))
        run_processing_command(
            ffmpeg_command(
                "-y",
                "-i",
                video_path,
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                *ffmpeg_thread_args(),
                str(wav_path),
            ),
            check=True,
            capture_output=True,
            text=True,
        )

        update_job(project_id, "generate_transcription", build_running_status("generate_transcription", 45, "Gerando transcrição..."))
        whisper_command = [
            str(WHISPER_CLI_PATH),
            "-m",
            str(WHISPER_MODEL_PATH),
        ]
        performance_threads = get_performance_profile().get("threads")
        if performance_threads:
            whisper_command.extend(["-t", str(performance_threads)])
        whisper_command.extend(
            [
                "-f",
                str(wav_path),
                "-l",
                "pt",
                "-otxt",
                "-osrt",
                "-of",
                str(transcript_base),
            ]
        )
        run_processing_command(
            whisper_command,
            check=True,
            capture_output=True,
            text=True,
        )

        update_job(project_id, "generate_transcription", build_running_status("generate_transcription", 88, "Finalizando arquivos originais..."))

        if not transcript_txt_path.exists():
            raise RuntimeError("txt/srt não gerado")

        update_job(project_id, "generate_transcription", build_running_status("generate_transcription", 92, "Revisando transcrição com IA...", "Aplicando glossário e correções automáticas."))
        revision_result = revise_transcription_with_ai(project_id)

        metadata["transcription_generated"] = "yes"
        metadata["transcription_revision_status"] = revision_result["status"]
        metadata["transcription_revision_applied"] = "yes" if revision_result["applied"] else "no"
        metadata["transcription_revision_glossary_count"] = str(revision_result["glossary_count"])
        metadata["transcription_revision_model"] = get_openai_model()
        metadata["transcription_revision_model_label"] = get_openai_model_display_label()
        metadata["status"] = "Transcrição revisada com IA"
        save_metadata(project_id, metadata)
        success_detail = f"TXT e SRT revisados foram gerados. Glossário utilizado: {revision_result['glossary_count']} termo(s)."
        update_job(project_id, "generate_transcription", build_success_status("generate_transcription", "Transcrição concluída", success_detail))
    except subprocess.CalledProcessError as e:
        raw = e.stderr or e.stdout or str(e)
        command_parts = [str(part) for part in (e.cmd or [])]
        if any("ffmpeg" in part for part in command_parts):
            raw = f"ffmpeg falhou: {raw}"
        else:
            raw = f"whisper falhou: {raw}"
        fail_job(project_id, "generate_transcription", raw)
    except Exception as e:
        fail_job(project_id, "generate_transcription", str(e))


# Executa a identificação de speakers em segundo plano.
def run_identify_speakers_job(project_id: str):
    metadata = load_metadata(project_id)
    video_path = metadata.get("video_path", "")

    try:
        srt_path = get_preferred_transcript_srt_path(project_id)
        if not srt_path.exists():
            raise FileNotFoundError("transcrição SRT ausente")

        wav_path = get_project_subdir(project_id, "Audios") / f"{project_id}.wav"
        if not wav_path.exists():
            if not video_path or not Path(video_path).exists():
                raise FileNotFoundError("áudio e vídeo ausentes")

            update_job(project_id, "identify_speakers", build_running_status("identify_speakers", 8, "Preparando áudio para diarização..."))
            run_processing_command(
                ffmpeg_command(
                    "-y",
                    "-i",
                    video_path,
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    *ffmpeg_thread_args(),
                    str(wav_path),
                ),
                check=True,
                capture_output=True,
                text=True,
            )

        update_job(project_id, "identify_speakers", build_running_status("identify_speakers", 20, "Lendo transcrição com timestamps..."))
        srt_text = srt_path.read_text(encoding="utf-8")
        srt_segments = parse_srt_segments(srt_text)
        if not srt_segments:
            raise RuntimeError("nenhum segmento válido encontrado no SRT")

        speaker_settings = load_speaker_settings(project_id)
        num_speakers = parse_optional_positive_int(speaker_settings.get("num_speakers"))
        min_speakers = parse_optional_positive_int(speaker_settings.get("min_speakers"))
        max_speakers = parse_optional_positive_int(speaker_settings.get("max_speakers"))

        detail_parts = []
        if num_speakers:
            detail_parts.append(f"número esperado: {num_speakers}")
        else:
            if min_speakers:
                detail_parts.append(f"mínimo: {min_speakers}")
            if max_speakers:
                detail_parts.append(f"máximo: {max_speakers}")
        detail = " · ".join(detail_parts) if detail_parts else "Sem número de participantes definido."

        update_job(project_id, "identify_speakers", build_running_status("identify_speakers", 45, "Mapeando participantes pelo áudio...", detail))
        diarization_segments = run_pyannote_diarization(
            wav_path,
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        )
        raise_if_current_job_cancelled()
        if not diarization_segments:
            raise RuntimeError("nenhum participante identificado")

        update_job(project_id, "identify_speakers", build_running_status("identify_speakers", 82, "Combinando participantes com a transcrição..."))
        speaker_transcript = build_speaker_transcript_from_segments(srt_segments, diarization_segments)
        speaker_transcript["settings"] = speaker_settings
        save_speaker_transcript(project_id, speaker_transcript)

        speaker_count = len([speaker for speaker in speaker_transcript.get("speakers", []) if speaker.get("id") != "SPEAKER_UNKNOWN"])
        metadata["speakers_identified"] = "yes"
        metadata["speakers_reviewed"] = "no"
        metadata["status"] = f"Participantes mapeados: {speaker_count}"
        save_metadata(project_id, metadata)

        update_job(
            project_id,
            "identify_speakers",
            build_success_status(
                "identify_speakers",
                "Participantes mapeados",
                f"A diarização encontrou {speaker_count} participante(s) e gerou speaker_transcript.json.",
            ),
        )
    except subprocess.CalledProcessError as e:
        raw = e.stderr or e.stdout or str(e)
        command_parts = [str(part) for part in (e.cmd or [])]
        if any("ffmpeg" in part for part in command_parts):
            fail_job(project_id, "identify_speakers", f"ffmpeg falhou: {raw}")
        else:
            fail_job(project_id, "identify_speakers", f"pyannote falhou: {raw}")
    except Exception as e:
        fail_job(project_id, "identify_speakers", str(e))


# Executa a sugestão de cortes com IA em segundo plano.
def run_suggest_cuts_job(project_id: str):
    metadata = load_metadata(project_id)
    project_path = get_project_path(project_id)

    try:
        openai_client = get_openai_client()
        if not openai_client:
            raise RuntimeError("OpenAI API Key ausente. Configure a chave na tela inicial em Configurações de APIs.")

        srt_path = get_preferred_transcript_srt_path(project_id)
        if not srt_path.exists():
            raise FileNotFoundError("sem srt")

        ai_request = load_ai_request(project_id)
        selected_speakers = ai_request.get("selected_speakers", [])
        if not isinstance(selected_speakers, list):
            selected_speakers = []
        free_prompt = str(ai_request.get("free_prompt", "")).strip()
        cut_option_count = parse_bounded_int(
            ai_request.get("cut_option_count"),
            DEFAULT_AI_CUT_OPTION_COUNT,
            MIN_AI_CUT_OPTION_COUNT,
            MAX_AI_CUT_OPTION_COUNT,
        )
        hook_option_count = parse_bounded_int(
            ai_request.get("hook_option_count"),
            DEFAULT_AI_HOOK_OPTION_COUNT,
            MIN_AI_HOOK_OPTION_COUNT,
            MAX_AI_HOOK_OPTION_COUNT,
        )

        update_job(project_id, "suggest_cuts", build_running_status("suggest_cuts", 15, "Lendo transcrição..."))
        speaker_transcript = load_speaker_transcript(project_id)
        speaker_mode_enabled = bool(speaker_transcript.get("segments"))

        if speaker_mode_enabled:
            transcript_for_ai = build_ai_transcript_from_speakers(speaker_transcript, selected_speakers)
            if not transcript_for_ai:
                raise RuntimeError("nenhum trecho disponível para os participantes selecionados")

            speaker_names = {speaker.get("id"): speaker.get("name", "") for speaker in speaker_transcript.get("speakers", [])}
            selected_labels = []
            for speaker_id in selected_speakers:
                selected_labels.append(speaker_names.get(speaker_id) or speaker_id)
            selected_speakers_text = ", ".join(selected_labels) if selected_labels else "todos os participantes identificados"
            transcript_label = "Transcrição com participantes"
            speaker_instruction = f"""
Filtro de participantes:
- Participantes selecionados para análise: {selected_speakers_text}.
- Se houver participantes selecionados, priorize somente as falas deles.
- Use falas de outros participantes apenas quando forem indispensáveis para entender o contexto, sem transformar essas falas no centro do corte.
"""
        else:
            transcript_for_ai = srt_path.read_text(encoding="utf-8")
            transcript_label = "Transcrição SRT"
            speaker_instruction = "Não há diarização disponível. Analise a transcrição inteira."

        user_intent = free_prompt or "Sugira os melhores cortes de conteúdo e ganchos com potencial para redes sociais."

        system_prompt = """
Você é um especialista em edição de vídeos curtos para redes sociais.

Sua tarefa é analisar uma transcrição com timestamps e sugerir os melhores pares de:
1. trecho principal de conteúdo
2. gancho viral interno a esse conteúdo

Regras obrigatórias:
- Priorize conteúdos entre 40 e 90 segundos.
- Se não houver nenhum trecho bom nessa faixa, aceite conteúdos entre 20 e 40 segundos.
- O gancho deve ter entre 5 e 15 segundos.
- O gancho obrigatoriamente deve estar dentro do conteúdo.
- Pode haver sobreposição parcial entre sugestões.
- Priorize trechos com clareza, valor, curiosidade, emoção, emoção humana, quebra de padrão, opinião forte ou utilidade prática.
- Respeite o filtro de participantes quando ele existir.
- Respeite o pedido livre do usuário.
- Respeite a quantidade solicitada de opções de conteúdo/corte e opções de gancho.
- Nunca invente tempos inexistentes.
- Use apenas os timestamps da transcrição como base.
- Se o vídeo for muito curto, ainda assim tente retornar o melhor corte possível respeitando essas regras.
- Responda apenas JSON válido.
"""

        user_prompt = f"""
Analise a transcrição abaixo e sugira pares conteúdo + gancho.

Pedido livre do usuário:
{user_intent}

{speaker_instruction}

Instruções:
- Primeiro tente encontrar conteúdos entre 40 e 90 segundos.
- Se não houver nenhum bom nessa faixa, aceite conteúdos entre 20 e 40 segundos.
- O gancho deve estar dentro do conteúdo e ter entre 5 e 15 segundos.
- Retorne até {cut_option_count} opções de conteúdo/corte se existir material aproveitável.
- Para cada opção de conteúdo, retorne até {hook_option_count} opção(ões) de gancho dentro do próprio conteúdo.
- O campo hook_start/hook_end/hook_title deve representar o melhor gancho daquele conteúdo.
- Quando houver mais de 1 opção de gancho, inclua também hook_options com as alternativas de gancho, colocando a melhor alternativa primeiro.
- Não invente timestamps.
- Os cortes devem fazer sentido como vídeos curtos independentes.

Formato desejado:
{{
  "cuts": [
    {{
      "content_start": "HH:MM:SS",
      "content_end": "HH:MM:SS",
      "hook_start": "HH:MM:SS",
      "hook_end": "HH:MM:SS",
      "title": "Nome curto do conteúdo",
      "hook_title": "Nome curto do gancho",
      "reason": "Justificativa objetiva",
      "hook_options": [
        {{
          "hook_start": "HH:MM:SS",
          "hook_end": "HH:MM:SS",
          "hook_title": "Nome curto do gancho",
          "reason": "Por que esse gancho funciona"
        }}
      ]
    }}
  ]
}}

{transcript_label}:
{transcript_for_ai}
"""

        update_job(project_id, "suggest_cuts", build_running_status("suggest_cuts", 45, "Consultando IA..."))
        response = openai_client.chat.completions.create(
            model=get_openai_model(),
            temperature=0.3,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
        raise_if_current_job_cancelled()

        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("resposta inválida")

        update_job(project_id, "suggest_cuts", build_running_status("suggest_cuts", 80, "Validando resposta..."))
        try:
            suggestions = json.loads(content)
        except json.JSONDecodeError:
            raise RuntimeError("json inválido")

        if not suggestions.get("cuts"):
            raise RuntimeError("nenhuma sugestão")

        suggestions = normalize_ai_suggestions_payload(suggestions, cut_option_count, hook_option_count)
        if not suggestions.get("cuts"):
            raise RuntimeError("nenhuma sugestão")

        get_ai_suggestions_path(project_id).write_text(json.dumps(suggestions, ensure_ascii=False, indent=2), encoding="utf-8")
        metadata["status"] = "Sugestões de cortes geradas por IA"
        save_metadata(project_id, metadata)
        update_job(project_id, "suggest_cuts", build_success_status("suggest_cuts", "Sugestões concluídas", "A IA gerou sugestões de cortes para este projeto."))
    except Exception as e:
        error_log = project_path / "ai_error.txt"
        error_log.write_text(str(e), encoding="utf-8")
        fail_job(project_id, "suggest_cuts", str(e))



# Carrega os trechos manuais do Video Splitter.
def load_split_manual_segments(project_id: str) -> list:
    path = get_split_manual_segments_path(project_id)
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    return []


# Salva os trechos manuais do Video Splitter.
def save_split_manual_segments(project_id: str, segments: list):
    path = get_split_manual_segments_path(project_id)
    with path.open("w", encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False, indent=2)


# Monta os segmentos do Video Splitter.
def build_split_segments(duration: float, mode: str, fixed_seconds: int | None = None, parts: int | None = None) -> list[tuple[float, float]]:
    if not duration or duration <= 0:
        raise ValueError("duração do vídeo inválida")

    segments = []

    if mode in ("fixed_duration", "social_preset"):
        if not fixed_seconds or fixed_seconds <= 0:
            raise ValueError("duração fixa inválida")

        start = 0.0
        while start < duration:
            end = min(duration, start + fixed_seconds)
            if end - start >= 0.5:
                segments.append((start, end))
            start = end

    elif mode == "by_parts":
        if not parts or parts < 1 or parts > 10:
            raise ValueError("número de partes inválido")

        segment_duration = duration / parts
        for index in range(parts):
            start = index * segment_duration
            end = duration if index == parts - 1 else (index + 1) * segment_duration
            if end - start >= 0.5:
                segments.append((start, end))
    else:
        raise ValueError("modo de divisão inválido")

    if not segments:
        raise ValueError("nenhum segmento válido gerado")

    return segments


# Executa o Video Splitter em segundo plano.
def run_split_video_job(project_id: str):
    metadata = load_metadata(project_id)
    video_path = metadata.get("video_path", "")

    try:
        if not video_path or not Path(video_path).exists():
            raise FileNotFoundError("vídeo ausente")

        duration = get_video_duration(video_path)
        if not duration:
            duration = float(metadata.get("duration") or 0)
        if not duration:
            raise ValueError("não foi possível ler a duração do vídeo")

        mode = metadata.get("split_mode", "fixed_duration")
        split_items = []

        if mode == "manual_segments":
            manual_segments = load_split_manual_segments(project_id)
            if not manual_segments:
                raise ValueError("nenhum trecho manual definido")

            for index, item in enumerate(manual_segments, start=1):
                start_time = item.get("start", "").strip()
                end_time = item.get("end", "").strip()
                name = sanitize_filename(item.get("name", "") or f"Trecho {index:02d}")

                start_seconds = parse_time_to_seconds(start_time)
                end_seconds = parse_time_to_seconds(end_time)

                if end_seconds <= start_seconds:
                    raise ValueError(f"trecho {index} inválido")
                if start_seconds >= duration:
                    raise ValueError(f"início do trecho {index} está fora da duração do vídeo")

                end_seconds = min(end_seconds, int(duration))
                split_items.append(
                    {
                        "start_seconds": float(start_seconds),
                        "end_seconds": float(end_seconds),
                        "name": name,
                        "output_base": name,
                        "label": "trecho",
                    }
                )
        else:
            fixed_seconds = int(float(metadata.get("split_duration_seconds") or 0)) if metadata.get("split_duration_seconds") else None
            parts = int(metadata.get("split_parts") or 0) if metadata.get("split_parts") else None
            segments = build_split_segments(duration, mode, fixed_seconds=fixed_seconds, parts=parts)

            for position, (start_seconds, end_seconds) in enumerate(segments, start=1):
                split_items.append(
                    {
                        "start_seconds": float(start_seconds),
                        "end_seconds": float(end_seconds),
                        "name": f"Parte {position:02d}",
                        "output_base": f"{project_id}_parte_{position:02d}",
                        "label": "parte",
                    }
                )

        if not split_items:
            raise ValueError("nenhum segmento válido gerado")

        output_dir = get_project_subdir(project_id, "Videos Finalizados")
        output_dir.mkdir(parents=True, exist_ok=True)

        cuts = load_cuts_registry(project_id)
        remaining_cuts = []

        for cut in cuts:
            if cut.get("source") == "video_splitter":
                output_name = cut.get("output_name", "").strip()
                if output_name:
                    output_path = output_dir / output_name
                    if output_path.exists():
                        output_path.unlink()
            else:
                remaining_cuts.append(cut)

        batch_id = datetime.now().strftime("%Y%m%d%H%M%S")
        generated_cuts = []
        total = len(split_items)

        for position, item in enumerate(split_items, start=1):
            start_seconds = item["start_seconds"]
            end_seconds = item["end_seconds"]
            segment_name = item["name"]
            label = item.get("label", "parte")

            progress = int(5 + ((position - 1) / total) * 90)
            update_job(
                project_id,
                "split_video",
                build_running_status(
                    "split_video",
                    progress,
                    f"Gerando {label} {position} de {total}...",
                    f"{format_seconds_to_time(start_seconds)} até {format_seconds_to_time(end_seconds)}",
                ),
            )

            output_name = build_unique_output_name(output_dir, item.get("output_base") or segment_name)
            output_file = output_dir / output_name
            segment_duration = max(0.5, end_seconds - start_seconds)

            try:
                run_processing_command(
                    ffmpeg_command(
                        "-y",
                        "-ss",
                        f"{start_seconds:.3f}",
                        "-i",
                        video_path,
                        "-t",
                        f"{segment_duration:.3f}",
                        "-c:v",
                        "libx264",
                        "-c:a",
                        "aac",
                        *ffmpeg_thread_args(),
                        str(output_file),
                    ),
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"ffmpeg falhou: {e.stderr or e.stdout or str(e)}")

            if not output_file.exists():
                raise RuntimeError("arquivo de saída não gerado")

            generated_cuts.append(
                {
                    "start": format_seconds_to_time(start_seconds),
                    "end": format_seconds_to_time(end_seconds),
                    "name": segment_name,
                    "batch_id": batch_id,
                    "status": "processed",
                    "output_name": output_name,
                    "source": "video_splitter",
                }
            )

            save_cuts_registry(project_id, remaining_cuts + generated_cuts)

        metadata["status"] = f"Video Splitter concluído: {total} trecho(s) gerado(s)" if mode == "manual_segments" else f"Video Splitter concluído: {total} parte(s) gerada(s)"
        metadata["cuts_defined"] = "yes"
        metadata["duration"] = str(duration)
        save_metadata(project_id, metadata)

        update_job(
            project_id,
            "split_video",
            build_success_status("split_video", "Divisão concluída", f"{total} arquivo(s) foram gerados com sucesso."),
        )
    except Exception as e:
        fail_job(project_id, "split_video", str(e))


# Executa o processamento apenas do lote pendente mais recente.
def run_process_cuts_job(project_id: str):
    metadata = load_metadata(project_id)
    video_path = metadata.get("video_path", "")

    try:
        if not video_path or not Path(video_path).exists():
            raise FileNotFoundError("vídeo ausente")

        cuts = load_cuts_registry(project_id)
        latest_batch_id = get_latest_pending_batch_id(project_id)
        if not latest_batch_id:
            raise RuntimeError("nenhum corte válido")

        pending_cuts = [cut for cut in cuts if cut.get("batch_id") == latest_batch_id and cut.get("status") == "pending"]
        if not pending_cuts:
            raise RuntimeError("nenhum corte válido")

        total = len(pending_cuts)
        for position, cut in enumerate(pending_cuts, start=1):
            progress = int(10 + ((position - 1) / total) * 80)
            update_job(
                project_id,
                "process_cuts",
                build_running_status("process_cuts", progress, f"Processando corte {position} de {total}...", cut.get("name", "")),
            )

            start = cut.get("start", "").strip()
            end = cut.get("end", "").strip()
            name = cut.get("name", "").strip()

            start_seconds = parse_time_to_seconds(start)
            end_seconds = parse_time_to_seconds(end)
            if end_seconds <= start_seconds:
                cut["status"] = "error"
                continue

            output_dir = get_project_subdir(project_id, "Videos Finalizados")
            output_name = build_unique_output_name(output_dir, name)
            output_file = output_dir / output_name

            try:
                run_processing_command(
                    ffmpeg_command(
                        "-y",
                        "-i",
                        video_path,
                        "-ss",
                        start,
                        "-to",
                        end,
                        "-c:v",
                        "libx264",
                        "-c:a",
                        "aac",
                        *ffmpeg_thread_args(),
                        str(output_file),
                    ),
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as e:
                cut["status"] = "error"
                cut["output_name"] = ""
                save_cuts_registry(project_id, cuts)
                raise RuntimeError(f"ffmpeg falhou: {e.stderr or e.stdout or str(e)}")

            if not output_file.exists():
                cut["status"] = "error"
                cut["output_name"] = ""
                save_cuts_registry(project_id, cuts)
                raise RuntimeError("arquivo de saída não gerado")

            cut["status"] = "processed"
            cut["output_name"] = output_name
            save_cuts_registry(project_id, cuts)

        metadata["status"] = "Cortes processados"
        save_metadata(project_id, metadata)
        clear_selected_ai_cuts(project_id)
        refresh_cuts_defined_flag(project_id)
        update_job(project_id, "process_cuts", build_success_status("process_cuts", "Cortes concluídos", f"{total} arquivo(s) foram gerados com sucesso."))
    except Exception as e:
        refresh_cuts_defined_flag(project_id)
        fail_job(project_id, "process_cuts", str(e))


# Aplica margens e salva a seleção da seção 5.
def select_ai_cuts_internal(project_id: str, selected_indexes: list, initial_margin: int, final_margin: int, select_all=False, replace_selected=False, selected_hook_options: dict[int, int] | None = None):
    suggestions = load_ai_suggestions(project_id)
    cuts_list = suggestions.get("cuts", [])
    metadata = load_metadata(project_id)
    max_duration = None
    selected_hook_options = selected_hook_options or {}

    try:
        if metadata.get("duration"):
            max_duration = float(metadata["duration"])
    except ValueError:
        max_duration = None

    indexes_to_use = list(range(len(cuts_list))) if select_all else []
    if not select_all:
        for idx in selected_indexes:
            try:
                indexes_to_use.append(int(idx))
            except ValueError:
                continue

    valid_selected = []
    for i in indexes_to_use:
        if 0 <= i < len(cuts_list):
            item = dict(cuts_list[i])
            hook_options = item.get("hook_options", [])
            if isinstance(hook_options, list) and hook_options:
                hook_option_index = selected_hook_options.get(i, 0)
                if hook_option_index < 0 or hook_option_index >= len(hook_options):
                    hook_option_index = 0
                selected_hook = hook_options[hook_option_index]
                if isinstance(selected_hook, dict):
                    item["hook_start"] = selected_hook.get("hook_start", item.get("hook_start", ""))
                    item["hook_end"] = selected_hook.get("hook_end", item.get("hook_end", ""))
                    item["hook_title"] = selected_hook.get("hook_title", item.get("hook_title", ""))
                    item["hook_reason"] = selected_hook.get("reason", "")
            item["content_start"], item["content_end"] = apply_cut_margin(item["content_start"], item["content_end"], initial_margin, final_margin, max_duration)
            item["hook_start"], item["hook_end"] = apply_cut_margin(item["hook_start"], item["hook_end"], initial_margin, final_margin, max_duration)
            valid_selected.append(item)

    if not valid_selected:
        raise ValueError("Nenhuma sugestão selecionada")

    existing_selected = [] if replace_selected else load_selected_ai_cuts(project_id)
    updated_selected = existing_selected + valid_selected
    save_selected_ai_cuts(project_id, updated_selected)

    return {"added": len(valid_selected), "total": len(updated_selected)}


# Converte a seleção da seção 5 em uma nova remessa pendente.
def append_selected_ai_cuts_to_registry(project_id: str):
    selected_ai_cuts = load_selected_ai_cuts(project_id)
    if not selected_ai_cuts:
        raise ValueError("nenhuma sugestão selecionada")

    cuts = load_cuts_registry(project_id)
    batch_id = datetime.now().strftime("%Y%m%d%H%M%S")
    next_cut_number = get_next_cut_number(project_id)

    for idx, item in enumerate(selected_ai_cuts):
        cut_number = next_cut_number + idx
        cuts.append(
            {
                "start": item["content_start"],
                "end": item["content_end"],
                "name": f"Conteúdo {cut_number:02d} - {item['title']}",
                "batch_id": batch_id,
                "status": "pending",
                "output_name": "",
            }
        )
        cuts.append(
            {
                "start": item["hook_start"],
                "end": item["hook_end"],
                "name": f"Gancho {cut_number:02d} - {item['hook_title']}",
                "batch_id": batch_id,
                "status": "pending",
                "output_name": "",
            }
        )

    save_cuts_registry(project_id, cuts)
    metadata = load_metadata(project_id)
    metadata["status"] = f"{len(selected_ai_cuts) * 2} corte(s) adicionados"
    metadata["cuts_defined"] = "yes"
    save_metadata(project_id, metadata)
    return batch_id, len(selected_ai_cuts) * 2


def get_home_project_stage_label(project_id: str, metadata: dict) -> str:
    task_type = metadata.get("task_type", "ai_cuts")

    if task_type == "video_splitter":
        if get_processed_files(project_id):
            return "Etapa 2 - Arquivos gerados"
        return "Etapa 1 - Vídeo carregado"

    if get_processed_files(project_id):
        return "Etapa 10 - Arquivos processados"
    if load_cuts(project_id):
        return "Etapa 8 - Cortes definidos"
    if load_selected_ai_cuts(project_id):
        return "Etapa 7 - Definir cortes manualmente"
    if load_ai_suggestions(project_id).get("cuts"):
        return "Etapa 6 - Sugestões da IA"
    if load_ai_request(project_id):
        return "Etapa 5 - Sugerir cortes com IA"
    if load_speaker_transcript(project_id).get("speakers"):
        metadata_reviewed = metadata.get("speakers_reviewed") == "yes"
        return "Etapa 4 - Participantes identificados" if metadata_reviewed else "Etapa 3 - Participantes mapeados"
    if metadata.get("transcription_generated") == "yes" or get_transcript_txt_path(project_id).exists():
        return "Etapa 2 - Transcrição gerada"
    return "Etapa 1 - Vídeo carregado"


def duplicate_project_with_options(source_project_id: str, options: dict) -> str:
    source_metadata = load_metadata(source_project_id)
    source_project_path = get_project_path(source_project_id)
    if not source_project_path.exists() or project_is_removed_from_app(source_metadata):
        raise FileNotFoundError("Projeto original não encontrado.")

    keep_video = options.get("keep_video") == "yes"
    keep_transcription = options.get("keep_transcription") == "yes"
    keep_speakers = options.get("keep_speakers") == "yes"
    keep_ai_suggestions = options.get("keep_ai_suggestions") == "yes"
    keep_cuts = options.get("keep_cuts") == "yes"
    keep_processed_files = options.get("keep_processed_files") == "yes"

    if keep_speakers or keep_ai_suggestions:
        keep_transcription = True
    if keep_processed_files:
        keep_cuts = True

    title_value = str(options.get("project_title") or "").strip()
    source_title = source_metadata.get("project_title") or source_project_id
    project_title = title_value or f"{source_title} cópia"
    project_slug = sanitize_filename(project_title).lower().replace(" ", "_")
    new_project_id = build_unique_project_id(project_slug)
    new_project_path = ensure_project_structure(new_project_id)

    new_metadata = dict(source_metadata)
    new_metadata["project_id"] = new_project_id
    new_metadata["project_title"] = project_title
    new_metadata["status"] = "Projeto duplicado"
    new_metadata["removed_from_app"] = "no"
    new_metadata["removed_at"] = ""
    new_metadata["archived"] = "no"
    new_metadata["archived_at"] = ""

    if keep_video:
        source_video = Path(source_metadata.get("video_path", ""))
        if source_video.exists():
            video_name = source_metadata.get("video_name") or source_video.name
            destination_video = get_project_subdir(new_project_id, "Arquivo Video Bruto") / f"{new_project_id}_{video_name}"
            copy_path_if_exists(source_video, destination_video)
            new_metadata["video_path"] = str(destination_video)
        else:
            new_metadata["video_path"] = ""
    else:
        new_metadata["video_path"] = ""

    if keep_transcription:
        copy_path_if_exists(get_transcript_txt_path(source_project_id), get_transcript_txt_path(new_project_id))
        copy_path_if_exists(get_transcript_srt_path(source_project_id), get_transcript_srt_path(new_project_id))
        copy_path_if_exists(get_revised_transcript_txt_path(source_project_id), get_revised_transcript_txt_path(new_project_id))
        copy_path_if_exists(get_revised_transcript_srt_path(source_project_id), get_revised_transcript_srt_path(new_project_id))
        copy_path_if_exists(get_transcript_revision_error_path(source_project_id), get_transcript_revision_error_path(new_project_id))
    else:
        new_metadata["transcription_generated"] = "no"
        new_metadata["transcription_revision_status"] = ""
        new_metadata["transcription_revision_applied"] = "no"
        new_metadata["transcription_revision_manual_reviewed"] = "no"
        new_metadata["transcription_revision_glossary_count"] = "0"

    if keep_speakers:
        copy_path_if_exists(get_speaker_transcript_path(source_project_id), get_speaker_transcript_path(new_project_id))
        copy_path_if_exists(get_speaker_settings_path(source_project_id), get_speaker_settings_path(new_project_id))
        copy_path_if_exists(get_speaker_samples_dir(source_project_id), get_speaker_samples_dir(new_project_id))
    else:
        new_metadata["speakers_identified"] = "no"
        new_metadata["speakers_reviewed"] = "no"

    if keep_ai_suggestions:
        copy_path_if_exists(get_ai_suggestions_path(source_project_id), get_ai_suggestions_path(new_project_id))
        copy_path_if_exists(get_ai_request_path(source_project_id), get_ai_request_path(new_project_id))
        copy_path_if_exists(get_selected_ai_cuts_path(source_project_id), get_selected_ai_cuts_path(new_project_id))

    if keep_cuts:
        copy_path_if_exists(get_cuts_registry_path(source_project_id), get_cuts_registry_path(new_project_id))
        copy_path_if_exists(get_cuts_txt_path(source_project_id), get_cuts_txt_path(new_project_id))
        copy_path_if_exists(get_split_manual_segments_path(source_project_id), get_split_manual_segments_path(new_project_id))
    else:
        new_metadata["cuts_defined"] = "no"

    if keep_processed_files:
        copy_path_if_exists(get_project_subdir(source_project_id, "Videos Finalizados"), get_project_subdir(new_project_id, "Videos Finalizados"))

    if keep_transcription and get_transcript_txt_path(new_project_id).exists():
        new_metadata["transcription_generated"] = "yes"
    if keep_speakers and get_speaker_transcript_path(new_project_id).exists():
        new_metadata["speakers_identified"] = "yes"
    if keep_cuts and get_cuts_registry_path(new_project_id).exists():
        new_metadata["cuts_defined"] = "yes"

    save_metadata(new_project_id, new_metadata)
    for job_type in JOB_TYPES:
        save_job_status(new_project_path, job_type, build_idle_status(job_type))
    append_project_log(new_project_id, "app", "duplicado", f"Criado a partir de {source_project_id}.")
    return new_project_id


@app.route("/")
def home():
    projects = []
    storage_status = get_storage_status()
    config = load_app_config()
    show_onboarding = not bool(config.get("onboarding", {}).get("home_tour_seen"))
    storage_root = get_storage_root()
    if storage_root.exists():
        for project_folder in sorted(storage_root.iterdir(), reverse=True):
            if project_folder.is_dir():
                metadata = load_metadata(project_folder.name)
                if project_is_removed_from_app(metadata):
                    continue
                archived = project_is_archived(metadata)
                status_group = get_home_project_status_group(project_folder.name, metadata)
                projects.append(
                    {
                        "id": project_folder.name,
                        "project_number": project_folder.name.split("_", 1)[0] if "_" in project_folder.name else "",
                        "project_title": metadata.get("project_title", ""),
                        "video_name": metadata.get("video_name", ""),
                        "status": metadata.get("status", "Novo"),
                        "stage_label": get_home_project_stage_label(project_folder.name, metadata),
                        "task_type": metadata.get("task_type", "ai_cuts"),
                        "task_label": "Video Splitter" if metadata.get("task_type") == "video_splitter" else "Cortes inteligentes com I.A",
                        "archived": archived,
                        "status_group": status_group,
                    }
                )
    return render_template(
        "index.html",
        projects=projects,
        storage_status=storage_status,
        show_onboarding=show_onboarding,
        ai_integrations_alert=get_ai_integrations_alert(),
    )


@app.route("/dismiss_onboarding", methods=["POST"])
def dismiss_onboarding():
    allowed_keys = {
        "home_tour_seen",
        "settings_tour_seen",
        "ai_cuts_tour_seen",
        "video_splitter_tour_seen",
    }
    tour_key = request.form.get("tour_key", "home_tour_seen")
    if tour_key not in allowed_keys:
        tour_key = "home_tour_seen"
    save_app_config({"onboarding": {"home_tips_seen": True, tour_key: True}})
    if is_ajax_request():
        return jsonify({"ok": True})
    return redirect(url_for("home"))


@app.route("/brand/logo")
def brand_logo():
    logo_path = BASE_DIR / "Logo APP de Video.png"
    if not logo_path.exists():
        abort(404)
    return send_file(logo_path, mimetype="image/png", conditional=True)


@app.route("/setup_storage", methods=["POST"])
def setup_storage():
    folder_name = request.form.get("storage_folder_name", DEFAULT_STORAGE_FOLDER_NAME)
    ensure_storage_root(folder_name)
    return redirect(url_for("home"))


@app.route("/reset_storage_config", methods=["POST"])
def reset_storage_config():
    save_app_config({"storage_folder_name": DEFAULT_STORAGE_FOLDER_NAME})
    return redirect(url_for("home"))


@app.route("/settings")
def settings_page():
    config = load_app_config()
    return render_template(
        "settings.html",
        api_settings=get_public_api_settings(),
        performance_settings=get_public_performance_settings(),
        glossary_settings=get_public_glossary_settings(),
        openai_model_options=OPENAI_MODEL_OPTIONS,
        pyannote_model_options=PYANNOTE_MODEL_OPTIONS,
        show_settings_onboarding=not bool(config.get("onboarding", {}).get("settings_tour_seen")),
    )


@app.route("/save_api_settings", methods=["POST"])
def save_api_settings():
    current = get_api_settings()

    openai_api_key = request.form.get("openai_api_key", "").strip()
    openai_model = request.form.get("openai_model", "").strip()
    huggingface_token = request.form.get("huggingface_token", "").strip()
    pyannote_model = request.form.get("pyannote_model", "").strip()

    allowed_openai_models = {item["value"] for item in OPENAI_MODEL_OPTIONS}
    allowed_pyannote_models = {item["value"] for item in PYANNOTE_MODEL_OPTIONS}

    if openai_model not in allowed_openai_models:
        openai_model = ENV_OPENAI_MODEL if ENV_OPENAI_MODEL in allowed_openai_models else OPENAI_MODEL_OPTIONS[0]["value"]

    if pyannote_model not in allowed_pyannote_models:
        pyannote_model = ENV_PYANNOTE_MODEL if ENV_PYANNOTE_MODEL in allowed_pyannote_models else PYANNOTE_MODEL_OPTIONS[0]["value"]

    updated = {
        "openai_api_key": "" if request.form.get("clear_openai_api_key") == "1" else (openai_api_key or current.get("openai_api_key", "")),
        "openai_model": openai_model,
        "huggingface_token": "" if request.form.get("clear_huggingface_token") == "1" else (huggingface_token or current.get("huggingface_token", "")),
        "pyannote_model": pyannote_model,
    }

    health_update = {}
    if request.form.get("clear_openai_api_key") == "1" or openai_api_key:
        health_update["openai_ok"] = False
        health_update["openai_checked_at"] = ""
    if request.form.get("clear_huggingface_token") == "1" or huggingface_token:
        health_update["huggingface_ok"] = False
        health_update["huggingface_checked_at"] = ""

    save_app_config({"api_settings": updated})
    if health_update:
        save_app_config({"api_health": health_update})
    return redirect(url_for("settings_page", saved="1"))


@app.route("/save_performance_settings", methods=["POST"])
def save_performance_settings():
    performance_mode = request.form.get("performance_mode", DEFAULT_PERFORMANCE_MODE).strip()
    allowed_performance_modes = {item["value"] for item in PERFORMANCE_MODE_OPTIONS}
    if performance_mode not in allowed_performance_modes:
        performance_mode = DEFAULT_PERFORMANCE_MODE

    save_app_config({"performance_mode": performance_mode})
    return redirect(url_for("settings_page", saved="1"))


@app.route("/save_glossary_settings", methods=["POST"])
def save_glossary_settings():
    terms = request.form.getlist("glossary_term[]")
    variants = request.form.getlist("glossary_variants[]")
    contexts = request.form.getlist("glossary_context[]")

    entries = []
    total = max(len(terms), len(variants), len(contexts))
    for index in range(total):
        entries.append(
            {
                "term": terms[index] if index < len(terms) else "",
                "variants": variants[index] if index < len(variants) else "",
                "context": contexts[index] if index < len(contexts) else "",
            }
        )

    save_app_config({"glossary": normalize_glossary_entries(entries)})
    return redirect(url_for("settings_page", saved="1"))


@app.route("/add_glossary_term", methods=["POST"])
def add_glossary_term():
    term = request.form.get("term", "").strip()
    variants = request.form.get("variants", "").strip()
    context = request.form.get("context", "").strip()

    if not term:
        if is_ajax_request():
            return jsonify({"ok": False, "message": "Informe o termo correto."}), 400
        return redirect(url_for("settings_page", api_error="Informe o termo correto."))

    entries = get_glossary_entries()
    existing = next((item for item in entries if item.get("term", "").casefold() == term.casefold()), None)
    if existing:
        existing_variants = [part.strip() for part in existing.get("variants", "").split(",") if part.strip()]
        for part in [item.strip() for item in variants.split(",") if item.strip()]:
            if part.casefold() not in {value.casefold() for value in existing_variants}:
                existing_variants.append(part)
        existing["variants"] = ", ".join(existing_variants)
        if context and not existing.get("context"):
            existing["context"] = context
    else:
        entries.append({"term": term, "variants": variants, "context": context})

    entries = normalize_glossary_entries(entries)
    save_app_config({"glossary": entries})

    if is_ajax_request():
        return jsonify({"ok": True, "count": len(entries)})
    return redirect(url_for("settings_page", saved="1"))


@app.route("/test_api_settings/<provider>", methods=["POST"])
def test_api_settings(provider):
    try:
        if provider == "openai":
            openai_client = get_openai_client()
            if not openai_client:
                raise RuntimeError("OpenAI API Key não configurada.")
            openai_client.models.list()
            message = "OpenAI conectada com sucesso."
            save_app_config({"api_health": {"openai_ok": True, "openai_checked_at": datetime.now().isoformat(timespec="seconds")}})

        elif provider == "huggingface":
            settings = get_api_settings()
            token = settings.get("huggingface_token", "")
            model = settings.get("pyannote_model", "")
            if not token:
                raise RuntimeError("Hugging Face Token não configurado.")
            if not model:
                raise RuntimeError("Modelo pyannote não configurado.")

            try:
                from huggingface_hub import HfApi
            except ImportError as exc:
                raise RuntimeError("huggingface_hub não está instalado.") from exc

            HfApi().model_info(model, token=token)
            message = "Hugging Face conectada com sucesso."
            save_app_config({"api_health": {"huggingface_ok": True, "huggingface_checked_at": datetime.now().isoformat(timespec="seconds")}})

        else:
            raise ValueError("Provedor inválido.")

        if is_ajax_request():
            return jsonify({"ok": True, "message": message})
        return redirect(url_for("settings_page", api_test=message))

    except Exception as e:
        message = str(e)
        if provider == "openai":
            save_app_config({"api_health": {"openai_ok": False, "openai_checked_at": ""}})
        elif provider == "huggingface":
            save_app_config({"api_health": {"huggingface_ok": False, "huggingface_checked_at": ""}})
        if is_ajax_request():
            return jsonify({"ok": False, "message": message}), 400
        return redirect(url_for("settings_page", api_error=message))


@app.route("/open_project_folder/<project_id>", methods=["POST"])
def open_project_folder(project_id):
    project_path = get_project_path(project_id)
    if project_path.exists():
        subprocess.run(["open", str(project_path)], check=False)
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/open_project_folder_from_home/<project_id>", methods=["POST"])
def open_project_folder_from_home(project_id):
    project_path = get_project_path(project_id)
    if project_path.exists():
        subprocess.run(["open", str(project_path)], check=False)
    return redirect(url_for("home"))


@app.route("/archive_project/<project_id>", methods=["POST"])
def archive_project(project_id):
    project_path = get_project_path(project_id)
    metadata = load_metadata(project_id)
    if not project_path.exists() or project_is_removed_from_app(metadata):
        abort(404)
    metadata["archived"] = "yes"
    metadata["archived_at"] = datetime.now().isoformat(timespec="seconds")
    save_metadata(project_id, metadata)
    return redirect(url_for("home"))


@app.route("/restore_project/<project_id>", methods=["POST"])
def restore_project(project_id):
    project_path = get_project_path(project_id)
    metadata = load_metadata(project_id)
    if not project_path.exists() or project_is_removed_from_app(metadata):
        abort(404)
    metadata["archived"] = "no"
    metadata["archived_at"] = ""
    save_metadata(project_id, metadata)
    return redirect(url_for("home"))


@app.route("/duplicate_project/<project_id>", methods=["POST"])
def duplicate_project(project_id):
    try:
        new_project_id = duplicate_project_with_options(
            project_id,
            {
                "project_title": request.form.get("duplicate_project_title", ""),
                "keep_video": request.form.get("keep_video", "no"),
                "keep_transcription": request.form.get("keep_transcription", "no"),
                "keep_speakers": request.form.get("keep_speakers", "no"),
                "keep_ai_suggestions": request.form.get("keep_ai_suggestions", "no"),
                "keep_cuts": request.form.get("keep_cuts", "no"),
                "keep_processed_files": request.form.get("keep_processed_files", "no"),
            },
        )
        if is_ajax_request():
            return jsonify({"ok": True, "project_id": new_project_id})
        return redirect(url_for("home"))
    except Exception as e:
        if is_ajax_request():
            return jsonify({"ok": False, "message": str(e)}), 400
        return redirect(url_for("home"))


@app.route("/project_video/<project_id>")
def project_video(project_id):
    metadata = load_metadata(project_id)
    project_path = get_project_path(project_id)
    video_path_value = metadata.get("video_path", "")
    if not video_path_value:
        abort(404)
    video_path = Path(video_path_value)

    if not project_path.exists() or project_is_removed_from_app(metadata) or not video_path.exists():
        abort(404)

    try:
        resolved_project_path = project_path.resolve()
        resolved_video_path = video_path.resolve()
    except FileNotFoundError:
        abort(404)

    if resolved_project_path != resolved_video_path and resolved_project_path not in resolved_video_path.parents:
        abort(404)

    return send_file(resolved_video_path, conditional=True)


@app.route("/open_processed_file/<project_id>/<path:filename>", methods=["POST"])
def open_processed_file(project_id, filename):
    output_path = get_processed_file_path(project_id, filename)
    if not output_path:
        if is_ajax_request():
            return jsonify({"ok": False, "message": "Arquivo processado não encontrado."}), 404
        abort(404)

    subprocess.run(["open", str(output_path)], check=False)

    if is_ajax_request():
        return jsonify({"ok": True})
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/delete_processed_file/<project_id>/<path:filename>", methods=["POST"])
def delete_processed_file(project_id, filename):
    output_path = get_processed_file_path(project_id, filename)
    if not output_path:
        if is_ajax_request():
            return jsonify({"ok": False, "message": "Arquivo processado não encontrado."}), 404
        abort(404)

    output_path.unlink()

    cuts = load_cuts_registry(project_id)
    for cut in cuts:
        if cut.get("output_name", "").strip() == filename:
            cut["status"] = "pending"
            cut["output_name"] = ""
    save_cuts_registry(project_id, cuts)
    refresh_cuts_defined_flag(project_id)

    metadata = load_metadata(project_id)
    metadata["status"] = "Arquivo processado excluído"
    save_metadata(project_id, metadata)

    if is_ajax_request():
        return jsonify({"ok": True, "remaining": get_processed_files(project_id)})
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/delete_project/<project_id>", methods=["POST"])
def delete_project(project_id):
    project_path = get_project_path(project_id)
    storage_root = get_storage_root().resolve()
    delete_mode = request.form.get("delete_mode", "app_only")

    try:
        resolved_project_path = project_path.resolve()
    except FileNotFoundError:
        return redirect(url_for("home"))

    if storage_root not in resolved_project_path.parents:
        abort(400)

    if delete_mode == "with_files":
        if resolved_project_path.exists():
            shutil.rmtree(resolved_project_path)
        return redirect(url_for("home"))

    if storage_root in resolved_project_path.parents and resolved_project_path.exists():
        metadata = load_metadata(project_id)
        metadata["removed_from_app"] = "yes"
        metadata["removed_at"] = datetime.now().isoformat(timespec="seconds")
        save_metadata(project_id, metadata)

    return redirect(url_for("home"))


@app.route("/upload", methods=["POST"])
def upload_video():
    if "video_file" not in request.files:
        return redirect(url_for("home"))

    file = request.files["video_file"]
    if file.filename == "":
        return redirect(url_for("home"))

    ensure_storage_root()
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    video_name = file.filename
    default_project_title = Path(video_name).stem or f"Projeto {timestamp}"
    project_title = request.form.get("project_title", "").strip() or default_project_title
    task_type = request.form.get("task_type", "ai_cuts")
    if task_type not in ("ai_cuts", "video_splitter"):
        task_type = "ai_cuts"
    project_slug = sanitize_filename(project_title).lower().replace(" ", "_")
    project_id = build_unique_project_id(project_slug)
    project_path = ensure_project_structure(project_id)

    bruto_path = get_project_subdir(project_id, "Arquivo Video Bruto") / f"{project_id}_{video_name}"
    file.save(bruto_path)

    duration = get_video_duration(bruto_path)
    save_metadata(
        project_id,
        {
            "project_id": project_id,
            "project_title": project_title,
            "video_name": video_name,
            "video_path": str(bruto_path),
            "duration": str(duration) if duration else "",
            "status": "Vídeo carregado",
            "task_type": task_type,
            "transcription_generated": "no",
            "speakers_identified": "no",
            "cuts_defined": "no",
        },
    )

    for job_type in JOB_TYPES:
        save_job_status(project_path, job_type, build_idle_status(job_type))

    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/project/<project_id>")
def project_detail(project_id):
    metadata = load_metadata(project_id)
    project_path = get_project_path(project_id)
    if not project_path.exists() or project_is_removed_from_app(metadata):
        abort(404)

    original_transcript_text = ""
    original_transcript_srt_text = ""
    transcript_text = ""
    transcript_srt_text = ""

    original_transcript_txt_path = get_transcript_txt_path(project_id)
    original_transcript_srt_path = get_transcript_srt_path(project_id)
    transcript_txt_path = get_preferred_transcript_txt_path(project_id)
    transcript_srt_path = get_preferred_transcript_srt_path(project_id)
    revised_transcript_exists = get_revised_transcript_txt_path(project_id).exists()

    if original_transcript_txt_path.exists():
        original_transcript_text = original_transcript_txt_path.read_text(encoding="utf-8")
    if original_transcript_srt_path.exists():
        original_transcript_srt_text = original_transcript_srt_path.read_text(encoding="utf-8")
    if transcript_txt_path.exists():
        transcript_text = transcript_txt_path.read_text(encoding="utf-8")
    if transcript_srt_path.exists():
        transcript_srt_text = transcript_srt_path.read_text(encoding="utf-8")

    cuts = load_cuts(project_id)
    ai_suggestions = load_ai_suggestions(project_id)
    selected_ai_cuts = load_selected_ai_cuts(project_id)
    speaker_transcript = load_speaker_transcript(project_id)
    speaker_settings = load_speaker_settings(project_id)
    ai_request = load_ai_request(project_id)
    next_cut_number = get_next_cut_number(project_id)
    processed_files = get_processed_files(project_id)
    split_manual_segments = load_split_manual_segments(project_id)
    job_statuses = {job_type: load_job_status(get_project_path(project_id), job_type) for job_type in JOB_TYPES}
    step_elapsed = get_project_step_elapsed(job_statuses)
    total_processing_time = get_total_processing_time(job_statuses)
    technical_log_text = read_project_technical_log(project_id)
    onboarding = load_app_config().get("onboarding", {})
    task_type = metadata.get("task_type", "ai_cuts")

    return render_template(
        "cuts.html",
        project_id=project_id,
        metadata=metadata,
        original_transcript_text=original_transcript_text,
        original_transcript_srt_text=original_transcript_srt_text,
        transcript_text=transcript_text,
        transcript_srt_text=transcript_srt_text,
        transcription_revision_model_label=metadata.get("transcription_revision_model_label") or get_openai_model_display_label(metadata.get("transcription_revision_model")),
        transcription_review_blocks=build_transcription_review_blocks(project_id),
        cuts=cuts,
        processed_files=processed_files,
        ai_suggestions=ai_suggestions,
        selected_ai_cuts=selected_ai_cuts,
        speaker_transcript=speaker_transcript,
        speaker_settings=speaker_settings,
        ai_request=ai_request,
        next_cut_number=next_cut_number,
        job_statuses=job_statuses,
        split_manual_segments=split_manual_segments,
        step_elapsed=step_elapsed,
        total_processing_time=total_processing_time,
        revised_transcript_exists=revised_transcript_exists,
        technical_log_text=technical_log_text,
        project_stage_label=get_home_project_stage_label(project_id, metadata),
        show_ai_cuts_onboarding=task_type != "video_splitter" and not bool(onboarding.get("ai_cuts_tour_seen")),
        show_video_splitter_onboarding=task_type == "video_splitter" and not bool(onboarding.get("video_splitter_tour_seen")),
    )


@app.route("/save_transcription_revision/<project_id>", methods=["POST"])
def save_transcription_revision(project_id):
    metadata = load_metadata(project_id)
    project_path = get_project_path(project_id)
    if not project_path.exists() or project_is_removed_from_app(metadata):
        abort(404)

    original_srt_path = get_transcript_srt_path(project_id)
    if not original_srt_path.exists():
        abort(400)

    original_blocks = parse_srt_blocks_for_revision(original_srt_path.read_text(encoding="utf-8"))
    revision_uids = request.form.getlist("revision_uid[]")
    revision_texts = request.form.getlist("revision_text[]")
    text_by_uid = {}

    for uid_value, text_value in zip(revision_uids, revision_texts):
        try:
            uid = int(uid_value)
        except (TypeError, ValueError):
            continue
        text_by_uid[uid] = str(text_value or "").strip()

    for block in original_blocks:
        revised_text = text_by_uid.get(block["uid"], block["text"])
        block["revised_text"] = revised_text or block["text"]

    get_revised_transcript_srt_path(project_id).write_text(build_srt_from_revision_blocks(original_blocks), encoding="utf-8")
    get_revised_transcript_txt_path(project_id).write_text(build_txt_from_revision_blocks(original_blocks), encoding="utf-8")

    metadata["transcription_revision_status"] = "manual_reviewed"
    metadata["transcription_revision_manual_reviewed"] = "yes"
    metadata["status"] = "Transcrição revisada manualmente"
    save_metadata(project_id, metadata)

    scroll_y = request.form.get("scroll_y", "")
    if scroll_y:
        return redirect(url_for("project_detail", project_id=project_id, scroll_y=scroll_y))
    return redirect(url_for("project_detail", project_id=project_id))



@app.route("/split_video/<project_id>", methods=["POST"])
def split_video(project_id):
    try:
        metadata = load_metadata(project_id)
        mode = request.form.get("split_mode", "fixed_duration")

        if mode == "fixed_duration":
            duration_value = request.form.get("split_duration_seconds", "60")
            if duration_value == "custom":
                duration_value = request.form.get("custom_duration_seconds", "")
            duration_seconds = int(float((duration_value or "").replace(",", ".")))
            if duration_seconds <= 0:
                raise ValueError("Informe uma duração válida.")

            metadata["split_mode"] = "fixed_duration"
            metadata["split_duration_seconds"] = str(duration_seconds)
            metadata["split_parts"] = ""
            metadata["split_social_platform"] = ""
            metadata["split_social_format"] = ""

        elif mode == "by_parts":
            split_parts = int(request.form.get("split_parts", "0"))
            if split_parts < 1 or split_parts > 10:
                raise ValueError("Informe um número de partes entre 1 e 10.")

            metadata["split_mode"] = "by_parts"
            metadata["split_parts"] = str(split_parts)
            metadata["split_duration_seconds"] = ""
            metadata["split_social_platform"] = ""
            metadata["split_social_format"] = ""

        elif mode == "social_preset":
            platform = request.form.get("split_social_platform", "instagram")
            social_format = request.form.get("split_social_format", "reels")
            duration_seconds = int(request.form.get("split_social_duration_seconds", "0"))

            platform_data = SPLIT_SOCIAL_PRESETS.get(platform)
            if not platform_data:
                raise ValueError("Rede social inválida.")

            format_data = platform_data["formats"].get(social_format)
            if not format_data:
                raise ValueError("Formato de rede social inválido.")

            if duration_seconds not in format_data["durations"]:
                raise ValueError("Duração inválida para este formato.")

            metadata["split_mode"] = "social_preset"
            metadata["split_duration_seconds"] = str(duration_seconds)
            metadata["split_parts"] = ""
            metadata["split_social_platform"] = platform
            metadata["split_social_format"] = social_format

        elif mode == "manual_segments":
            starts = request.form.getlist("split_manual_start[]")
            ends = request.form.getlist("split_manual_end[]")
            names = request.form.getlist("split_manual_name[]")
            manual_segments = []

            for index, (start_time, end_time, name) in enumerate(zip(starts, ends, names), start=1):
                start_time = start_time.strip()
                end_time = end_time.strip()
                name = name.strip()

                if not start_time and not end_time and not name:
                    continue
                if not start_time or not end_time:
                    raise ValueError(f"Preencha início e fim do trecho {index}.")

                start_seconds = parse_time_to_seconds(start_time)
                end_seconds = parse_time_to_seconds(end_time)
                if end_seconds <= start_seconds:
                    raise ValueError(f"O fim do trecho {index} precisa ser maior que o início.")

                manual_segments.append(
                    {
                        "start": start_time,
                        "end": end_time,
                        "name": sanitize_filename(name or f"Trecho {len(manual_segments) + 1:02d}"),
                    }
                )

            if not manual_segments:
                raise ValueError("Adicione pelo menos um trecho manual.")

            save_split_manual_segments(project_id, manual_segments)
            metadata["split_mode"] = "manual_segments"
            metadata["split_duration_seconds"] = ""
            metadata["split_parts"] = ""
            metadata["split_social_platform"] = ""
            metadata["split_social_format"] = ""

        else:
            raise ValueError("Modo de divisão inválido.")

        metadata["task_type"] = "video_splitter"
        metadata["status"] = "Video Splitter iniciado"
        save_metadata(project_id, metadata)

    except Exception as e:
        if is_ajax_request():
            return jsonify({"ok": False, "message": str(e)}), 400
        metadata = load_metadata(project_id)
        metadata["status"] = str(e)
        save_metadata(project_id, metadata)
        return redirect(url_for("project_detail", project_id=project_id))

    started = start_background_job(project_id, "split_video", run_split_video_job)

    if is_ajax_request():
        if not started:
            return jsonify({"ok": False, "message": "Já existe uma divisão em andamento."}), 409
        return jsonify({"ok": True, "job_type": "split_video"})

    scroll_y = request.form.get("scroll_y", "")
    if scroll_y:
        return redirect(url_for("project_detail", project_id=project_id, scroll_y=scroll_y))
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/job_status/<project_id>/<job_type>")
def job_status(project_id, job_type):
    if job_type not in JOB_TYPES:
        return jsonify({"error": build_error_title(job_type)}), 404
    status = load_job_status(get_project_path(project_id), job_type)
    return jsonify(annotate_job_status(project_id, job_type, status))


@app.route("/cancel_job/<project_id>/<job_type>", methods=["POST"])
def cancel_job(project_id, job_type):
    if job_type not in JOB_TYPES:
        return jsonify({"ok": False, "message": build_error_title(job_type)}), 404

    current = load_job_status(get_project_path(project_id), job_type)
    if current.get("state") != "running":
        return jsonify({"ok": True, "message": "Nenhum processamento ativo."})

    request_job_cancel(project_id, job_type)
    append_project_log(project_id, job_type, "cancelado", "Cancelamento solicitado pelo usuário.")
    update_job(
        project_id,
        job_type,
        build_error_status(
            job_type,
            "Processamento cancelado",
            "O job foi interrompido antes da conclusão. Execute esta etapa novamente quando quiser continuar.",
        ),
    )
    metadata = load_metadata(project_id)
    metadata["status"] = "Processamento cancelado"
    save_metadata(project_id, metadata)
    return jsonify({"ok": True})


@app.route("/reset_project/<project_id>", methods=["POST"])
def reset_project(project_id):
    metadata = load_metadata(project_id)
    project_path = get_project_path(project_id)

    paths = [
        get_transcript_txt_path(project_id),
        get_transcript_srt_path(project_id),
        get_revised_transcript_txt_path(project_id),
        get_revised_transcript_srt_path(project_id),
        get_transcript_revision_error_path(project_id),
        get_cuts_txt_path(project_id),
        get_cuts_registry_path(project_id),
        get_ai_suggestions_path(project_id),
        get_ai_request_path(project_id),
        get_selected_ai_cuts_path(project_id),
        get_speaker_transcript_path(project_id),
        get_speaker_settings_path(project_id),
        get_project_subdir(project_id, "Audios") / f"{project_id}.wav",
        get_project_subdir(project_id, "Dados de Processamento") / "selected_ai_cut.json",
        get_split_manual_segments_path(project_id),
    ]

    for path in paths:
        if path.exists():
            path.unlink()

    samples_dir = get_speaker_samples_dir(project_id)
    if samples_dir.exists():
        shutil.rmtree(samples_dir, ignore_errors=True)

    transcription_samples_dir = get_transcription_samples_dir(project_id)
    if transcription_samples_dir.exists():
        shutil.rmtree(transcription_samples_dir, ignore_errors=True)

    for output_name in get_processed_files(project_id):
        output_path = get_project_subdir(project_id, "Videos Finalizados") / output_name
        if output_path.exists():
            output_path.unlink()

    metadata["status"] = "Vídeo carregado"
    metadata["transcription_generated"] = "no"
    metadata["transcription_revision_status"] = ""
    metadata["transcription_revision_applied"] = "no"
    metadata["transcription_revision_manual_reviewed"] = "no"
    metadata["transcription_revision_glossary_count"] = "0"
    metadata["speakers_identified"] = "no"
    metadata["cuts_defined"] = "no"
    save_metadata(project_id, metadata)

    for job_type in JOB_TYPES:
        save_job_status(project_path, job_type, build_idle_status(job_type))

    scroll_y = request.form.get("scroll_y", "")
    if scroll_y:
        return redirect(url_for("project_detail", project_id=project_id, scroll_y=scroll_y))
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/generate_transcription/<project_id>", methods=["POST"])
def generate_transcription(project_id):
    started = start_background_job(project_id, "generate_transcription", run_generate_transcription_job)
    if is_ajax_request():
        if not started:
            return jsonify({"ok": False, "message": "Já existe uma transcrição em andamento."}), 409
        return jsonify({"ok": True, "job_type": "generate_transcription"})
    scroll_y = request.form.get("scroll_y", "")
    if scroll_y:
        return redirect(url_for("project_detail", project_id=project_id, scroll_y=scroll_y))
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/transcription_segment_sample/<project_id>/<int:uid>")
def transcription_segment_sample(project_id, uid):
    original_srt_path = get_transcript_srt_path(project_id)
    if not original_srt_path.exists():
        abort(404)

    block = next((item for item in parse_srt_blocks_for_revision(original_srt_path.read_text(encoding="utf-8")) if item["uid"] == uid), None)
    if not block:
        abort(404)

    match = SRT_TIME_RANGE_PATTERN.search(block.get("time_line", ""))
    if not match:
        abort(404)

    start_seconds = max(0.0, parse_srt_time_to_seconds(match.group("start")) - 0.08)
    end_seconds = parse_srt_time_to_seconds(match.group("end")) + 0.08
    duration_seconds = max(0.4, end_seconds - start_seconds)

    wav_path = get_project_subdir(project_id, "Audios") / f"{project_id}.wav"
    metadata = load_metadata(project_id)
    video_path = Path(metadata.get("video_path", ""))
    source_path = wav_path if wav_path.exists() else video_path
    if not source_path.exists():
        abort(404)

    sample_path = get_transcription_sample_path(project_id, uid)
    if not sample_path.exists():
        run_processing_command(
            ffmpeg_command(
                "-y",
                "-ss",
                f"{start_seconds:.3f}",
                "-i",
                str(source_path),
                "-t",
                f"{duration_seconds:.3f}",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                *ffmpeg_thread_args(),
                str(sample_path),
            ),
            check=True,
            capture_output=True,
            text=True,
        )

    return send_file(sample_path, mimetype="audio/wav", conditional=True)


@app.route("/speaker_sample/<project_id>/<speaker_id>")
def speaker_sample(project_id, speaker_id):
    speaker_transcript = load_speaker_transcript(project_id)
    segments = [
        segment
        for segment in speaker_transcript.get("segments", [])
        if segment.get("speaker") == speaker_id
    ]

    if not segments:
        abort(404)

    mark_speakers_reviewed(project_id)

    wav_path = get_project_subdir(project_id, "Audios") / f"{project_id}.wav"
    if not wav_path.exists():
        abort(404)

    sample_path = get_speaker_sample_path(project_id, speaker_id)
    if not sample_path.exists():
        def segment_duration(segment):
            try:
                return parse_timestamp_to_float_seconds(segment.get("end", "")) - parse_timestamp_to_float_seconds(segment.get("start", ""))
            except Exception:
                return 0

        selected = max(segments, key=segment_duration)
        start_seconds = max(0, parse_timestamp_to_float_seconds(selected.get("start", "")) - 0.2)
        end_seconds = max(start_seconds + 0.5, parse_timestamp_to_float_seconds(selected.get("end", "")) + 0.2)
        duration_seconds = min(12.0, max(0.5, end_seconds - start_seconds))

        run_processing_command(
            ffmpeg_command(
                "-y",
                "-ss",
                f"{start_seconds:.3f}",
                "-i",
                str(wav_path),
                "-t",
                f"{duration_seconds:.3f}",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                *ffmpeg_thread_args(),
                str(sample_path),
            ),
            check=True,
            capture_output=True,
            text=True,
        )

    return send_file(sample_path, mimetype="audio/wav", conditional=True)


@app.route("/ai_suggestion_sample/<project_id>/<int:suggestion_index>/<segment_type>")
def ai_suggestion_sample(project_id, suggestion_index, segment_type):
    suggestions = load_ai_suggestions(project_id)
    cuts_list = suggestions.get("cuts", [])
    if suggestion_index < 0 or suggestion_index >= len(cuts_list):
        abort(404)

    item = cuts_list[suggestion_index]
    if segment_type == "content":
        start_time = item.get("content_start", "")
        end_time = item.get("content_end", "")
    elif segment_type == "hook":
        start_time = item.get("hook_start", "")
        end_time = item.get("hook_end", "")
    elif segment_type.startswith("hook_option_"):
        try:
            hook_option_index = int(segment_type.replace("hook_option_", "", 1))
        except ValueError:
            abort(404)
        hook_options = item.get("hook_options", [])
        if not isinstance(hook_options, list) or hook_option_index < 0 or hook_option_index >= len(hook_options):
            abort(404)
        selected_hook = hook_options[hook_option_index]
        if not isinstance(selected_hook, dict):
            abort(404)
        start_time = selected_hook.get("hook_start", "")
        end_time = selected_hook.get("hook_end", "")
    else:
        abort(404)

    try:
        start_seconds = parse_time_to_seconds(start_time)
        end_seconds = parse_time_to_seconds(end_time)
    except Exception:
        abort(404)

    duration_seconds = end_seconds - start_seconds
    if duration_seconds <= 0:
        abort(404)

    metadata = load_metadata(project_id)
    video_path = Path(metadata.get("video_path", ""))
    if not video_path.exists():
        abort(404)

    sample_path = get_ai_suggestion_sample_path(project_id, suggestion_index, segment_type, start_time, end_time)
    if not sample_path.exists():
        run_processing_command(
            ffmpeg_command(
                "-y",
                "-ss",
                str(start_seconds),
                "-i",
                str(video_path),
                "-t",
                str(duration_seconds),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                *ffmpeg_thread_args(),
                str(sample_path),
            ),
            check=True,
            capture_output=True,
            text=True,
        )

    return send_file(sample_path, mimetype="audio/wav", conditional=True)


@app.route("/identify_speakers/<project_id>", methods=["POST"])
def identify_speakers(project_id):
    try:
        num_speakers = request.form.get("num_speakers", "").strip()
        min_speakers = request.form.get("min_speakers", "").strip()
        max_speakers = request.form.get("max_speakers", "").strip()

        # Se o usuário informar número exato, ele prevalece sobre mínimo/máximo.
        if num_speakers:
            parse_optional_positive_int(num_speakers)
            min_speakers = ""
            max_speakers = ""
        else:
            parse_optional_positive_int(min_speakers)
            parse_optional_positive_int(max_speakers)
            if min_speakers and max_speakers and int(min_speakers) > int(max_speakers):
                raise ValueError("O mínimo de speakers não pode ser maior que o máximo.")

        save_speaker_settings_data(
            project_id,
            {
                "num_speakers": num_speakers,
                "min_speakers": min_speakers,
                "max_speakers": max_speakers,
            },
        )
    except Exception as e:
        if is_ajax_request():
            return jsonify({"ok": False, "message": str(e)}), 400
        metadata = load_metadata(project_id)
        metadata["status"] = str(e)
        save_metadata(project_id, metadata)
        return redirect(url_for("project_detail", project_id=project_id))

    started = start_background_job(project_id, "identify_speakers", run_identify_speakers_job)
    if is_ajax_request():
        if not started:
            return jsonify({"ok": False, "message": "Já existe um mapeamento de participantes em andamento."}), 409
        return jsonify({"ok": True, "job_type": "identify_speakers"})
    scroll_y = request.form.get("scroll_y", "")
    if scroll_y:
        return redirect(url_for("project_detail", project_id=project_id, scroll_y=scroll_y))
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/save_speaker_settings/<project_id>", methods=["POST"])
def save_speaker_settings(project_id):
    try:
        speaker_transcript = load_speaker_transcript(project_id)
        if not speaker_transcript:
            raise ValueError("nenhuma transcrição com participantes encontrada")

        names_by_id = {}
        for speaker in speaker_transcript.get("speakers", []):
            speaker_id = speaker.get("id", "")
            if speaker_id:
                names_by_id[speaker_id] = request.form.get(f"speaker_name_{speaker_id}", "").strip()

        speaker_transcript = apply_speaker_names(speaker_transcript, names_by_id)
        save_speaker_transcript(project_id, speaker_transcript)

        metadata = load_metadata(project_id)
        metadata["status"] = "Nomes dos participantes atualizados"
        metadata["speakers_reviewed"] = "yes"
        save_metadata(project_id, metadata)

        if is_ajax_request():
            return jsonify({"ok": True})

        scroll_y = request.form.get("scroll_y", "")
        if scroll_y:
            return redirect(url_for("project_detail", project_id=project_id, scroll_y=scroll_y))
        return redirect(url_for("project_detail", project_id=project_id))
    except Exception as e:
        if is_ajax_request():
            return jsonify({"ok": False, "message": str(e)}), 400
        metadata = load_metadata(project_id)
        metadata["status"] = str(e)
        save_metadata(project_id, metadata)
        return redirect(url_for("project_detail", project_id=project_id))


@app.route("/mark_speakers_reviewed/<project_id>", methods=["POST"])
def mark_speakers_reviewed_route(project_id):
    try:
        mark_speakers_reviewed(project_id)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 400


@app.route("/suggest_cuts/<project_id>", methods=["POST"])
def suggest_cuts(project_id):
    mark_speakers_reviewed(project_id)
    cut_option_count = parse_bounded_int(
        request.form.get("cut_option_count"),
        DEFAULT_AI_CUT_OPTION_COUNT,
        MIN_AI_CUT_OPTION_COUNT,
        MAX_AI_CUT_OPTION_COUNT,
    )
    hook_option_count = parse_bounded_int(
        request.form.get("hook_option_count"),
        DEFAULT_AI_HOOK_OPTION_COUNT,
        MIN_AI_HOOK_OPTION_COUNT,
        MAX_AI_HOOK_OPTION_COUNT,
    )
    save_ai_request(
        project_id,
        {
            "selected_speakers": request.form.getlist("selected_speakers"),
            "free_prompt": request.form.get("free_prompt", "").strip(),
            "cut_option_count": cut_option_count,
            "hook_option_count": hook_option_count,
        },
    )
    started = start_background_job(project_id, "suggest_cuts", run_suggest_cuts_job)
    if is_ajax_request():
        if not started:
            return jsonify({"ok": False, "message": "Já existe uma sugestão em andamento."}), 409
        return jsonify({"ok": True, "job_type": "suggest_cuts"})
    scroll_y = request.form.get("scroll_y", "")
    if scroll_y:
        return redirect(url_for("project_detail", project_id=project_id, scroll_y=scroll_y))
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/use_ai_cuts/<project_id>", methods=["POST"])
def use_ai_cuts(project_id):
    try:
        initial_margin = parse_margin_seconds(request.form.get("initial_margin_seconds", "0"))
        final_margin = parse_margin_seconds(request.form.get("final_margin_seconds", "1"))
        selected_indexes = request.form.getlist("selected_indexes")
        select_all = request.form.get("select_all") == "1"
        replace_selected = request.form.get("replace_selected") == "1"
        selected_hook_options = {}
        for key, value in request.form.items():
            if not key.startswith("hook_option_"):
                continue
            try:
                suggestion_index = int(key.replace("hook_option_", "", 1))
                selected_hook_options[suggestion_index] = int(value)
            except (TypeError, ValueError):
                continue

        result = select_ai_cuts_internal(
            project_id,
            selected_indexes,
            initial_margin,
            final_margin,
            select_all=select_all,
            replace_selected=replace_selected,
            selected_hook_options=selected_hook_options,
        )

        metadata = load_metadata(project_id)
        metadata["status"] = f"{result['added']} sugestão(ões) adicionadas à seção 5"
        save_metadata(project_id, metadata)

        if is_ajax_request():
            return jsonify({"ok": True, "added": result["added"], "total": result["total"]})

        scroll_y = request.form.get("scroll_y", "")
        if scroll_y:
            return redirect(url_for("project_detail", project_id=project_id, scroll_y=scroll_y))
        return redirect(url_for("project_detail", project_id=project_id))
    except Exception as e:
        if is_ajax_request():
            return jsonify({"ok": False, "message": str(e)}), 400
        metadata = load_metadata(project_id)
        metadata["status"] = str(e)
        save_metadata(project_id, metadata)
        return redirect(url_for("project_detail", project_id=project_id))


@app.route("/append_selected_ai_cuts/<project_id>", methods=["POST"])
def append_selected_ai_cuts(project_id):
    try:
        batch_id, added_count = append_selected_ai_cuts_to_registry(project_id)
        if is_ajax_request():
            return jsonify({"ok": True, "batch_id": batch_id, "added_count": added_count})
        scroll_y = request.form.get("scroll_y", "")
        if scroll_y:
            return redirect(url_for("project_detail", project_id=project_id, scroll_y=scroll_y))
        return redirect(url_for("project_detail", project_id=project_id))
    except Exception as e:
        if is_ajax_request():
            return jsonify({"ok": False, "message": str(e)}), 400
        metadata = load_metadata(project_id)
        metadata["status"] = str(e)
        save_metadata(project_id, metadata)
        return redirect(url_for("project_detail", project_id=project_id))


@app.route("/save_cuts/<project_id>", methods=["POST"])
def save_cuts(project_id):
    starts = request.form.getlist("start[]")
    ends = request.form.getlist("end[]")
    names = request.form.getlist("name[]")
    cuts = load_cuts_registry(project_id)
    batch_id = datetime.now().strftime("%Y%m%d%H%M%S")
    added_count = 0

    for start, end, name in zip(starts, ends, names):
        start = start.strip()
        end = end.strip()
        name = name.strip()

        if start and end and name:
            parse_time_to_seconds(start)
            parse_time_to_seconds(end)
            if parse_time_to_seconds(end) <= parse_time_to_seconds(start):
                continue

            cuts.append(
                {
                    "start": start,
                    "end": end,
                    "name": name,
                    "batch_id": batch_id,
                    "status": "pending",
                    "output_name": "",
                }
            )
            added_count += 1

    save_cuts_registry(project_id, cuts)
    metadata = load_metadata(project_id)
    metadata["status"] = "Cortes definidos" if added_count else "Nenhum corte novo foi adicionado"
    metadata["cuts_defined"] = "yes" if cuts else "no"
    save_metadata(project_id, metadata)

    if is_ajax_request():
        return jsonify({"ok": True, "added_count": added_count, "batch_id": batch_id})

    scroll_y = request.form.get("scroll_y", "")
    if scroll_y:
        return redirect(url_for("project_detail", project_id=project_id, scroll_y=scroll_y))
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/delete_cut/<project_id>/<int:cut_index>", methods=["POST"])
def delete_cut(project_id, cut_index):
    cuts = load_cuts_registry(project_id)

    if 0 <= cut_index < len(cuts):
        removed = cuts.pop(cut_index)
        output_name = removed.get("output_name", "").strip()
        if output_name:
            output_path = get_project_subdir(project_id, "Videos Finalizados") / output_name
            if output_path.exists():
                output_path.unlink()

        save_cuts_registry(project_id, cuts)
        refresh_cuts_defined_flag(project_id)

    if is_ajax_request():
        return jsonify({"ok": True})

    scroll_y = request.form.get("scroll_y", "")
    if scroll_y:
        return redirect(url_for("project_detail", project_id=project_id, scroll_y=scroll_y))
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/clear_cuts/<project_id>", methods=["POST"])
def clear_cuts(project_id):
    cuts = load_cuts_registry(project_id)
    finalized_dir = get_project_subdir(project_id, "Videos Finalizados")

    for cut in cuts:
        output_name = cut.get("output_name", "").strip()
        if output_name:
            output_path = finalized_dir / output_name
            if output_path.exists():
                output_path.unlink()

    save_cuts_registry(project_id, [])
    clear_selected_ai_cuts(project_id)
    refresh_cuts_defined_flag(project_id)

    metadata = load_metadata(project_id)
    metadata["status"] = "Cortes definidos limpos"
    save_metadata(project_id, metadata)

    if is_ajax_request():
        return jsonify({"ok": True})

    scroll_y = request.form.get("scroll_y", "")
    if scroll_y:
        return redirect(url_for("project_detail", project_id=project_id, scroll_y=scroll_y))
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/process_cuts/<project_id>", methods=["POST"])
def process_cuts(project_id):
    started = start_background_job(project_id, "process_cuts", run_process_cuts_job)
    if is_ajax_request():
        if not started:
            return jsonify({"ok": False, "message": "Já existe um processamento em andamento."}), 409
        return jsonify({"ok": True, "job_type": "process_cuts"})
    scroll_y = request.form.get("scroll_y", "")
    if scroll_y:
        return redirect(url_for("project_detail", project_id=project_id, scroll_y=scroll_y))
    return redirect(url_for("project_detail", project_id=project_id))


if __name__ == "__main__":
    app.run(debug=True, port=5050)

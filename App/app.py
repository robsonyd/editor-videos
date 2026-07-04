import json
import os
import re
import shutil
import subprocess
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

# Garante que o app aberto pelo Finder encontre ffmpeg/ffprobe instalados via Homebrew.
os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")

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

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
DEFAULT_STORAGE_FOLDER_NAME = "VideosEditados"
PROCESSING_FOLDER_NAME = "Estrutura de Processamento"
PROJECT_PROCESSING_SUBFOLDERS = ["Audios", "Arquivo Video Bruto", "Dados de Processamento", "Transcrições"]
PROJECT_PUBLIC_SUBFOLDERS = ["Videos Finalizados"]

WHISPER_CLI_PATH = BASE_DIR / "whisper.cpp" / "build" / "bin" / "whisper-cli"
WHISPER_MODEL_PATH = BASE_DIR / "Modelos" / "ggml-base.bin"

JOB_TYPES = ["generate_transcription", "identify_speakers", "suggest_cuts", "process_cuts", "split_video"]

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
                config.update({key: value for key, value in data.items() if key != "api_settings"})
                if isinstance(data.get("api_settings"), dict):
                    config["api_settings"].update(data["api_settings"])
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
        "openai_api_key": settings.get("openai_api_key") or ENV_OPENAI_API_KEY,
        "openai_model": settings.get("openai_model") or ENV_OPENAI_MODEL,
        "huggingface_token": settings.get("huggingface_token") or ENV_HUGGINGFACE_TOKEN,
        "pyannote_model": settings.get("pyannote_model") or ENV_PYANNOTE_MODEL,
    }


def get_public_api_settings() -> dict:
    settings = get_api_settings()
    return {
        "openai_configured": bool(settings.get("openai_api_key")),
        "openai_api_key_masked": mask_secret(settings.get("openai_api_key", "")),
        "openai_model": settings.get("openai_model", ""),
        "huggingface_configured": bool(settings.get("huggingface_token")),
        "huggingface_token_masked": mask_secret(settings.get("huggingface_token", "")),
        "pyannote_model": settings.get("pyannote_model", ""),
    }


def get_openai_client():
    api_key = get_api_settings().get("openai_api_key", "")
    if not api_key:
        return None
    return OpenAI(api_key=api_key)


def get_openai_model() -> str:
    return get_api_settings().get("openai_model") or ENV_OPENAI_MODEL


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


# Retorna o caminho dos trechos manuais do Video Splitter.
def get_split_manual_segments_path(project_id: str) -> Path:
    return get_project_subdir(project_id, "Dados de Processamento") / "split_manual_segments.json"


# Retorna o caminho base dos arquivos de transcrição.
def get_transcript_txt_path(project_id: str) -> Path:
    return get_project_subdir(project_id, "Transcrições") / f"{project_id}_transcricao.txt"


# Retorna o caminho SRT da transcrição.
def get_transcript_srt_path(project_id: str) -> Path:
    return get_project_subdir(project_id, "Transcrições") / f"{project_id}_transcricao.srt"


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


# Roda diarização real de áudio com pyannote.audio.
def run_pyannote_diarization(wav_path: Path, num_speakers=None, min_speakers=None, max_speakers=None) -> list[dict]:
    huggingface_token = get_huggingface_token()
    pyannote_model = get_pyannote_model()

    if not huggingface_token:
        raise RuntimeError(
            "Token da Hugging Face ausente. Configure o token em Configurações > APIs e Integrações."
        )

    try:
        from pyannote.audio import Pipeline
    except ImportError as exc:
        raise RuntimeError(
            "pyannote.audio não está instalado. Instale as dependências de diarização antes de identificar speakers."
        ) from exc

    try:
        try:
            pipeline = Pipeline.from_pretrained(pyannote_model, token=huggingface_token)
        except TypeError:
            pipeline = Pipeline.from_pretrained(pyannote_model, use_auth_token=huggingface_token)
    except Exception as exc:
        raise RuntimeError(
            f"não foi possível carregar o modelo de diarização ({pyannote_model}). Verifique token, acesso ao modelo e internet."
        ) from exc

    diarization_kwargs = {}
    if num_speakers:
        diarization_kwargs["num_speakers"] = int(num_speakers)
    else:
        if min_speakers:
            diarization_kwargs["min_speakers"] = int(min_speakers)
        if max_speakers:
            diarization_kwargs["max_speakers"] = int(max_speakers)

    diarization_output = pipeline(str(wav_path), **diarization_kwargs)

    # pyannote.audio 4.x retorna um DiarizeOutput.
    # A Annotation real fica em output.speaker_diarization.
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
        # Fallback compatível com alguns outputs novos que iteram como (turn, speaker).
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
    save_job_status(get_project_path(project_id), job_type, status)


# Marca falha amigável de um job.
def fail_job(project_id: str, job_type: str, raw_error: str):
    friendly = humanize_error(job_type, raw_error)
    update_job(project_id, job_type, build_error_status(job_type, friendly["message"], friendly["detail"]))
    metadata = load_metadata(project_id)
    metadata["status"] = friendly["message"]
    save_metadata(project_id, metadata)


# Inicia um job em thread se ainda não houver outro rodando.
def start_background_job(project_id: str, job_type: str, target):
    project_path = get_project_path(project_id)
    current = load_job_status(project_path, job_type)
    if current.get("state") == "running":
        return False

    update_job(project_id, job_type, build_running_status(job_type, 3, "Iniciando..."))
    thread = threading.Thread(target=target, args=(project_id,), daemon=True)
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

        project_path = get_project_path(project_id)
        wav_path = get_project_subdir(project_id, "Audios") / f"{project_id}.wav"
        transcript_base = get_project_subdir(project_id, "Transcrições") / f"{project_id}_transcricao"
        transcript_txt_path = get_transcript_txt_path(project_id)
        transcript_srt_path = get_transcript_srt_path(project_id)

        update_job(project_id, "generate_transcription", build_running_status("generate_transcription", 10, "Preparando áudio..."))
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                video_path,
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(wav_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        update_job(project_id, "generate_transcription", build_running_status("generate_transcription", 45, "Gerando transcrição..."))
        subprocess.run(
            [
                str(WHISPER_CLI_PATH),
                "-m",
                str(WHISPER_MODEL_PATH),
                "-f",
                str(wav_path),
                "-l",
                "pt",
                "-otxt",
                "-osrt",
                "-of",
                str(transcript_base),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        update_job(project_id, "generate_transcription", build_running_status("generate_transcription", 90, "Finalizando arquivos..."))

        if not transcript_txt_path.exists():
            raise RuntimeError("txt/srt não gerado")

        metadata["transcription_generated"] = "yes"
        metadata["status"] = "Transcrição com timestamps gerada" if transcript_srt_path.exists() else "Transcrição gerada sem SRT"
        save_metadata(project_id, metadata)
        update_job(project_id, "generate_transcription", build_success_status("generate_transcription", "Transcrição concluída", "Os arquivos de transcrição foram gerados com sucesso."))
    except subprocess.CalledProcessError as e:
        raw = e.stderr or e.stdout or str(e)
        if "ffmpeg" in (e.cmd[0] if e.cmd else ""):
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
        srt_path = get_transcript_srt_path(project_id)
        if not srt_path.exists():
            raise FileNotFoundError("transcrição SRT ausente")

        wav_path = get_project_subdir(project_id, "Audios") / f"{project_id}.wav"
        if not wav_path.exists():
            if not video_path or not Path(video_path).exists():
                raise FileNotFoundError("áudio e vídeo ausentes")

            update_job(project_id, "identify_speakers", build_running_status("identify_speakers", 8, "Preparando áudio para diarização..."))
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    video_path,
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    str(wav_path),
                ],
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
        detail = " · ".join(detail_parts) if detail_parts else "Sem número de speakers definido."

        update_job(project_id, "identify_speakers", build_running_status("identify_speakers", 45, "Identificando speakers pelo áudio...", detail))
        diarization_segments = run_pyannote_diarization(
            wav_path,
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        )
        if not diarization_segments:
            raise RuntimeError("nenhum speaker identificado")

        update_job(project_id, "identify_speakers", build_running_status("identify_speakers", 82, "Combinando speakers com a transcrição..."))
        speaker_transcript = build_speaker_transcript_from_segments(srt_segments, diarization_segments)
        speaker_transcript["settings"] = speaker_settings
        save_speaker_transcript(project_id, speaker_transcript)

        speaker_count = len([speaker for speaker in speaker_transcript.get("speakers", []) if speaker.get("id") != "SPEAKER_UNKNOWN"])
        metadata["speakers_identified"] = "yes"
        metadata["status"] = f"Speakers identificados: {speaker_count}"
        save_metadata(project_id, metadata)

        update_job(
            project_id,
            "identify_speakers",
            build_success_status(
                "identify_speakers",
                "Speakers identificados",
                f"A diarização encontrou {speaker_count} speaker(s) e gerou speaker_transcript.json.",
            ),
        )
    except subprocess.CalledProcessError as e:
        raw = e.stderr or e.stdout or str(e)
        fail_job(project_id, "identify_speakers", f"ffmpeg falhou: {raw}")
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

        srt_path = get_transcript_srt_path(project_id)
        if not srt_path.exists():
            raise FileNotFoundError("sem srt")

        ai_request = load_ai_request(project_id)
        selected_speakers = ai_request.get("selected_speakers", [])
        if not isinstance(selected_speakers, list):
            selected_speakers = []
        free_prompt = str(ai_request.get("free_prompt", "")).strip()

        update_job(project_id, "suggest_cuts", build_running_status("suggest_cuts", 15, "Lendo transcrição..."))
        speaker_transcript = load_speaker_transcript(project_id)
        speaker_mode_enabled = bool(speaker_transcript.get("segments"))

        if speaker_mode_enabled:
            transcript_for_ai = build_ai_transcript_from_speakers(speaker_transcript, selected_speakers)
            if not transcript_for_ai:
                raise RuntimeError("nenhum trecho disponível para os speakers selecionados")

            speaker_names = {speaker.get("id"): speaker.get("name", "") for speaker in speaker_transcript.get("speakers", [])}
            selected_labels = []
            for speaker_id in selected_speakers:
                selected_labels.append(speaker_names.get(speaker_id) or speaker_id)
            selected_speakers_text = ", ".join(selected_labels) if selected_labels else "todos os speakers identificados"
            transcript_label = "Transcrição com speakers"
            speaker_instruction = f"""
Filtro de speakers:
- Speakers selecionados para análise: {selected_speakers_text}.
- Se houver speakers selecionados, priorize somente as falas deles.
- Use falas de outros speakers apenas quando forem indispensáveis para entender o contexto, sem transformar essas falas no centro do corte.
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
- Respeite o filtro de speakers quando ele existir.
- Respeite o pedido livre do usuário.
- Retorne de 2 a 15 sugestões, se houver material suficiente.
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
- Retorne de 2 a 15 sugestões se existir material aproveitável.
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
      "reason": "Justificativa objetiva"
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
                subprocess.run(
                    [
                        "ffmpeg",
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
                        str(output_file),
                    ],
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
                subprocess.run(
                    [
                        "ffmpeg",
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
                        str(output_file),
                    ],
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
def select_ai_cuts_internal(project_id: str, selected_indexes: list, initial_margin: int, final_margin: int, select_all=False, replace_selected=False):
    suggestions = load_ai_suggestions(project_id)
    cuts_list = suggestions.get("cuts", [])
    metadata = load_metadata(project_id)
    max_duration = None

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


@app.route("/")
def home():
    projects = []
    storage_status = get_storage_status()
    storage_root = get_storage_root()
    if storage_root.exists():
        for project_folder in sorted(storage_root.iterdir(), reverse=True):
            if project_folder.is_dir():
                metadata = load_metadata(project_folder.name)
                projects.append(
                    {
                        "id": project_folder.name,
                        "project_number": project_folder.name.split("_", 1)[0] if "_" in project_folder.name else "",
                        "project_title": metadata.get("project_title", ""),
                        "video_name": metadata.get("video_name", ""),
                        "status": metadata.get("status", "Novo"),
                        "task_type": metadata.get("task_type", "ai_cuts"),
                        "task_label": "Video Splitter" if metadata.get("task_type") == "video_splitter" else "Cortes e Ganchos com I.A",
                    }
                )
    return render_template("index.html", projects=projects, storage_status=storage_status)


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
    return render_template(
        "settings.html",
        api_settings=get_public_api_settings(),
        openai_model_options=OPENAI_MODEL_OPTIONS,
        pyannote_model_options=PYANNOTE_MODEL_OPTIONS,
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

    save_app_config({"api_settings": updated})
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

        else:
            raise ValueError("Provedor inválido.")

        if is_ajax_request():
            return jsonify({"ok": True, "message": message})
        return redirect(url_for("settings_page", api_test=message))

    except Exception as e:
        message = str(e)
        if is_ajax_request():
            return jsonify({"ok": False, "message": message}), 400
        return redirect(url_for("settings_page", api_error=message))


@app.route("/open_project_folder/<project_id>", methods=["POST"])
def open_project_folder(project_id):
    project_path = get_project_path(project_id)
    if project_path.exists():
        subprocess.run(["open", str(project_path)], check=False)
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/delete_project/<project_id>", methods=["POST"])
def delete_project(project_id):
    project_path = get_project_path(project_id)
    storage_root = get_storage_root().resolve()

    try:
        resolved_project_path = project_path.resolve()
    except FileNotFoundError:
        return redirect(url_for("home"))

    if storage_root in resolved_project_path.parents and resolved_project_path.exists():
        shutil.rmtree(resolved_project_path)

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
    transcript_text = ""
    transcript_srt_text = ""

    transcript_txt_path = get_transcript_txt_path(project_id)
    transcript_srt_path = get_transcript_srt_path(project_id)

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
    job_statuses = {job_type: load_job_status(get_project_path(project_id), job_type) for job_type in JOB_TYPES}

    return render_template(
        "cuts.html",
        project_id=project_id,
        metadata=metadata,
        transcript_text=transcript_text,
        transcript_srt_text=transcript_srt_text,
        cuts=cuts,
        processed_files=processed_files,
        ai_suggestions=ai_suggestions,
        selected_ai_cuts=selected_ai_cuts,
        speaker_transcript=speaker_transcript,
        speaker_settings=speaker_settings,
        ai_request=ai_request,
        next_cut_number=next_cut_number,
        job_statuses=job_statuses,
    )



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
    return jsonify(load_job_status(get_project_path(project_id), job_type))


@app.route("/reset_project/<project_id>", methods=["POST"])
def reset_project(project_id):
    metadata = load_metadata(project_id)
    project_path = get_project_path(project_id)

    paths = [
        get_transcript_txt_path(project_id),
        get_transcript_srt_path(project_id),
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

    for output_name in get_processed_files(project_id):
        output_path = get_project_subdir(project_id, "Videos Finalizados") / output_name
        if output_path.exists():
            output_path.unlink()

    metadata["status"] = "Vídeo carregado"
    metadata["transcription_generated"] = "no"
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

        subprocess.run(
            [
                "ffmpeg",
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
                str(sample_path),
            ],
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
            return jsonify({"ok": False, "message": "Já existe uma identificação de speakers em andamento."}), 409
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
            raise ValueError("nenhuma transcrição com speakers encontrada")

        names_by_id = {}
        for speaker in speaker_transcript.get("speakers", []):
            speaker_id = speaker.get("id", "")
            if speaker_id:
                names_by_id[speaker_id] = request.form.get(f"speaker_name_{speaker_id}", "").strip()

        speaker_transcript = apply_speaker_names(speaker_transcript, names_by_id)
        save_speaker_transcript(project_id, speaker_transcript)

        metadata = load_metadata(project_id)
        metadata["status"] = "Nomes dos speakers atualizados"
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


@app.route("/suggest_cuts/<project_id>", methods=["POST"])
def suggest_cuts(project_id):
    save_ai_request(
        project_id,
        {
            "selected_speakers": request.form.getlist("selected_speakers"),
            "free_prompt": request.form.get("free_prompt", "").strip(),
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

        result = select_ai_cuts_internal(
            project_id,
            selected_indexes,
            initial_margin,
            final_margin,
            select_all=select_all,
            replace_selected=replace_selected,
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

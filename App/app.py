import json
import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, url_for
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

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
BRUTO_DIR = BASE_DIR / "Bruto"
PROCESSADOS_DIR = BASE_DIR / "Processados"
PROJECTS_DIR = BASE_DIR / "Projetos"
TRANSCRIPTS_DIR = BASE_DIR / "Transcricoes"

WHISPER_CLI_PATH = BASE_DIR / "whisper.cpp" / "build" / "bin" / "whisper-cli"
WHISPER_MODEL_PATH = BASE_DIR / "Modelos" / "ggml-base.bin"

JOB_TYPES = ["generate_transcription", "suggest_cuts", "process_cuts"]

for folder in [BRUTO_DIR, PROCESSADOS_DIR, PROJECTS_DIR, TRANSCRIPTS_DIR]:
    folder.mkdir(parents=True, exist_ok=True)


# Retorna o caminho raiz de um projeto.
def get_project_path(project_id: str) -> Path:
    return PROJECTS_DIR / project_id


# Retorna o caminho do metadata do projeto.
def get_metadata_path(project_id: str) -> Path:
    return get_project_path(project_id) / "metadata.txt"


# Retorna o caminho do registro principal dos cortes.
def get_cuts_registry_path(project_id: str) -> Path:
    return get_project_path(project_id) / "cuts.json"


# Retorna o caminho do arquivo texto compatível com o legado.
def get_cuts_txt_path(project_id: str) -> Path:
    return get_project_path(project_id) / "cuts.txt"


# Retorna o caminho das sugestões selecionadas para a seção 5.
def get_selected_ai_cuts_path(project_id: str) -> Path:
    return get_project_path(project_id) / "selected_ai_cuts.json"


# Retorna o caminho das sugestões da IA.
def get_ai_suggestions_path(project_id: str) -> Path:
    return get_project_path(project_id) / "ai_suggestions.json"


# Retorna o caminho base dos arquivos de transcrição.
def get_transcript_txt_path(project_id: str) -> Path:
    return TRANSCRIPTS_DIR / f"{project_id}_transcricao.txt"


# Retorna o caminho SRT da transcrição.
def get_transcript_srt_path(project_id: str) -> Path:
    return TRANSCRIPTS_DIR / f"{project_id}_transcricao.srt"


# Salva o metadata simples do projeto.
def save_metadata(project_id: str, data: dict):
    project_path = get_project_path(project_id)
    project_path.mkdir(parents=True, exist_ok=True)
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
def build_unique_output_name(name: str) -> str:
    base_name = sanitize_filename(name)
    candidate = f"{base_name}.mp4"
    index = 2
    while (PROCESSADOS_DIR / candidate).exists():
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
def format_seconds_to_time(total_seconds: int) -> str:
    total_seconds = max(0, int(total_seconds))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


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
            output_path = PROCESSADOS_DIR / output_name
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
        wav_path = project_path / f"{project_id}.wav"
        transcript_base = TRANSCRIPTS_DIR / f"{project_id}_transcricao"
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


# Executa a sugestão de cortes com IA em segundo plano.
def run_suggest_cuts_job(project_id: str):
    metadata = load_metadata(project_id)
    project_path = get_project_path(project_id)

    try:
        if not client:
            raise RuntimeError("sem chave")

        srt_path = get_transcript_srt_path(project_id)
        if not srt_path.exists():
            raise FileNotFoundError("sem srt")

        update_job(project_id, "suggest_cuts", build_running_status("suggest_cuts", 15, "Lendo transcrição..."))
        srt_text = srt_path.read_text(encoding="utf-8")

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
- Retorne de 2 a 15 sugestões, se houver material suficiente.
- Nunca invente tempos inexistentes.
- Use apenas os timestamps da transcrição como base.
- Se o vídeo for muito curto, ainda assim tente retornar o melhor corte possível respeitando essas regras.
- Responda apenas JSON válido.
"""

        user_prompt = f"""
Analise a transcrição abaixo e sugira pares conteúdo + gancho.

Instruções:
- Primeiro tente encontrar conteúdos entre 40 e 90 segundos.
- Se não houver nenhum bom nessa faixa, aceite conteúdos entre 20 e 40 segundos.
- O gancho deve estar dentro do conteúdo e ter entre 5 e 15 segundos.
- Retorne de 2 a 15 sugestões se existir material aproveitável.
- Não invente timestamps.

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

Transcrição:
{srt_text}
"""

        update_job(project_id, "suggest_cuts", build_running_status("suggest_cuts", 45, "Consultando IA..."))
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
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

            output_name = build_unique_output_name(name)
            output_file = PROCESSADOS_DIR / output_name

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
    if PROJECTS_DIR.exists():
        for project_folder in sorted(PROJECTS_DIR.iterdir(), reverse=True):
            if project_folder.is_dir():
                metadata = load_metadata(project_folder.name)
                projects.append(
                    {
                        "id": project_folder.name,
                        "video_name": metadata.get("video_name", ""),
                        "status": metadata.get("status", "Novo"),
                    }
                )
    return render_template("index.html", projects=projects)


@app.route("/upload", methods=["POST"])
def upload_video():
    if "video_file" not in request.files:
        return redirect(url_for("home"))

    file = request.files["video_file"]
    if file.filename == "":
        return redirect(url_for("home"))

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    project_id = f"projeto_{timestamp}"
    project_path = get_project_path(project_id)
    project_path.mkdir(parents=True, exist_ok=True)

    video_name = file.filename
    bruto_path = BRUTO_DIR / f"{project_id}_{video_name}"
    file.save(bruto_path)

    duration = get_video_duration(bruto_path)
    save_metadata(
        project_id,
        {
            "project_id": project_id,
            "video_name": video_name,
            "video_path": str(bruto_path),
            "duration": str(duration) if duration else "",
            "status": "Vídeo carregado",
            "transcription_generated": "no",
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
        next_cut_number=next_cut_number,
        job_statuses=job_statuses,
    )


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
        get_selected_ai_cuts_path(project_id),
        project_path / f"{project_id}.wav",
        project_path / "selected_ai_cut.json",
    ]

    for path in paths:
        if path.exists():
            path.unlink()

    for output_name in get_processed_files(project_id):
        output_path = PROCESSADOS_DIR / output_name
        if output_path.exists():
            output_path.unlink()

    metadata["status"] = "Vídeo carregado"
    metadata["transcription_generated"] = "no"
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


@app.route("/suggest_cuts/<project_id>", methods=["POST"])
def suggest_cuts(project_id):
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
            output_path = PROCESSADOS_DIR / output_name
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
    app.run(debug=True)

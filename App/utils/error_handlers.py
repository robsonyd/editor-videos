def build_error_title(job_type: str) -> str:
    titles = {
        "generate_transcription": "Erro ao gerar transcrição",
        "suggest_cuts": "Erro ao sugerir cortes com IA",
        "process_cuts": "Erro ao processar cortes",
    }
    return titles.get(job_type, "Erro no processamento")


ERROR_MAP = {
    "generate_transcription": [
        ("vídeo não encontrado", "O vídeo do projeto não foi encontrado.", "Confira se o arquivo bruto ainda existe e tente novamente."),
        ("whisper-cli não encontrado", "O whisper-cli não foi encontrado.", "Verifique se o caminho do whisper.cpp está correto."),
        ("modelo whisper não encontrado", "O modelo do Whisper não foi encontrado.", "Confirme se o arquivo ggml-base.bin existe na pasta Modelos."),
        ("ffmpeg", "O FFmpeg falhou ao preparar o áudio.", "Pode haver problema no arquivo de vídeo ou no FFmpeg instalado."),
        ("whisper", "O Whisper falhou ao gerar a transcrição.", "Tente novamente. Se persistir, confira o modelo e o arquivo de áudio temporário."),
        ("txt/srt não gerado", "A transcrição foi executada, mas os arquivos finais não foram gerados.", "Verifique permissões de escrita e espaço em disco."),
    ],
    "suggest_cuts": [
        ("openai_api_key", "A chave da OpenAI não está configurada.", "Preencha a variável OPENAI_API_KEY no arquivo .env."),
        ("sem srt", "A transcrição com timestamps não foi encontrada.", "Gere a transcrição SRT antes de pedir sugestões."),
        ("internet", "Falha de conexão ao consultar a IA.", "Verifique sua internet e tente novamente."),
        ("resposta inválida", "A IA respondeu em um formato inesperado.", "Tente novamente. Se persistir, revise o prompt e o modelo."),
        ("json inválido", "A resposta da IA não veio em JSON válido.", "Tente novamente. Se persistir, registre a resposta bruta para inspeção."),
        ("nenhuma sugestão", "A IA não encontrou sugestões aproveitáveis.", "Pode ser que o vídeo não tenha trechos fortes dentro das regras atuais."),
    ],
    "process_cuts": [
        ("cuts.txt inexistente", "O arquivo cuts.txt não foi encontrado.", "Defina e salve os cortes antes de processar."),
        ("nenhum corte válido", "Nenhum corte válido foi encontrado.", "Revise início, fim e nome dos cortes salvos."),
        ("tempo inválido", "Há um corte com tempo inválido.", "Confira se o início é menor que o fim e se o formato está correto."),
        ("ffmpeg", "O FFmpeg falhou ao gerar um dos arquivos.", "Confira os tempos do corte e se o vídeo bruto ainda existe."),
        ("arquivo de saída não gerado", "O corte foi processado, mas o arquivo final não apareceu.", "Confira permissões, espaço em disco e nome do arquivo."),
    ],
}


def humanize_error(job_type: str, raw_error: str) -> dict:
    raw = (raw_error or "").strip()
    raw_lower = raw.lower()

    for key, message, detail in ERROR_MAP.get(job_type, []):
        if key in raw_lower:
            return {"message": message, "detail": detail}

    return {
        "message": build_error_title(job_type),
        "detail": raw if raw else "Ocorreu um erro inesperado.",
    }

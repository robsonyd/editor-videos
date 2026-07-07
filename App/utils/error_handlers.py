def build_error_title(job_type: str) -> str:
    titles = {
        "generate_transcription": "Erro ao gerar transcrição",
        "identify_speakers": "Erro ao mapear participantes",
        "suggest_cuts": "Erro ao sugerir cortes com IA",
        "process_cuts": "Erro ao processar cortes",
        "split_video": "Erro no Video Splitter",
    }
    return titles.get(job_type, "Erro no processamento")


SUPPORT_HINT = "Se isso não estiver claro ou continuar acontecendo, fale com Robson Yuri."


ERROR_MAP = {
    "generate_transcription": [
        ("api key ausente", "Provedor de IA não configurado.", "Abra Configurações, escolha e cadastre uma IA principal, como OpenAI, Claude, Gemini ou DeepSeek. Depois salve e clique em Testar IA principal.", False),
        ("api key não configurada", "Provedor de IA não configurado.", "Abra Configurações, escolha e cadastre uma IA principal, como OpenAI, Claude, Gemini ou DeepSeek. Depois salve e clique em Testar IA principal.", False),
        ("vídeo não encontrado", "O vídeo do projeto não foi encontrado.", "Confira se o arquivo bruto ainda existe na pasta do projeto e tente novamente.", False),
        ("whisper-cli não encontrado", "O motor local de transcrição não foi encontrado.", "Reinstale o EVR Deluxe pelo pacote mais recente.", True),
        ("modelo whisper não encontrado", "O modelo local do Whisper não foi encontrado.", "Reinstale o EVR Deluxe pelo pacote mais recente.", True),
        ("ffmpeg", "O FFmpeg falhou ao preparar o áudio.", "Tente outro arquivo ou confirme se o vídeo abre normalmente. Pode ser arquivo corrompido ou codec problemático.", True),
        ("whisper", "O Whisper falhou ao gerar a transcrição.", "Tente novamente. Se persistir, pode ser problema no arquivo de áudio temporário ou no motor local.", True),
        ("txt/srt não gerado", "A transcrição rodou, mas os arquivos finais não apareceram.", "Verifique espaço em disco e permissão de escrita na pasta do projeto.", True),
    ],
    "identify_speakers": [
        ("token da hugging face ausente", "Hugging Face não configurado.", "Abra Configurações, cadastre o token da Hugging Face, salve e clique em Testar Hugging Face.", False),
        ("pyannote.audio não está instalado", "Dependência de participantes não foi instalada.", "Reinstale o EVR Deluxe pelo pacote mais recente e abra o app novamente para concluir dependências.", True),
        ("não foi possível carregar o modelo de diarização", "Não foi possível carregar o modelo de participantes.", "Teste a Hugging Face em Configurações e confirme internet/acesso ao modelo Pyannote.", True),
        ("falha ao rodar diarização", "A análise de participantes falhou no áudio.", "Tente informar o número exato ou intervalo de participantes e rode novamente.", True),
        ("nenhum participante identificado", "Nenhum participante foi identificado.", "Tente definir o número esperado de participantes e rodar a etapa novamente.", False),
        ("pyannote falhou", "Pyannote falhou ao mapear participantes.", "Teste a Hugging Face em Configurações e tente novamente.", True),
    ],
    "suggest_cuts": [
        ("api key ausente", "Provedor de IA não configurado.", "Abra Configurações, escolha e cadastre uma IA principal, como OpenAI, Claude, Gemini ou DeepSeek. Depois salve e clique em Testar IA principal.", False),
        ("api key não configurada", "Provedor de IA não configurado.", "Abra Configurações, escolha e cadastre uma IA principal, como OpenAI, Claude, Gemini ou DeepSeek. Depois salve e clique em Testar IA principal.", False),
        ("sem srt", "A transcrição com timestamps não foi encontrada.", "Gere a transcrição antes de pedir sugestões.", False),
        ("internet", "Falha de conexão ao consultar a IA.", "Verifique sua internet e tente novamente.", False),
        ("resposta inválida", "A IA respondeu em um formato inesperado.", "Tente novamente. Se persistir, reduza o pedido livre ou troque o modelo.", True),
        ("json inválido", "A resposta da IA não veio em formato válido.", "Tente novamente. Se persistir, reduza o pedido livre ou troque o modelo.", True),
        ("nenhuma sugestão", "A IA não encontrou sugestões aproveitáveis.", "Ajuste o prompt, aumente a quantidade de cortes ou revise a transcrição.", False),
    ],
    "process_cuts": [
        ("cuts.txt inexistente", "Nenhum corte salvo foi encontrado.", "Defina os cortes antes de processar.", False),
        ("nenhum corte válido", "Nenhum corte válido foi encontrado.", "Revise início, fim e nome dos cortes salvos.", False),
        ("tempo inválido", "Há um corte com tempo inválido.", "Confira se o início é menor que o fim e se o formato está correto.", False),
        ("ffmpeg", "O FFmpeg falhou ao gerar um dos arquivos.", "Confira os tempos do corte e se o vídeo bruto ainda existe.", True),
        ("arquivo de saída não gerado", "O corte rodou, mas o arquivo final não apareceu.", "Confira permissões, espaço em disco e nome do arquivo.", True),
    ],
    "split_video": [
        ("nenhum trecho manual definido", "Nenhum trecho manual foi definido.", "Adicione pelo menos um trecho ou escolha outro modo de divisão.", False),
        ("adicione pelo menos um trecho manual", "Nenhum trecho manual foi definido.", "Adicione pelo menos um trecho antes de processar.", False),
        ("ffmpeg", "O FFmpeg falhou ao dividir o vídeo.", "Confira se o vídeo bruto ainda existe e se os tempos escolhidos são válidos.", True),
        ("arquivo de saída não gerado", "A divisão rodou, mas o arquivo final não apareceu.", "Confira espaço em disco e permissão de escrita na pasta do projeto.", True),
    ],
}


def humanize_error(job_type: str, raw_error: str) -> dict:
    raw = (raw_error or "").strip()
    raw_lower = raw.lower()

    for key, message, detail, needs_support in ERROR_MAP.get(job_type, []):
        if key in raw_lower:
            return {
                "message": message,
                "detail": detail,
                "support_hint": SUPPORT_HINT if needs_support else "",
            }

    return {
        "message": build_error_title(job_type),
        "detail": raw if raw else "Ocorreu um erro inesperado.",
        "support_hint": SUPPORT_HINT,
    }

# EVR Deluxe — instalador interno macOS

Esta pasta guarda o instalador interno do EVR Deluxe para distribuição em Macs da equipe.

## Arquivo para compartilhar

Depois de gerar o pacote, envie para o Google Drive o arquivo:

```txt
instalador/dist/EVR-Deluxe-Installer.pkg
```

O usuário deve baixar, abrir o `.pkg`, concluir a instalação e abrir **EVR Deluxe** em Aplicativos.

## O que o instalador faz

- Instala `EVR Deluxe.app` em `/Applications`.
- Embute uma cópia limpa do app dentro do bundle.
- Embute um runtime Python privado para Apple Silicon.
- Não instala Python global no Mac do usuário.
- Embute `ffmpeg` e `ffprobe` com as bibliotecas necessárias.
- Na primeira abertura, copia o app para:

```txt
~/Library/Application Support/EVR Deluxe/
```

- Cria/atualiza a `.venv` do usuário.
- Instala automaticamente as dependências Python do EVR na primeira abertura.
- Usa configurações e tokens locais por usuário.

## Requisitos da primeira versão interna

Esta build interna é para **Mac Apple Silicon**.

O pacote inclui:

- Python 3.11 privado/standalone para Apple Silicon.
- `ffmpeg` e `ffprobe`.
- Modelo Whisper `ggml-base.bin`.
- Build local de `whisper.cpp`.

A primeira abertura ainda precisa de internet para baixar e instalar as dependências Python da `.venv`, como `pyannote.audio`, `torch`, OpenAI e Hugging Face.

## Gerar novamente o pacote

Na raiz do projeto:

```bash
cd ~/Projetos/editor-videos
instalador/evr_deluxe_installer_pkg.sh
```

Saída:

```txt
instalador/dist/EVR-Deluxe-Installer.pkg
```

## Gerar pacote assinado e notarizado

Quando os certificados Apple estiverem instalados no Mac, use:

```bash
instalador/evr_deluxe_sign_notarize.sh
```

Passo a passo completo:

```txt
instalador/ASSINATURA_E_NOTARIZACAO.md
```

## Observações

- O pacote não inclui vídeos, projetos, transcrições, outputs, `.env` ou `App/config.json`.
- O pacote inclui Python 3.11 privado/standalone para Apple Silicon.
- O pacote inclui `ffmpeg` e `ffprobe`.
- O pacote inclui o modelo Whisper `Modelos/ggml-base.bin`.
- O pacote inclui o build local de `whisper.cpp`.
- O pacote ainda não está assinado/notarizado pela Apple.
- Por não estar assinado/notarizado, Macs podem bloquear a abertura após download pelo navegador. Veja `INSTRUCOES_TESTE_INTERNO.md` ou o passo a passo direto em `PASSO_A_PASSO_AJUSTES_SISTEMA.md`.

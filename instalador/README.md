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
- Na primeira abertura, copia o app para:

```txt
~/Library/Application Support/EVR Deluxe/
```

- Cria/atualiza a `.venv` do usuário.
- Instala as dependências Python do EVR.
- Usa configurações e tokens locais por usuário.

## Requisito da primeira versão interna

Esta primeira versão interna ainda precisa encontrar **Python 3.10+** no Mac do usuário.

Também é uma build inicial para **Mac Apple Silicon**. O binário local do `whisper.cpp` incluído no pacote foi compilado como `arm64`.

Ela procura automaticamente em:

- `/opt/homebrew/bin/python3`
- `/usr/local/bin/python3`
- `python3` no `PATH`
- `/usr/bin/python3`

Se nenhum Python compatível for encontrado, o app mostra um alerta. Uma sprint futura pode embutir Python no pacote para remover esse requisito.

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

## Observações

- O pacote não inclui vídeos, projetos, transcrições, outputs, `.env` ou `App/config.json`.
- O pacote inclui o modelo Whisper `Modelos/ggml-base.bin`.
- O pacote inclui o build local de `whisper.cpp`.
- O pacote ainda não está assinado/notarizado pela Apple.
- Por não estar assinado/notarizado, Macs podem bloquear a abertura após download pelo navegador. Veja `INSTRUCOES_TESTE_INTERNO.md`.

# Editor de Vídeos com IA

Aplicativo local em Python/Flask para processar vídeos, gerar transcrição com Whisper local, sugerir cortes com IA e exportar cortes em vídeo.

## Tecnologias usadas

- Python
- Flask
- ffmpeg / ffprobe
- whisper.cpp
- OpenAI API
- HTML, CSS e JavaScript

## Estrutura principal

- App/: código principal Flask
- Bruto/: vídeos brutos locais
- Modelos/: modelos locais do Whisper
- Processados/: cortes exportados
- Projetos/: dados dos projetos criados
- Transcricoes/: arquivos de transcrição
- whisper.cpp/: dependência externa local, não versionada

## Requisitos do Mac

Antes de rodar o projeto, instale:

brew install ffmpeg cmake

Também é necessário ter o whisper.cpp compilado localmente e o modelo ggml-base.bin dentro da pasta Modelos.

## Configuração do ambiente

Entre na pasta do app:

cd App

Crie e ative o ambiente virtual:

python -m venv venv
source venv/bin/activate

Instale as dependências:

pip install -r requirements.txt

Crie o arquivo .env com base no exemplo:

cp .env.example .env

Depois edite o .env e configure sua chave:

OPENAI_API_KEY=sua_chave_openai_aqui
OPENAI_MODEL=gpt-5.4

## Rodando o app

Com o ambiente virtual ativo:

cd App
python app.py

Abra no navegador:

http://127.0.0.1:5000

## Observações importantes

Arquivos sensíveis e pesados não são versionados:

- .env
- vídeos brutos
- vídeos processados
- transcrições
- projetos gerados
- modelos .bin
- ambiente virtual venv
- pasta whisper.cpp

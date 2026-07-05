# EVR Deluxe — resumo da sessão Codex

Este documento consolida as principais melhorias, ajustes, decisões de UX e funcionalidades implementadas nesta sessão de trabalho no projeto local **EVR Deluxe**.

EVR Deluxe significa **Editor de Vídeos do Robson Deluxe**.

O objetivo geral da sessão foi transformar o app de uma ferramenta técnica local em uma experiência mais próxima de produto: mais bonita, organizada, segura para jobs longos, com melhor fluxo por etapas, melhor controle de processamento e avanços importantes no Video Splitter.

## Contexto do projeto

Stack atual:

- Flask
- HTML/Jinja
- CSS próprio
- JavaScript simples
- FFmpeg / ffprobe
- whisper.cpp local
- OpenAI API
- pyannote.audio / Hugging Face

Como rodar localmente:

```bash
cd ~/Projetos/editor-videos
source .venv/bin/activate
python App/app.py
```

Porta local:

```txt
http://127.0.0.1:5050
```

Arquivos mais trabalhados nesta sessão:

- `App/app.py`
- `App/templates/index.html`
- `App/templates/cuts.html`
- `App/templates/settings.html`
- `App/templates/partials/progress_bar.html`
- `App/static/css/app.css`
- `App/static/css/progress.css`
- `App/static/js/app.js`
- `App/static/js/progress.js`
- `App/utils/job_status.py`
- `README.md`

Observação: arquivos sensíveis como `App/config.json`, `App/.env` e `App/.env.save` não devem ser versionados nem compartilhados.

## 1. Identidade e nome do produto

O projeto passou a ser chamado de **EVR Deluxe**.

Nome completo/conceito:

```txt
Editor de Vídeos do Robson Deluxe
```

Direção visual adotada:

- Interface dark/SaaS.
- Visual premium, moderno e profissional.
- PC-first.
- Otimizado para uso com mouse.
- Menos textos óbvios.
- Mais densidade operacional.
- Hierarquia visual mais clara.

## 2. Refatoração visual geral

Foi criada uma nova camada visual global em CSS próprio, sem frameworks externos.

Principais mudanças:

- Criação/uso de `App/static/css/app.css`.
- Criação/uso de `App/static/js/app.js`.
- Tema escuro predominante.
- Cards com bordas suaves, sombras sutis e estados de hover.
- Topbar mais elegante.
- Uso mais discreto do logo.
- Rodapé com créditos e contatos.
- Microinterações em botões, cards, inputs, collapses e estados de seleção.
- Redução de labels e textos explicativos redundantes.
- Ajustes de responsividade básica, mantendo foco em desktop.

Também foi reduzida a intensidade do efeito de luz que acompanha o mouse:

- Spotlight global: opacidade reduzida pela metade.
- Spotlight do Video Splitter: opacidade reduzida pela metade.

## 3. Home

A Home foi redesenhada para ficar mais compacta, operacional e menos publicitária.

Melhorias implementadas:

- Primeira seção da Home reorganizada como painel operacional.
- Remoção de labels óbvios como "Home", "Projetos", "Painel operacional", etc.
- Elementos principais alinhados mais à esquerda.
- Pasta de trabalho/storage integrada à área inicial.
- Texto de apoio para pasta de trabalho:

```txt
Pasta onde os projetos e arquivos gerados ficam salvos.
```

- Reordenação do fluxo de novo projeto:
  - Primeiro escolher tarefa.
  - Depois selecionar arquivo bruto.
  - Depois título opcional do projeto.
- Remoção de textos redundantes na seção de upload.
- Cards de projetos melhorados.
- Cada projeto passou a exibir:
  - número do projeto;
  - título;
  - ferramenta usada;
  - nome do arquivo;
  - última etapa mais avançada/recente;
  - ações principais.
- Botão para abrir pasta do projeto.
- Fluxo de exclusão com escolha:
  - excluir apenas do app;
  - excluir projeto e arquivos do computador.
- Correção de sobreposição no menu de exclusão.
- Instagram no rodapé tornado clicável e abrindo em nova aba.

## 4. Topbar e navegação

A topbar passou por várias iterações até chegar ao formato atual.

Implementações:

- Manter nome do programa como marca principal.
- Subtítulo/frase institucional:

```txt
Editor de vídeo local, organizado e inteligente
```

- Na página de projeto, a ferramenta atual aparece na topbar.
- Ordem desejada ajustada:
  - ferramenta atual;
  - Home;
  - Configurações.
- Remoção de botão redundante de voltar para Home dentro da página de cortes, já que a topbar cumpre essa função.

## 5. Configurações

A tela de configurações foi profundamente reorganizada.

Principais entregas:

- Visual dark/SaaS alinhado com o restante do app.
- Primeira seção com indicadores mais fortes e legíveis.
- Cards de status para OpenAI e Hugging Face.
- Alinhamento dos cards de status à direita.
- Mensagens de teste de API reposicionadas dentro da seção de chaves/modelos.
- Seção de desempenho criada e posicionada antes das chaves.
- Seção de chaves/modelos reposicionada como última área importante da tela.
- Campos de OpenAI e Hugging Face mantidos sem expor credenciais no HTML renderizado.
- Glossário adicionado à área de configurações.

## 6. Modo de desempenho

Foi implementado um sistema de modos de desempenho para processamento local no MacBook/macOS.

Modos:

- Padrão
- Econômico
- Equilibrado
- Máximo

Decisão importante:

- O modo padrão não mexe em prioridade, threads nem variáveis do sistema.
- O usuário precisa escolher deliberadamente outro modo.
- A camada atual foi documentada como pensada para MacBook/macOS.
- Em futura versão Windows, essa lógica deve ser adaptada.

Modo Econômico:

- Processa com calma.
- Reduz prioridade/uso.
- Indicado para deixar processando à noite.
- Mantém o Mac acordado durante o job sem manter a tela ligada.
- Pode solicitar tela apagando após 1 minuto durante processamento.

Modo Equilibrado:

- Meio-termo entre velocidade e conforto térmico.
- Recomendado para continuar trabalhando enquanto o EVR processa vídeos.

Modo Máximo:

- Usa mais prioridade/processamento para terminar mais rápido.
- Pode aumentar temperatura do processador.
- Texto ajustado com personalidade:

```txt
Usa o MEGABRAIN EM FORÇA TOTAL e mais prioridade de processamento para terminar mais rápido. Pode aumentar a temperatura do processador.
```

Também foi adicionada opção de "Ver detalhes" dos modos, sem permitir edição manual.

## 7. Segurança de jobs longos

Foram discutidas e implementadas melhorias para rodar jobs longos durante a noite com mais tranquilidade.

Implementações e decisões:

- Jobs passam a aplicar perfil de desempenho apenas durante o processamento.
- Ao fechar o app/processo, o comportamento do computador deve voltar ao normal.
- Uso de `caffeinate` limitado ao job, quando aplicável.
- Cancelamento/interrupção de jobs.
- Evitar deixar processos órfãos.
- Barra de progresso bloqueia a interface enquanto job está em andamento.
- Stop/cancelamento exposto na interface.

## 8. Barra de progresso inteligente

Foi feita uma sprint forte de progresso/overlay.

Principais entregas:

- Overlay glass bloqueando temporariamente o app enquanto processa.
- Percentual visível.
- Status textual do job.
- Modo de desempenho aplicado.
- Tempo decorrido.
- Previsão/estimativa.
- Histórico local para melhorar previsões após alguns processamentos.
- Mensagens melhores para jobs longos.
- Botão de parar/interromper processamento.
- Integração com jobs como:
  - transcrição;
  - identificação de participantes;
  - sugestões da IA;
  - processamento de cortes;
  - Video Splitter.

Também foi corrigido o tempo decorrido, que não estava atualizando corretamente em alguns casos.

## 9. Botão Mágico e automação

O Botão Mágico passou a ter uma estética integrada ao overlay de progresso.

Implementações:

- Overlay do Botão Mágico com GIF.
- Texto:

```txt
A magia está rolando...
```

- Barra do Botão Mágico com fundo mais transparente.
- Ajustes de opacidade.
- Correção de máscara/efeito de brilho do botão.
- Botão "Automatizar" adicionado na etapa 1.
- Popup de automação com seletor sequencial de até onde automatizar.
- Regras hierárquicas: o usuário não marca etapas aleatórias; escolhe até onde o EVR roda sozinho.

Conceito consolidado:

- Botão Mágico: roda tudo até entregar arquivos processados.
- Automatizar: usuário escolhe até qual etapa quer que rode sozinho e onde quer assumir manualmente.

Sprint futura recomendada:

- Sprint dedicada e exclusiva para robustez final do Botão Mágico.

## 10. Página de projeto — fluxo de Cortes inteligentes com I.A

A tela de projeto foi reorganizada em etapas compactas e colapsáveis.

Nome padronizado da tarefa:

```txt
Cortes inteligentes com I.A
```

Mudanças de UX:

- Etapas colapsadas por padrão.
- Etapa 1 permanece fixa/expandida.
- Demais etapas abrem apenas quando existe ação humana necessária.
- Conteúdos longos ficam escondidos por padrão.
- Cabeçalhos de etapa compactos:
  - "Etapa X" + título na mesma linha.
- Check verde para etapas concluídas.
- Check também no menu lateral.
- Correção de alinhamento vertical/horizontal dos checks.
- Tempo decorrido discreto ao lado do check.
- Preparação visual para tempos maiores que uma hora.
- Ícones de alerta foram preservados/desabilitados para uso futuro.
- Menu lateral refinado para evitar quebra feia em duas linhas.

## 11. Reorganização das etapas de Cortes inteligentes

O fluxo foi refinado conceitualmente.

Etapas passaram por ajustes e renumeração. A ideia final ficou mais clara:

- Começar.
- Transcrição.
- Mapeamento de participantes.
- Identificação de participantes.
- Sugerir cortes com IA.
- Sugestões da IA.
- Definir cortes manualmente.
- Fila/cortes definidos.
- Processamento.
- Arquivos processados.

Também foi trocada a linguagem de "speakers" para "participantes" na experiência do usuário.

## 12. Transcrição, revisão e glossário

Foi implementada uma camada obrigatória e automática de revisão da transcrição com IA.

Fluxo:

1. Whisper gera transcrição original.
2. IA revisa/corrige automaticamente.
3. EVR salva transcrição revisada.
4. EVR também gera SRT revisado para uso futuro.

Glossário:

- Adicionado em Configurações.
- Campos por item:
  - termo correto;
  - variações comuns erradas;
  - contexto opcional.

Exemplo:

```txt
Termo correto: Serpol
Variações comuns erradas: Serpó, Serpão, Serpolh
Contexto opcional: Nome da empresa mencionada no podcast
```

A revisão da IA usa o glossário como referência forte.

Também foi criada experiência de revisão manual:

- Popup grande.
- Comparação entre original e revisada.
- Campo editável apenas para texto revisado.
- Sem edição de timestamps.
- Botão "Salvar edições".
- Atalho `Cmd+S`.
- Botão para próxima diferença.
- Possibilidade de ouvir trecho para validar correção.
- Sistema entende ação de edição + próxima diferença como revisão, sem exigir botão "marcar revisado".

## 13. Participantes / diarização

Melhorias na parte de identificação e revisão de participantes:

- Separação conceitual entre mapeamento e identificação de participantes.
- Campos de número exato/mínimo/máximo de participantes compactados.
- Cards de participantes refinados.
- Ajustes para suportar 4 ou 5 participantes sem quebrar o layout.
- Reproduzir amostra de áudio de cada participante.
- Renomear participantes.
- Salvar nomes.
- Selecionar participantes para análise da IA.

Foi corrigido um problema conceitual em que uma etapa parecia concluída antes de o usuário revisar/nomear participantes.

## 14. Sugestões da IA

A seção de sugestões da IA foi ampliada.

Implementações:

- Botão "Selecionar todos".
- Margem inicial e margem final na mesma linha de controles.
- Botão "Adicionar todas as sugestões".
- Botão "Adicionar apenas selecionadas", habilitado somente quando houver seleção.
- Quando uma ou mais sugestões estão selecionadas, o botão de adicionar todas fica desabilitado.
- Reproduzir áudio do conteúdo sugerido.
- Reproduzir áudio do gancho sugerido.
- Detalhes/motivo da sugestão em área expansível.

Sprint adicional implementada:

- Escolher numericamente quantas opções de corte pedir para a IA.
- Escolher numericamente quantos ganchos por corte pedir.
- Valores salvos no `ai_request.json`.
- Prompt da IA ajustado para respeitar essas quantidades.
- Se houver mais de um gancho por corte, a interface mostra alternativas com rádio.
- É possível ouvir cada alternativa de gancho.
- Ao adicionar sugestões, o gancho escolhido segue para a etapa manual.

Limites implementados:

- Opções de corte: 1 a 15.
- Ganchos por corte: 1 a 5.

## 15. Cortes manuais

A etapa de cortes manuais foi refinada.

Melhorias:

- Linhas extras opcionais não aparecem mais de início.
- Botão "Adicionar nova linha".
- Botão "Limpar".
- Botão de excluir linha.
- Ícone de exclusão trocado para X vermelho minimalista.
- Cabeçalho único para:
  - início;
  - fim;
  - nome do corte.
- Nomes de cortes recalculados para não quebrar nomenclatura ao adicionar/excluir linhas.
- Linhas mais compactas.

## 16. Cortes definidos / fila

Melhorias na etapa de cortes definidos/fila:

- Visual mais compacto.
- Botão limpar ao final da etapa.
- Exclusão de item sem recarregar de forma desconfortável quando possível.
- Botão de excluir alinhado à direita.
- Mesmo padrão visual do X minimalista.

## 17. Arquivos processados

Na etapa de arquivos processados:

- Cada arquivo passou a ter botão "Reproduzir".
- "Reproduzir" abre o arquivo no player padrão do dispositivo.
- Cada arquivo também tem botão de excluir.
- Visual dos arquivos finalizados foi melhorado.

## 18. Video Splitter

Foi iniciada e avançada a sprint do Video Splitter com preview visual e linha do tempo.

Funcionalidades implementadas:

- Preview do vídeo dentro da tela do projeto.
- Rota segura para servir o vídeo original ao player.
- Linha do tempo visual.
- Playhead.
- Pinças de início e fim.
- Seleção visual de trecho.
- Inputs sincronizados de início/fim/nome.
- Botão "Marcar início".
- Botão "Marcar fim".
- Botão "Reproduzir trecho".
- Botão "Adicionar trecho".
- Lista manual de trechos persistida.
- Processamento dos trechos manuais com FFmpeg.
- Modos antigos preservados:
  - duração fixa;
  - número de partes;
  - preset social;
  - pinçar trechos manualmente.

Refinamentos recentes:

- Vídeo ficou menor para não ocupar a tela toda.
- Painel lateral com dados da seleção.
- Linha do tempo ocupa a largura total da sessão.
- Dois blocos principais da área de preview ajustados para 50% / 50%.
- Campos numéricos compactados.
- Ajuste para resoluções menores, evitando quebra lateral.
- Pinças visualmente mais finas, mas com área invisível maior para clicar.
- Ao arrastar pinças, o vídeo acompanha o ponto.
- Playhead branco também pode ser arrastado.
- Modo de seleção:
  - Livre: início/fim ajustáveis manualmente.
  - Fixa: duração travada e faixa arrastável.
- Campo para digitar duração fixa em segundos.
- Presets rápidos:
  - 15s;
  - 30s;
  - 60s;
  - 90s;
  - 3min.
- Ao clicar em "Adicionar trecho", o botão dá feedback visual:

```txt
Trecho adicionado ✓
```

com pulso/brilho curto.

Conceito atual:

- Modo de divisão define como o processamento final vai rodar.
- Seleção visual define o trecho que o usuário quer pinçar/adicionar.
- Ao adicionar trecho manual, o app muda para o modo de trechos manuais.

## 19. Exclusão de projetos e arquivos

Foi implementada melhoria na Home:

- Excluir apenas do aplicativo.
- Excluir projeto e arquivos.

Comportamento:

- "Só do app" remove o projeto da listagem sem apagar a pasta.
- "Projeto e arquivos" remove a pasta do projeto e arquivos gerados/brutos.

Também foi corrigida sobreposição visual do menu de exclusão.

## 20. Contatos e rodapé

Informações de contato registradas:

- Instagram: `robson.yd`
- E-mail: `robson.yd@gmail.com`
- WhatsApp: `47988553204`
- Local: São Paulo - SP

Rodapé recebeu créditos e links.

Créditos:

```txt
Criado por Robson Yuri
```

Instagram tornou-se clicável e abre em nova aba.

## 21. Ajustes visuais finos implementados

Lista de refinamentos menores, mas importantes:

- Redução de tamanho de títulos que quebravam em duas linhas.
- Remoção de textos auxiliares desnecessários.
- Redução de contraste de informações secundárias.
- Correção de botões dourados em hover, com brilho mais delicado.
- Correção de brilho do Botão Mágico vazando sobre outros elementos.
- Correção de botões de expandir desalinhados.
- Padronização de botões de expandir no projeto todo.
- Check verde mais saturado e nítido.
- Redução do tamanho do check em seções.
- Correção de posição vertical de checks.
- Separação mais clara de hierarquia entre título, subtítulo, status e metadados.
- Melhor aproveitamento horizontal em telas grandes.
- Redução de páginas longas com accordions/collapses.

## 22. Arquivos criados/adicionados

Arquivos novos ou relevantes adicionados na sessão:

- `App/static/css/app.css`
- `App/static/js/app.js`
- `RESUMO_SESSAO_CODEX.md`

Arquivos sensíveis presentes localmente mas que não devem ser compartilhados:

- `App/.env`
- `App/.env.save`
- `App/config.json`

## 23. Validações feitas ao longo da sessão

Foram usados checks como:

```bash
python3 -m py_compile App/app.py App/utils/job_status.py
.venv/bin/python -c "import sys; sys.path.insert(0, 'App'); import app; print('app import ok')"
.venv/bin/python -c "from pathlib import Path; from jinja2 import Environment; Environment().parse(Path('App/templates/cuts.html').read_text(encoding='utf-8')); print('cuts template ok')"
node --check App/static/js/progress.js
```

Também foram renderizados templates com contexto fake para validar o Video Splitter sem depender de dados reais.

## 24. Próximas sprints recomendadas

Ordem sugerida antes de empacotar:

1. Sprint dedicada do Botão Mágico.
2. Finalização/estabilização do Video Splitter.
3. Sprint de estabilização geral.
4. Empacotamento Mac.
5. Pré-refatoração/arquitetura.
6. Só depois discutir migração/refatoração para outra linguagem.

## 25. Sprint futura — Botão Mágico

Objetivo:

- Tornar o fluxo 1-clique realmente confiável.

Ideias:

- Retomada em caso de erro.
- Cancelamento mais explícito.
- Log por etapa.
- Estado visual pós-falha.
- Relatório final do que foi feito.
- Escolha de até onde automatizar.
- Garantir que todos os jobs respeitem cancelamento.
- Garantir que não existam processos órfãos.
- Melhorar mensagens do overlay.

## 26. Sprint futura — Video Splitter 2.x

Ideias:

- Thumbnails na timeline.
- Zoom da timeline.
- Snap em segundos ou frames.
- Lista de trechos sempre visível em painel lateral.
- Reordenar trechos.
- Duplicar trecho.
- Nome automático por preset.
- Exportar múltiplas versões.
- Prévia visual do intervalo selecionado.
- Melhor integração entre preset social e seleção visual.

## 27. Sprint futura — prompts

Ideias:

- Predefinições de prompt.
- Salvar prompt personalizado.
- Editar/excluir predefinições.
- Categorias:
  - Podcast;
  - Reels;
  - Cortes estratégicos;
  - Cortes polêmicos;
  - Cortes educacionais;
  - LinkedIn.

## 28. Sprint futura — transcrição/glossário 2.0

Ideias:

- Melhorar revisão manual.
- Melhorar comparação original vs revisada.
- Sugestões automáticas com base no glossário.
- Histórico de correções.
- Aprendizado de termos frequentes.
- Melhorias no SRT revisado.
- Usar glossário por projeto ou global.

## 29. Sprint futura — empacotamento

Objetivo:

- Transformar o EVR Deluxe em instalável/app local.

Pontos a resolver:

- Empacotar Python/venv.
- Garantir FFmpeg.
- Garantir whisper.cpp e modelo.
- Primeiro uso/configuração inicial.
- Permissões de pasta.
- Local de armazenamento.
- Atualização futura.
- Separar dados do app e dados do usuário.

## 30. Sprint futura — refatoração/portabilidade

Foi observado que:

- A camada de desempenho atual é macOS/MacBook.
- Futuro Windows exigirá adaptação.
- Antes de migrar linguagem, é melhor estabilizar o produto.

Possíveis caminhos futuros:

- Manter Flask e melhorar arquitetura.
- Separar backend de processamento.
- Criar frontend mais robusto.
- Criar app desktop.
- Migrar partes críticas para outra linguagem apenas depois do MVP estável.

## 31. Estado geral atual

O EVR Deluxe já está bem mais próximo de um produto local utilizável:

- Interface dark/SaaS consistente.
- Home operacional.
- Configurações robustas.
- Fluxo de Cortes inteligentes mais claro.
- Transcrição revisada por IA.
- Glossário.
- Participantes.
- Sugestões da IA com áudio e quantidades configuráveis.
- Cortes manuais melhorados.
- Processamento com barra inteligente.
- Modo desempenho.
- Botão Mágico em evolução.
- Video Splitter com preview e timeline visual.

Ainda falta uma sprint forte de estabilização antes de empacotar.

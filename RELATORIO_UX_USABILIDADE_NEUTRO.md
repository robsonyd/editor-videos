# Relatório de sugestões de UX e usabilidade

## Resumo executivo

A rotina aplicada priorizou progressive disclosure, prevenção de erro e feedback imediato sem alterar a identidade visual do EVR Deluxe. As melhorias implementadas deixam a Home mais sequencial, tornam a configuração de IA mais segura e reduzem ruído no Video Splitter ao exibir controles conforme a funcionalidade selecionada.

As sugestões abaixo não foram implementadas automaticamente. Elas dependem de aprovação humana antes de virar código.

## Sugestões priorizadas

### Sugestão 1 - Criar um indicador global de pré-requisitos

- Prioridade: Alta
- Tela/fluxo impactado: Home, Configurações e projetos
- Problema observado: Estados como IA ausente, IA não testada, pasta configurada e desempenho definido aparecem em pontos diferentes da interface.
- Princípio de UX aplicado: Visibilidade do status do sistema
- Recomendações: Consolidar um resumo pequeno de pré-requisitos no topo, com links para resolver cada item.
- Benefício esperado: O usuário entende rapidamente o que falta antes de processar um episódio.
- Risco de implementação: Médio
- Depende de aprovação humana: Sim

### Sugestão 2 - Melhorar o fluxo de confirmação manual de API

- Prioridade: Alta
- Tela/fluxo impactado: Configurações
- Problema observado: A confirmação manual libera recursos sem validação real da API.
- Princípio de UX aplicado: Prevenção de erro
- Recomendações: Manter confirmação manual apenas para cenários de indisponibilidade de rede/teste, com texto mais forte explicando o risco.
- Benefício esperado: Reduz chance de usuário liberar IA com chave incorreta.
- Risco de implementação: Baixo
- Depende de aprovação humana: Sim

### Sugestão 3 - Separar melhor modos automáticos e modo manual no Video Splitter

- Prioridade: Média
- Tela/fluxo impactado: Video Splitter
- Problema observado: O player é útil em todos os modos, mas a linha do tempo e pinças só fazem sentido quando o usuário vai selecionar trechos.
- Princípio de UX aplicado: Progressive disclosure
- Recomendações: Transformar o modo manual em uma área de seleção visual mais explícita e manter modos automáticos como formulários compactos.
- Benefício esperado: Menos confusão sobre quais campos afetam o processamento.
- Risco de implementação: Médio
- Depende de aprovação humana: Sim

### Sugestão 4 - Persistir estado de seções expandidas por usuário

- Prioridade: Média
- Tela/fluxo impactado: Configurações
- Problema observado: Seções resolvidas ficam colapsadas por padrão, mas o usuário pode querer manter uma seção aberta durante ajustes repetidos.
- Princípio de UX aplicado: Controle e liberdade do usuário
- Recomendações: Salvar no navegador o último estado aberto/fechado das seções, sem afetar o backend.
- Benefício esperado: Mais conforto durante configuração e testes.
- Risco de implementação: Baixo
- Depende de aprovação humana: Sim

### Sugestão 5 - Revisar a ordem do DOM em Configurações

- Prioridade: Média
- Tela/fluxo impactado: Configurações
- Problema observado: A ordem visual foi priorizada, mas vale auditar a ordem de navegação por teclado para garantir que ela acompanhe a ordem visual.
- Princípio de UX aplicado: Acessibilidade e consistência
- Recomendações: Em sprint futura, reorganizar a estrutura do template para que API venha antes de desempenho também no HTML.
- Benefício esperado: Melhor experiência para teclado e tecnologias assistivas.
- Risco de implementação: Baixo
- Depende de aprovação humana: Sim

## Mudanças que NÃO devem ser feitas sem aprovação

- Redesenhar a identidade visual da Home ou Configurações.
- Remover o modo manual do Video Splitter.
- Eliminar confirmação manual de API sem decidir a regra de negócio.
- Trocar o modelo de armazenamento local de credenciais.
- Adicionar novas dependências externas para modais, tours ou formulários.

## Observações sobre estética

As sugestões não exigem alteração de identidade visual, cores ou estilo. Qualquer melhoria futura deve reutilizar os componentes visuais já existentes no EVR Deluxe.

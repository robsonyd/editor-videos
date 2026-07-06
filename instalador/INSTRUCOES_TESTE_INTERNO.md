# EVR Deluxe — instruções de teste interno no macOS

Este instalador é uma versão interna de teste e ainda não está assinado/notarizado pela Apple.

Por isso, depois de baixar pelo Google Drive, o macOS pode mostrar:

```txt
O item não foi aberto. A Apple não pode verificar se ele está livre de malware.
```

## Como abrir sem usar Terminal

1. Abra a pasta onde o arquivo foi baixado.
2. Clique com o botão direito no arquivo `EVR-Deluxe-Installer.pkg`.
3. Clique em **Abrir**.
4. Se aparecer um aviso de segurança, clique em **Abrir** novamente.
5. Se o macOS mostrar apenas **Mover para o Lixo**, siga o passo abaixo.

## Se aparecer apenas "Mover para o Lixo"

1. Abra **Ajustes do Sistema**.
2. Vá em **Privacidade e Segurança**.
3. Role até a seção **Segurança**.
4. Procure o aviso sobre o `EVR-Deluxe-Installer.pkg`.
5. Clique em **Abrir Mesmo Assim**.
6. Tente abrir o instalador novamente.

## Alternativa para TI ou usuário avançado

Se o Mac continuar bloqueando, rode no Terminal:

```bash
xattr -dr com.apple.quarantine ~/Downloads/EVR-Deluxe-Installer.pkg
open ~/Downloads/EVR-Deluxe-Installer.pkg
```

Se o arquivo estiver em outra pasta, ajuste o caminho.

## Primeira abertura do EVR Deluxe

Depois de instalar:

1. Abra **EVR Deluxe** em Aplicativos.
2. A primeira abertura pode demorar alguns minutos.
3. O app vai preparar o ambiente Python local do usuário.
4. Depois, configure OpenAI e Hugging Face na tela de Configurações.

## Requisitos desta versão interna

- Mac Apple Silicon.
- Python 3.10+ instalado.
- Internet na primeira abertura para baixar dependências Python.

Uma versão futura assinada/notarizada pela Apple deve remover esse alerta de segurança.

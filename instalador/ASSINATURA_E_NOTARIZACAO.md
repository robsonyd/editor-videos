# EVR Deluxe — assinatura e notarização Apple

Este fluxo remove o alerta do Gatekeeper para distribuição interna fora da App Store.

Não é revisão manual de app. A Apple faz uma checagem automática de assinatura, malware e regras técnicas de notarização.

## O que precisa estar pronto na conta Apple

### 1. Certificado Developer ID Application

Use para assinar o `.app` e os binários internos.

1. Abra o app **Acesso às Chaves** no Mac.
2. Menu **Acesso às Chaves > Assistente de Certificado > Solicitar um Certificado de uma Autoridade de Certificação**.
3. Informe seu e-mail Apple Developer.
4. Em **Nome Comum**, use algo como `Robson Yuri EVR Deluxe`.
5. Selecione **Salvo no disco** e gere o arquivo `.certSigningRequest`.
6. Acesse Apple Developer:
   `Certificates, Identifiers & Profiles > Certificates > +`
7. Escolha **Developer ID Application**.
8. Envie o `.certSigningRequest`.
9. Baixe o certificado gerado e dê dois cliques para instalar no Keychain.

### 2. Certificado Developer ID Installer

Use para assinar o `.pkg`.

Repita o processo acima, mas escolha **Developer ID Installer**.

## Conferir certificados no Mac

Na raiz do projeto:

```bash
security find-identity -v | grep "Developer ID"
```

O esperado é aparecer algo parecido com:

```txt
Developer ID Application: Nome/Empresa (TEAMID)
Developer ID Installer: Nome/Empresa (TEAMID)
```

## Criar credencial de notarização

### Opção rápida com app-specific password

1. Acesse `account.apple.com`.
2. Vá em **Sign-In and Security > App-Specific Passwords**.
3. Crie uma senha com nome `EVR Deluxe Notary`.
4. Copie a senha gerada.
5. Descubra seu **Team ID** em:
   `developer.apple.com/account > Membership details`.
6. Rode:

```bash
xcrun notarytool store-credentials "EVR_DELUXE_NOTARY" \
  --apple-id "SEU_EMAIL_APPLE" \
  --team-id "SEU_TEAM_ID"
```

Quando pedir a senha, cole a app-specific password.

## Gerar pacote assinado e notarizado

Na raiz do projeto:

```bash
instalador/evr_deluxe_sign_notarize.sh
```

O script faz:

1. Gera o app com Python privado, FFmpeg, Whisper e arquivos do EVR.
2. Assina todos os binários internos.
3. Assina o `.app`.
4. Gera o `.pkg`.
5. Assina o `.pkg`.
6. Envia para notarização Apple.
7. Aplica o ticket com `stapler`.
8. Valida com `spctl`.

Saída final:

```txt
instalador/dist/EVR-Deluxe-Installer.pkg
```

## Se houver mais de um certificado no Keychain

Defina manualmente:

```bash
EVR_APP_SIGN_IDENTITY="Developer ID Application: Nome (TEAMID)" \
EVR_INSTALLER_SIGN_IDENTITY="Developer ID Installer: Nome (TEAMID)" \
instalador/evr_deluxe_sign_notarize.sh
```

## Teste sem notarizar

Para testar só assinatura local:

```bash
EVR_SKIP_NOTARY=1 instalador/evr_deluxe_sign_notarize.sh
```

Esse modo ainda pode mostrar alerta do Gatekeeper, porque não tem notarização.

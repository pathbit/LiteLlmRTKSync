# Single sign-on: OpenID Connect and SAML 2.0

*(Versão em português ao final.)*

The panel has always had one door: a user, a password and a break-glass recovery credential.
Single sign-on adds a **second** door next to it. It never replaces the first one — if the
identity provider is unreachable, the local form is still on the screen and still works.

Nothing here is on by default. With no configuration the login page is exactly the page it has
always been, and the `/sso/...` routes answer **404**, the same way any address that was never
served answers.

---

## Before you start: the public address has to stay still

Single sign-on registers a **return address** at the provider. If that address changes, every
sign-in breaks with an error message written by the provider, not by us.

That rules out a quick tunnel: it hands out a new hostname every time it starts (see
[Remote Access](Remote-Access)). Use a **named tunnel** or **Tailscale**, and write that fixed
origin into the panel — it is the first field of the settings screen.

---

## Turning it on

Sign in with the local password, open **Settings** in the header bar, and fill in the tab of the
provider you use. Saving asks for your **current panel password** again, on top of the session
you already have.

That extra ask is not ceremony. Whoever steals an eight-hour session could otherwise point the
panel at an identity provider they control, put their own address on the allowed list, and keep
permanent access. The password they do not have is what stops that.

Two rules the screen enforces and will not bend on:

- **One provider at a time.** With two live issuers, a response from one can be accepted as if it
  came from the other. One issuer means there is exactly one legitimate answer to compare against.
- **The allowed list cannot be empty.** "Sign in with Google" with no filter means every Google
  account on the planet gets in. Give it at least one domain or one address, or the panel refuses
  to turn single sign-on on.

---

## OpenID Connect with Google Workspace, end to end

1. In the Google Cloud console, open **APIs and services → Credentials** and create an
   **OAuth client ID** of type *Web application*.
2. Under *Authorized redirect URIs*, paste the return address the panel shows you in the settings
   screen. It is your public origin followed by `/sso/oidc/callback`, and nothing else:

   ```
   https://panel.example.com/sso/oidc/callback
   ```

3. Google gives you a **Client ID** and a **client secret**. Copy both.
4. Back in the panel, on the OpenID Connect tab:

   | Field | Value |
   | --- | --- |
   | Public address of this panel | `https://panel.example.com` |
   | Issuer | `https://accounts.google.com` |
   | Client ID | the one Google gave you |
   | Client secret | the one Google gave you |
   | Scopes | `openid email profile` |
   | Allowed domains | `example.com` |

5. Type your panel password and save. Sign out, and the login page now carries a
   **Sign in with accounts.google.com** button next to the password form.

The same shape works for any provider that publishes a discovery document — Microsoft Entra ID,
Okta, Authentik, Keycloak. Only the issuer changes; Entra ID, for instance, uses
`https://login.microsoftonline.com/<your tenant>/v2.0`.

### The secret never comes back to the screen

The client secret is written to a file with mode `0600` inside `DATA_DIR`, next to the recovery
credential. It is never stored in the preferences database, never logged, never put in a page.

The settings screen shows `••••••••` and a field to replace it. **Saving with that field empty
keeps the secret you already have** — the field starts empty on every visit precisely because it
never shows the stored value, and clearing the secret for that reason would silently turn single
sign-on off.

You can also set it in the environment as `OIDC_CLIENT_SECRET`. The environment wins over the
file, and when it does the screen shows the field locked, the same way `DASHBOARD_PASSWORD`
already behaves.

---

## SAML 2.0

The SAML tab exists and is **disabled in the published image**, with a message saying so.

The honest reason: SAML signature validation cannot be done safely with the standard library.
`xml.etree` canonicalizes to C14N 2.0 while SAML signs over exclusive canonicalization 1.0, so
correct signatures would look invalid; there is no RSA verification in the standard library; and
defending against XML Signature Wrapping requires tying the signature reference to the element
that was actually read, which is library work, not pattern matching.

So SAML depends on `python3-saml`, declared as an optional extra in `pyproject.toml`. With the
library installed, the panel serves its service description at `/sso/saml/metadata` — behind
login, on purpose — for you to hand to the identity provider. Configure the provider to **sign
the assertion and not encrypt it**: encrypted assertions are out of scope, because they would
require a private key for the panel, which is one more secret to hold.

### Building an image that has it

The published image does not ship the library, and the project `Dockerfile` is unchanged. To build
your own, add this to it:

```dockerfile
RUN apk add --no-cache libxml2 libxslt xmlsec && \
    apk add --no-cache --virtual .build gcc musl-dev libxml2-dev libxslt-dev xmlsec-dev pkgconfig && \
    pip install --no-cache-dir --no-binary lxml,xmlsec python3-saml && \
    apk del .build
```

Compiling from source is not optional. The published wheels of `lxml` and `xmlsec` each bundle
their own copy of `libxml2`, at different versions, and the result is a version mismatch error at
import time. Building both against the system library is what resolves it; the build tools can be
dropped afterwards, and the runtime keeps working with the three shared libraries alone.

---

## When the identity provider goes down

Four independent ways back in, in the order you should try them:

1. **The local form never leaves the screen.** User and password still work, with the provider
   dead or alive.
2. **The recovery credential always works.** It is break-glass and stays valid after a password is
   set — see [Authentication](Authentication).
3. **`SSO_DISABLED=1` in the environment** beats the database without touching it. Recreate the
   container with the variable and the login page goes back to the plain form.
4. **The non-HTML door is untouched.** `curl`, cron and monitoring keep using Basic Auth and never
   pass through single sign-on at all.

```bash
docker compose -f docker-compose.example.yml up -d --force-recreate litellmrtk-sync
```

---

## What is checked before a session is issued

In this order, stopping at the first failure, and always with the same generic message on screen —
telling apart "wrong state" from "address not on the list" tells an attacker how far they got:

1. the same per-address rate limit as the login form, answering **429** with `Retry-After`;
2. the state cookie is present, intact and has not been spent before;
3. the `state` in the query matches the one in the cookie, compared in constant time;
4. the code is exchanged at the token endpoint with proof of key exchange, always S256;
5. the identity token says it came from the configured issuer, carries our audience, is inside its
   validity window, and repeats the one-time number we sent;
6. the user info endpoint confirms the same subject, and that the address was verified;
7. the address is on the allowed list.

Only then does the panel issue **the same signed cookie the password form issues** — no second
kind of session exists. The landing page is a plain page with a refresh to `/`; no parameter from
the provider ever becomes a destination.

| Route | Session | What it is for |
| --- | --- | --- |
| `/sso/oidc/iniciar` | public | sends the browser to the provider |
| `/sso/oidc/callback` | public | the provider sends the browser back |
| `/sso/saml/iniciar` | public | same, for SAML |
| `/sso/saml/acs` | public | where the provider posts the assertion |
| `/sso/saml/metadata` | required | service description, downloaded signed in |
| `/acoes/sso` (POST) | required, plus the local password | saves the settings |

The four public routes are public because the round trip to the provider happens before there is a
session — creating one is what they are for. They still answer 404 while single sign-on is off.

---

## Out of scope, declared

- **Federated sign-out.** Signing out clears the local cookie only. The session at the provider is
  still open, so the next click on the sign-in button comes straight back without asking. That is
  behaviour, not a defect.
- **Encrypted SAML assertions**, for the reason above.
- **Signed authentication requests** to the provider: almost no provider requires them, and one
  would cost another private key to hold.

---
---

# Acesso federado: OpenID Connect e SAML 2.0

O painel sempre teve uma porta: usuário, senha e uma credencial de recuperação de emergência. O
acesso federado acrescenta uma **segunda** porta ao lado dela. Nunca substitui a primeira — se o
provedor de identidade estiver fora do ar, o formulário local continua na tela e continua
funcionando.

Nada aqui vem ligado. Sem configuração, a tela de login é exatamente a de sempre, e as rotas
`/sso/...` respondem **404**, do mesmo jeito que qualquer endereço que nunca foi servido.

---

## Antes de começar: o endereço público precisa ficar parado

O acesso federado registra um **endereço de retorno** no provedor. Se esse endereço muda, toda
entrada passa a falhar com uma mensagem escrita pelo provedor, não por nós.

Isso descarta o túnel rápido: ele entrega um nome novo a cada subida (veja
[Remote Access](Remote-Access)). Use um **túnel nomeado** ou o **Tailscale**, e escreva essa
origem fixa no painel — é o primeiro campo da tela de configuração.

---

## Ligando

Entre com a senha local, abra **Configurações** na barra do cabeçalho e preencha a aba do provedor
que você usa. Salvar pede de novo a **senha atual do painel**, além da sessão que você já tem.

Esse pedido a mais não é cerimônia. Quem roubar uma sessão de oito horas poderia, sem ele, apontar
o painel para um provedor de identidade que controla, pôr o próprio endereço na lista de
autorizados e ficar com acesso permanente. A senha que ele não tem é o que impede isso.

Duas regras que a tela cobra e não abre mão:

- **Um provedor por vez.** Com dois emissores válidos, uma resposta de um pode ser aceita como se
  fosse do outro. Um emissor significa que há exatamente uma resposta legítima a comparar.
- **A lista de autorizados não pode ficar vazia.** "Entrar com o Google" sem filtro significa que
  toda conta Google do planeta entra. Informe ao menos um domínio ou um endereço, ou o painel
  recusa ligar o acesso federado.

---

## OpenID Connect com o Google Workspace, do começo ao fim

1. No console do Google Cloud, abra **APIs e serviços → Credenciais** e crie um
   **ID de cliente OAuth** do tipo *Aplicativo da Web*.
2. Em *URIs de redirecionamento autorizados*, cole o endereço de retorno que o painel mostra na
   tela de configuração. É a sua origem pública seguida de `/sso/oidc/callback`, e nada além
   disso:

   ```
   https://painel.exemplo.com/sso/oidc/callback
   ```

3. O Google devolve um **Client ID** e um **segredo do cliente**. Copie os dois.
4. De volta ao painel, na aba OpenID Connect:

   | Campo | Valor |
   | --- | --- |
   | Endereço público deste painel | `https://painel.exemplo.com` |
   | Emissor | `https://accounts.google.com` |
   | Identificador do cliente | o que o Google deu |
   | Segredo do cliente | o que o Google deu |
   | Escopos | `openid email profile` |
   | Domínios autorizados | `exemplo.com` |

5. Digite a senha do painel e salve. Saia, e a tela de login passa a ter o botão
   **Entrar com accounts.google.com** ao lado do formulário de senha.

O mesmo formato serve para qualquer provedor que publique um documento de descoberta — Microsoft
Entra ID, Okta, Authentik, Keycloak. Só o emissor muda; o Entra ID, por exemplo, usa
`https://login.microsoftonline.com/<seu tenant>/v2.0`.

### O segredo nunca volta para a tela

O segredo do cliente é gravado num arquivo com modo `0600` dentro do `DATA_DIR`, ao lado da
credencial de recuperação. Nunca vai para o banco de preferências, nunca vai para o log, nunca
entra numa página.

A tela mostra `••••••••` e um campo para substituí-lo. **Salvar com esse campo em branco mantém o
segredo que já existe** — o campo nasce vazio a cada visita justamente porque nunca reexibe o valor
guardado, e apagar o segredo por causa disso desligaria o acesso federado em silêncio.

Ele também pode vir do ambiente, em `OIDC_CLIENT_SECRET`. O ambiente vence o arquivo, e quando é
ele quem manda a tela mostra o campo travado, como `DASHBOARD_PASSWORD` já se comporta.

---

## SAML 2.0

A aba de SAML existe e aparece **desabilitada na imagem publicada**, com uma mensagem dizendo isso.

O motivo honesto: a validação da assinatura SAML não sai com segurança usando só a biblioteca
padrão. O `xml.etree` canonicaliza em C14N 2.0 e o SAML assina sobre canonicalização exclusiva 1.0,
então assinaturas corretas pareceriam inválidas; não há verificação RSA na biblioteca padrão; e
defender-se de XML Signature Wrapping exige amarrar a referência da assinatura ao elemento que foi
efetivamente lido, o que é trabalho de biblioteca, não de expressão regular.

Por isso o SAML depende da `python3-saml`, declarada como extra opcional no `pyproject.toml`. Com a
biblioteca instalada, o painel serve a descrição do serviço em `/sso/saml/metadata` — atrás de
login, de propósito — para você entregar ao provedor de identidade. Configure o provedor para
**assinar a asserção e não criptografá-la**: asserção criptografada está fora de escopo, porque
exigiria uma chave privada do painel, ou seja, mais um segredo para guardar.

### Construindo uma imagem que tenha a biblioteca

A imagem publicada não traz a biblioteca, e o `Dockerfile` do projeto está inalterado. Para
construir a sua, acrescente a ele:

```dockerfile
RUN apk add --no-cache libxml2 libxslt xmlsec && \
    apk add --no-cache --virtual .build gcc musl-dev libxml2-dev libxslt-dev xmlsec-dev pkgconfig && \
    pip install --no-cache-dir --no-binary lxml,xmlsec python3-saml && \
    apk del .build
```

Compilar a partir do fonte não é opcional. Os pacotes prontos de `lxml` e de `xmlsec` embutem cada
um a sua cópia da `libxml2`, em versões diferentes, e o resultado é um erro de incompatibilidade de
versão na hora do import. Compilar os dois contra a biblioteca do sistema é o que resolve; as
ferramentas de compilação podem sair depois, e o tempo de execução continua funcionando só com as
três bibliotecas compartilhadas.

---

## Quando o provedor de identidade cai

Quatro saídas independentes, na ordem em que se deve tentar:

1. **O formulário local não sai da tela.** Usuário e senha continuam entrando, com o provedor vivo
   ou morto.
2. **A credencial de recuperação sempre entra.** Ela é de emergência e continua válida depois de a
   senha ser definida — veja [Authentication](Authentication).
3. **`SSO_DISABLED=1` no ambiente** vence o banco sem tocar nele. Recrie o container com a variável
   e a tela de login volta ao formulário puro.
4. **A porta que não é HTML fica intacta.** `curl`, cron e monitoramento continuam com Basic Auth e
   nunca passam pelo acesso federado.

```bash
docker compose -f docker-compose.example.yml up -d --force-recreate litellmrtk-sync
```

---

## O que é conferido antes de emitir sessão

Nesta ordem, parando na primeira falha, e sempre com a mesma mensagem genérica na tela —
distinguir "state errado" de "endereço fora da lista" conta ao atacante até onde ele chegou:

1. o mesmo teto por endereço do formulário de login, respondendo **429** com `Retry-After`;
2. o cookie de estado está presente, íntegro e ainda não foi gasto;
3. o `state` da query bate com o do cookie, comparado em tempo constante;
4. o código é trocado no ponto de tokens com prova de chave, sempre S256;
5. o token de identidade diz vir do emissor configurado, carrega a nossa audiência, está dentro da
   validade e repete o número de uso único que enviamos;
6. o ponto de informações do usuário confirma o mesmo sujeito e que o endereço foi verificado;
7. o endereço está na lista de autorizados.

Só então o painel emite **o mesmo cookie assinado que o formulário de senha emite** — não existe
uma segunda forma de sessão. O pouso é uma página simples com atualização para `/`; nenhum
parâmetro vindo do provedor vira destino.

| Rota | Sessão | Para que serve |
| --- | --- | --- |
| `/sso/oidc/iniciar` | pública | manda o navegador ao provedor |
| `/sso/oidc/callback` | pública | o provedor manda o navegador de volta |
| `/sso/saml/iniciar` | pública | o mesmo, para SAML |
| `/sso/saml/acs` | pública | onde o provedor entrega a asserção |
| `/sso/saml/metadata` | exigida | descrição do serviço, baixada autenticado |
| `/acoes/sso` (POST) | exigida, mais a senha local | grava a configuração |

As quatro rotas públicas são públicas porque a ida e a volta ao provedor acontecem antes de existir
sessão — criar uma é exatamente para o que elas servem. Mesmo assim respondem 404 enquanto o acesso
federado estiver desligado.

---

## Fora de escopo, declarado

- **Saída federada.** Sair apaga apenas o cookie local. A sessão no provedor continua aberta, então
  o clique seguinte no botão de entrada volta sem pedir nada. Isso é comportamento, não defeito.
- **Asserção SAML criptografada**, pelo motivo acima.
- **Requisição de autenticação assinada** ao provedor: quase nenhum provedor exige, e uma delas
  custaria mais uma chave privada para guardar.

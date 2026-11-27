# Início rápido para agentes

[English](agent-tools.en.md) · [Español](agent-tools.es.md)

Execute os comandos na raiz do repositório com [Node.js 24+](https://nodejs.org/pt/download) e o `npm` e `npx` que vêm com ele. Os kits rodam via `npx`, sem instalação global. Para usar os modelos, tenha o OpenCode 2 disponível.

## Selecionar agentes com o AgentDevKit

```console
npx --yes --ignore-scripts @pathbit/agentdevkit@0.0.42 init
```

No primeiro uso, escolha o idioma e o objetivo da configuração. Use as setas para navegar, **Espaço** para marcar os clientes e **Enter** para confirmar. O repositório inclui as referências do Codex e os recursos compartilhados em `.pbai4devs`. A seleção de clientes fica no checkout local.

Para mudar a seleção depois da configuração inicial, execute o primeiro comando abaixo. Confira a configuração com `doctor`. Na primeira configuração, use `@latest` para executar a versão estável atual ou fixe o número para repetir a configuração com a versão validada.

```console
npx --yes --ignore-scripts @pathbit/agentdevkit@0.0.42 agents --select
npx --yes --ignore-scripts @pathbit/agentdevkit@0.0.42 doctor
npx --yes --ignore-scripts @pathbit/agentdevkit@latest init
```

## Configurar modelos com o AgentModelKit

O exemplo configura os modelos do OpenCode Console no projeto atual. Mantenha `OPENCODE_API_KEY` no ambiente do OpenCode. A CLI grava somente a referência à variável, sem guardar a chave. Para ver o plano primeiro, acrescente `--dry-run` ao comando `npx` abaixo.

```console
npx --yes --ignore-scripts @pathbit/agentmodelkit@0.0.3 add --providers opencode --tiers payg --targets opencode --project-dir . --no-save-key --yes
```

Execute `opencode models --standalone` na raiz do repositório para conferir os modelos. No OpenCode, use `/models` para escolher um deles. A configuração e os backups do ModelKit em `.opencode` ficam no checkout local.

Com `OPENCODE_API_KEY` no ambiente, use o assistente para escolher o plano do OpenCode e os modelos no terminal. Troque `@latest` por `@0.0.3` para repetir essa seleção com a versão validada.

```console
npx --yes --ignore-scripts @pathbit/agentmodelkit@latest opencode --targets opencode --project-dir . --no-save-key
```

Pacotes no npmjs. [AgentDevKit](https://www.npmjs.com/package/@pathbit/agentdevkit) · [AgentModelKit](https://www.npmjs.com/package/@pathbit/agentmodelkit)

Guias completos. [AgentDevKit](https://github.com/pathbit/AgentDevKit/blob/main/README.md) · [AgentModelKit](https://github.com/pathbit/AgentModelKit/blob/main/README.md)

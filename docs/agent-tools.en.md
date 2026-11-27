# Agent tools quick start

[Português do Brasil](agent-tools.md) · [Español](agent-tools.es.md)

Run these commands from the repository root with [Node.js 24+](https://nodejs.org/en/download) and its bundled `npm` and `npx`. The kits run via `npx` without a global install. OpenCode 2 is required to use the configured models.

## Select agents with AgentDevKit

```console
npx --yes --ignore-scripts @pathbit/agentdevkit@0.0.42 init
```

On first use, choose the language and setup purpose. Use the arrow keys to navigate, **Space** to select clients, and **Enter** to confirm. The repository includes Codex references and shared resources in `.pbai4devs`. Your client selection stays in the local checkout.

To change the selection after initial setup, run the first command below. Run `doctor` to check the setup. For initial setup, use `@latest` for the current stable release or pin the version to reproduce the tested setup.

```console
npx --yes --ignore-scripts @pathbit/agentdevkit@0.0.42 agents --select
npx --yes --ignore-scripts @pathbit/agentdevkit@0.0.42 doctor
npx --yes --ignore-scripts @pathbit/agentdevkit@latest init
```

## Configure models with AgentModelKit

This example configures OpenCode Console models for the current project. Keep `OPENCODE_API_KEY` in the OpenCode environment. The CLI writes the variable reference and leaves the key in the environment. Add `--dry-run` to the `npx` command below to preview the changes.

```console
npx --yes --ignore-scripts @pathbit/agentmodelkit@0.0.3 add --providers opencode --tiers payg --targets opencode --project-dir . --no-save-key --yes
```

Run `opencode models --standalone` from the repository root to check the model list. In OpenCode, use `/models` to select one. The ModelKit configuration and backups in `.opencode` stay in the local checkout.

With `OPENCODE_API_KEY` in the environment, run the wizard to select an OpenCode plan and models in the terminal. Replace `@latest` with `@0.0.3` to repeat the selection with the tested version.

```console
npx --yes --ignore-scripts @pathbit/agentmodelkit@latest opencode --targets opencode --project-dir . --no-save-key
```

Packages on npmjs. [AgentDevKit](https://www.npmjs.com/package/@pathbit/agentdevkit) · [AgentModelKit](https://www.npmjs.com/package/@pathbit/agentmodelkit)

Full guides. [AgentDevKit](https://github.com/pathbit/AgentDevKit/blob/main/README.en.md) · [AgentModelKit](https://github.com/pathbit/AgentModelKit/blob/main/README.en.md)

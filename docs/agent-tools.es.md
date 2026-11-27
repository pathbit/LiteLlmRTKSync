# Inicio rápido para agentes

[Português do Brasil](agent-tools.md) · [English](agent-tools.en.md)

Ejecuta los comandos desde la raíz del repositorio con [Node.js 24+](https://nodejs.org/es/download) y con `npm` y `npx` incluidos. Los kits se ejecutan con `npx` sin instalación global. Para usar los modelos configurados, necesitas OpenCode 2.

## Seleccionar agentes con AgentDevKit

```console
npx --yes --ignore-scripts @pathbit/agentdevkit@0.0.42 init
```

En el primer uso, elige el idioma y el objetivo de la configuración. Usa las flechas para navegar, **Espacio** para seleccionar clientes y **Enter** para confirmar. El repositorio incluye las referencias de Codex y los recursos compartidos en `.pbai4devs`. La selección de clientes se guarda en la copia local.

Para cambiar la selección después de la configuración inicial, ejecuta el primer comando de abajo. Ejecuta `doctor` para comprobar la configuración. En la configuración inicial, usa `@latest` para ejecutar la versión estable actual o indica el número para reproducir la configuración con la versión probada.

```console
npx --yes --ignore-scripts @pathbit/agentdevkit@0.0.42 agents --select
npx --yes --ignore-scripts @pathbit/agentdevkit@0.0.42 doctor
npx --yes --ignore-scripts @pathbit/agentdevkit@latest init
```

## Configurar modelos con AgentModelKit

El ejemplo configura los modelos de OpenCode Console para el proyecto actual. Mantén `OPENCODE_API_KEY` en el entorno de OpenCode. La CLI guarda la referencia a la variable y la clave permanece en el entorno. Añade `--dry-run` al comando `npx` de abajo para ver los cambios previstos.

```console
npx --yes --ignore-scripts @pathbit/agentmodelkit@0.0.3 add --providers opencode --tiers payg --targets opencode --project-dir . --no-save-key --yes
```

Ejecuta `opencode models --standalone` desde la raíz del repositorio para comprobar la lista de modelos. En OpenCode, usa `/models` para elegir uno. La configuración y las copias de seguridad de ModelKit en `.opencode` se guardan en la copia local.

Con `OPENCODE_API_KEY` en el entorno, ejecuta el asistente para elegir un plan de OpenCode y los modelos en la terminal. Sustituye `@latest` por `@0.0.3` para repetir la selección con la versión probada.

```console
npx --yes --ignore-scripts @pathbit/agentmodelkit@latest opencode --targets opencode --project-dir . --no-save-key
```

Paquetes en npmjs. [AgentDevKit](https://www.npmjs.com/package/@pathbit/agentdevkit) · [AgentModelKit](https://www.npmjs.com/package/@pathbit/agentmodelkit)

Guías completas. [AgentDevKit](https://github.com/pathbit/AgentDevKit/blob/main/README.es.md) · [AgentModelKit](https://github.com/pathbit/AgentModelKit/blob/main/README.es.md)

# Spec — Fase 6: Agente LangChain

> Sugerido guardar como `docs/fase-6-agente.md`. Depende de la Fase 5
> (servidor MCP).

## 1. Objetivo de la fase

Un agente que usa las herramientas de solo lectura del servidor MCP
(Fase 5) para diagnosticar anomalías, y cuando corresponde, llama a una
herramienta de propuesta de acción — nunca ejecuta nada directamente
(esa garantía ya la da el diseño de la Fase 5, acá solo hay que no
romperla con el system prompt).

## 2. Decisiones de diseño

### 2.1 LangGraph en vez de agentes legacy de LangChain

Usar LangGraph (el motor de grafos de LangChain) en vez de los agentes
"clásicos" — es lo que la documentación actual de
`langchain-mcp-adapters` recomienda, y da control explícito sobre el
flujo de razonamiento, útil para loggear cada paso (importante acá:
vas a querer poder auditar por qué el agente propuso algo).

### 2.2 Conexión al MCP server

`langchain-mcp-adapters` (`pip install langchain-mcp-adapters`) provee
`MultiServerMCPClient`: se conecta al servidor MCP de la Fase 5 y
convierte sus herramientas en objetos `BaseTool` de LangChain
automáticamente. No reinventar este cliente a mano.

### 2.3 System prompt — el límite más importante de esta fase

Dejar explícito en el prompt del agente:
- Su rol: diagnosticar anomalías usando las herramientas de solo lectura
  disponibles.
- Puede "proponer" una acción de remediación llamando a las herramientas
  de propuesta — pero **nunca debe afirmar que ejecutó algo**, siempre
  enmarcarlo como "propuesta pendiente de confirmación".
- Si no hay una acción de remediación clara o segura para la situación,
  está bien no proponer nada y solo explicar.

### 2.4 Modelo de LLM

Un modelo económico/rápido alcanza para este caso de uso — no hace falta
el más grande. Dejar el nombre del modelo como variable de config, no
hardcodeado. La API key del proveedor que uses (OpenAI, Anthropic, etc.)
se suma a la lista de secrets a manejar en la Fase 9.

### 2.5 Memoria entre turnos

Para el MVP, cada consulta es independiente — no hace falta memoria
persistente de conversación. Si más adelante se quiere un chat con
contexto de turnos previos, es una extensión, no bloqueante ahora.

## 3. Puntos de entrada

- **`diagnose_alert(alert_id)`** — función interna: arma el contexto de
  una alerta ya persistida (Fase 4) y corre el agente para producir una
  explicación + posible propuesta. Este es el que va a llamar el
  scheduler de la Fase 7 automáticamente ante cada alerta nueva.
- **`POST /agent/query`** — endpoint para preguntas libres en lenguaje
  natural (ej. *"¿qué pasó a las 3am?"*), para el chat interactivo de la
  Fase 8.

## 4. Estructura de archivos nuevos

```
src/predictive_monitoring_tool/
└── agent/
    ├── __init__.py
    ├── graph.py       # arma el grafo LangGraph con las tools del MCP
    ├── prompts.py       # system prompt del agente
    └── service.py        # diagnose_alert(), answer_query()
tests/
└── test_agent.py
```

## 5. Definition of Done

- [ ] Agente conectado al servidor MCP de la Fase 5 vía
      `MultiServerMCPClient`
- [ ] System prompt deja explícito el límite: proponer, nunca afirmar
      ejecución
- [ ] `diagnose_alert(alert_id)` produce explicación + propuesta (si
      corresponde) para una alerta persistida
- [ ] `POST /agent/query` responde preguntas libres usando las
      herramientas de solo lectura
- [ ] Test con un prompt adversarial que intenta hacer que el agente
      "confirme" una ejecución — verificar que ni siquiera lo simula en
      el texto de su respuesta (no tiene la capacidad, pero vale la pena
      que tampoco lo aparente)
- [ ] Modelo de LLM configurable (no hardcodeado), con timeout razonable
- [ ] README con un ejemplo de consulta y la respuesta del agente

## 6. Fuera de alcance en esta fase

No tocar: la UI (Fase 8), el scheduler que dispara diagnósticos
automáticos (Fase 7 — acá se construye la función que el scheduler va a
llamar, no el disparo periódico en sí).

## 7. Notas para el agente (Claude Code)

- `pip install langchain-mcp-adapters` — no armar el cliente MCP a mano.
- LangGraph, no agentes legacy de LangChain.
- Mismas convenciones que fases anteriores: Python 3.14, type hints,
  docstrings en inglés.

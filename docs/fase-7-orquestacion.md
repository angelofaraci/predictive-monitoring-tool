# Spec — Fase 7: Orquestación

> Sugerido guardar como `docs/fase-7-orquestacion.md`. Depende de las
> Fases 4 (API), 5 (MCP) y 6 (agente).

## 1. Objetivo de la fase

Cerrar el círculo automático en **modo real**: poll periódico a
Prometheus → ingest → si hay anomalía, diagnóstico automático del agente
→ todo persistido, sin intervención manual. En modo demo no aplica —
ese modo se sigue disparando a demanda desde la UI (Fase 8).

## 2. Decisión de diseño: ¿dónde vive el scheduler?

Dos opciones:

- **Loop in-process** (un `asyncio` task que arranca con la app y hace
  `sleep(interval)` entre ciclos): cero infraestructura nueva, más
  simple.
- **Azure Container Apps Job con cron trigger**: un recurso separado,
  desacoplado del API, que se dispara solo en el horario configurado.

**Recomendación para esta fase: el loop in-process.** Es más simple y no
agrega un recurso de infra nuevo todavía. Pero hay que documentar la
limitación explícitamente: **si el Container App escala a cero (sin
tráfico), el loop deja de correr con él** — el monitoreo se detiene
hasta que llegue una request y lo despierte. Migrar a un Container Apps
Job (desacoplado de la escala del API) es el ajuste correcto si esto
importa de verdad, y queda anotado para la Fase 9 — no se implementa
ahora.

## 3. Alcance funcional

### 3.1 Loop de polling (modo real)

- Intervalo configurable (`POLL_INTERVAL_SECONDS`, default razonable,
  ej. 60s) — nunca menor a la ventana mínima de historial definida en la
  Fase 3.5.
- Cada ciclo llama a la lógica interna de `/ingest` en modo real
  (reusar la función, no un loopback HTTP innecesario).
- Si Prometheus no responde: loggear y seguir con el próximo ciclo —
  **nunca crashear el proceso** por una falla transitoria de conexión.

### 3.2 Disparo automático del diagnóstico

Cuando el ingest persiste una alerta nueva (`is_anomaly=True`), llamar
automáticamente a `diagnose_alert(alert_id)` (Fase 6) y guardar la
explicación + propuesta (si la hay) junto a la alerta. Esto corre como
tarea en background — **no bloquear el ciclo de polling** esperando la
respuesta del LLM.

### 3.3 Deduplicación / cooldown de alertas

Para evitar "alert fatigue": si ya existe una alerta reciente sin
resolver para el mismo tipo de anomalía, dentro de una ventana de
cooldown configurable (ej. 15 min), **no crear una alerta nueva ni
volver a disparar el agente**. Sin esto, una sola fuga de memoria de
20 minutos generaría 20 alertas idénticas y 20 diagnósticos redundantes
del agente (y 20 llamadas al LLM pagadas de más).

Criterio de "mismo tipo de anomalía": puede ser simple — el `scenario`
en modo demo, o la métrica que más contribuyó a la anomalía en modo
real. No hace falta sofisticación acá.

### 3.4 Cambios de schema

La tabla `alerts` (Fase 4) suma dos columnas:
- `diagnosis` (texto, nullable) — la explicación del agente
- `proposal_id` (nullable, referencia a la tabla `proposals` de la
  Fase 5) — si el agente propuso algo

## 4. Estructura de archivos nuevos

```
src/predictive_monitoring_tool/
└── orchestration/
    ├── __init__.py
    └── scheduler.py     # loop de polling, cooldown, disparo del agente
tests/
└── test_scheduler.py
```

## 5. Definition of Done

- [ ] Loop de polling corriendo en modo real, con intervalo configurable
- [ ] Manejo de fallas de conexión a Prometheus sin crashear el proceso
- [ ] Cooldown implementado — test que verifica que una anomalía
      persistente no genera alertas ni diagnósticos duplicados
- [ ] Diagnóstico automático disparado al crear una alerta nueva,
      guardado junto a la alerta (`diagnosis`, `proposal_id`)
- [ ] Limitación de scale-to-zero documentada en el README, con la
      migración a Container Apps Jobs anotada para la Fase 9
- [ ] Test de integración de punta a punta: simular una anomalía y
      verificar que poll → ingest → alerta → diagnóstico corre solo,
      sin intervención manual

## 6. Fuera de alcance en esta fase

No tocar: UI (Fase 8). La migración real a Container Apps Jobs queda
anotada para la Fase 9, no se implementa ahora.

## 7. Notas para el agente

- No esperar sincrónicamente al LLM dentro del loop de polling —
  dispararlo aparte para no bloquear el ciclo siguiente.
- Mismas convenciones que fases anteriores: Python 3.14, type hints,
  docstrings en inglés.

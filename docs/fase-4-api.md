# Spec — Fase 4: Servicio FastAPI

> Se agrega sobre la app que ya quedó desplegada en la Fase 2.5 (que solo
> tenía `GET /health`). Depende de las decisiones cerradas en la Fase 3.5.

## 1. Objetivo de la fase

Exponer el modelo entrenado en la Fase 3 vía HTTP, con un endpoint de
inferencia stateless (`/predict`), uno que orquesta el pipeline completo
desde la fuente de datos configurada (`/ingest`), y uno que devuelve el
historial de anomalías detectadas (`/alerts`).

## 2. Endpoints

### 2.1 `GET /health`

Ya existe desde la Fase 2.5. No se toca en esta fase.

### 2.2 `POST /predict` — inferencia stateless

- **Body**: ventana de lecturas crudas con timestamp (mínimo la ventana
  más larga configurada de historial, según lo definido en la Fase 3.5,
  sección 3.3).
- **Proceso**: valida que haya suficiente historial → `build_features()`
  (Fase 2) → modelo cargado en memoria (Fase 3) → score.
- **Respuesta**: `is_anomaly` (bool), `anomaly_score` (float, score
  continuo — no solo el booleano, según quedó definido en la Fase 3),
  y opcionalmente las features usadas (útil para debug).
- **Si falta historial**: `422` con mensaje claro (ej. *"faltan 6 minutos
  de historial para calcular las features de 15min"*), nunca un `NaN`
  silencioso.

No persiste nada — es una función pura sobre la entrada que recibe.

### 2.3 `POST /ingest` — corre el pipeline completo

- **Body opcional**: `{"mode": "demo", "scenario": "memory_leak"}` para
  modo demo, o vacío para modo real (usa la configuración de Prometheus
  de la Fase 1.6 si existe).
- **Proceso**: obtiene los datos crudos (`fetch_metrics()` en modo real,
  `generate()` en modo demo) → reusa la misma lógica interna que
  `/predict` → si detecta anomalía, persiste un registro.
- Este es el endpoint que va a llamar el scheduler de la Fase 7 en modo
  real (periódicamente), y el botón "simular escenario" del dashboard en
  modo demo (bajo demanda).

### 2.4 `GET /alerts` — historial

- Devuelve las alertas persistidas, más recientes primero, con un límite
  configurable (`?limit=50`). Sin filtros avanzados por ahora — no hace
  falta para el alcance actual.

## 3. Persistencia

SQLite vía el módulo `sqlite3` de la stdlib, sin ORM — no hace falta esa
capa extra para esta escala. Un archivo `alerts.db` con una tabla
`alerts` (timestamp, source, scenario, is_anomaly, anomaly_score).

> **Nota importante**: en Azure Container Apps el storage del contenedor
> es efímero por defecto — este archivo se resetea en cada redeploy salvo
> que se monte un volumen persistente. Eso se resuelve en la Fase 9
> (endurecimiento de infra), no ahora. Para esta fase, alcanza con que
> funcione durante la vida del contenedor.

## 4. Estructura de archivos nuevos

```
src/predictive_monitoring_tool/
└── api/
    ├── __init__.py
    ├── main.py          # ya existe (fase 2.5) — se le agregan los endpoints
    ├── schemas.py        # modelos pydantic de request/response
    ├── inference.py       # predict_from_raw(): valida historial, features, modelo
    ├── ingestion.py        # orquesta fetch_metrics()/generate() → inference → storage
    └── storage.py          # SQLite: guardar/leer alertas
tests/
├── test_predict.py
├── test_ingest.py
└── test_alerts.py
```

## 5. Definition of Done

- [ ] `GET /health` sigue funcionando sin cambios
- [ ] `POST /predict`: valida historial mínimo, calcula features, devuelve
      predicción + score; `422` claro si falta historia
- [ ] `POST /ingest`: soporta modo demo (con escenario opcional) y modo
      real (usa la config de Prometheus si existe); persiste en SQLite
      cuando detecta anomalía
- [ ] `GET /alerts`: devuelve las alertas persistidas, más recientes
      primero, con límite configurable
- [ ] El modelo se carga **una sola vez** al arrancar el proceso (startup
      de FastAPI), no en cada request — importante para la latencia
- [ ] `/predict` e `/ingest` reusan la misma función interna de
      inferencia, sin duplicar lógica
- [ ] Tests de integración: predecir sobre datos sintéticos con y sin
      anomalía inyectada; ingest end-to-end en modo demo; alerts
      devuelve lo persistido correctamente
- [ ] README actualizado con los tres endpoints y ejemplos de uso (`curl`)

## 6. Fuera de alcance en esta fase

No tocar: MCP, agente, dashboard visual (Fase 8), persistencia robusta o
volúmenes en Azure (Fase 9). Tampoco implementar todavía nada de
"acciones" (reiniciar contenedor, liberar disco) — eso es Fase 5/6.

## 7. Notas para el agente

- Cargar el modelo (`joblib.load`) en el evento de startup de FastAPI, no
  dentro del handler de cada request.
- El path del modelo sale de la variable de entorno `MODEL_PATH` definida
  en la Fase 3.5 — no hardcodear el nombre del archivo.
- Usar pydantic para los schemas de request/response — aprovechar la
  validación automática y la documentación OpenAPI que genera FastAPI
  gratis a partir de eso.
- Mismas convenciones que fases anteriores: Python 3.14, type hints,
  docstrings en inglés.

## 8. Decisión adicional (cerrada antes de implementar)

`fetch_metrics()` (Fase 1.6, conexión Prometheus) **no existe todavía**
en el código ni en docs. Decisión: `POST /ingest` con `mode` ausente o
distinto de `"demo"` (es decir, modo real) devuelve `501 Not Implemented`
con un mensaje claro (ej. "real mode not available yet — Prometheus
connection (Phase 1.6) not implemented"). No se construye un
`fetch_metrics()` de relleno. Solo `mode: "demo"` es funcional en esta
fase.

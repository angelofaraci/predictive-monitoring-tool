# Spec del proyecto — AIOps predictivo (nombre tentativo: Argos)

> Este documento es el spec inicial para arrancar el proyecto con Claude Code
> siguiendo Spec-Driven Development. Contiene la visión completa del proyecto
> y el detalle accionable de la Fase 1. Las fases siguientes se detallarán en
> specs propios cuando llegue el momento (no hace falta resolverlas ahora).
>
> **Nota de esta revisión**: durante el ciclo de SDD de la Fase 1 se
> corrigieron dos cosas respecto al borrador original de este documento,
> ambas registradas como decisiones explícitas del usuario: (1) la versión
> de Python objetivo pasó de `3.11+` a `3.14` (verificado soporte de wheels
> en pandas/numpy/scikit-learn), y (2) la firma de `generate()` en 5.3 fue
> corregida a la forma que el usuario definió originalmente, después de que
> una fase intermedia de SDD la alterara sin autorización. El resto del
> documento se mantiene tal como fue escrito.

## 1. Visión general

Sistema de monitoreo predictivo de infraestructura: detecta anomalías en
métricas de sistema (CPU, memoria, disco, latencia) usando un modelo de
machine learning, y un agente conversacional explica qué pasó y qué se
podría hacer, usando sus propias herramientas expuestas vía MCP.

**Objetivos del proyecto:**
- Aprender e integrar de punta a punta: pandas, scikit-learn, FastAPI,
  LangChain, MCP y deploy en GCP.
- Producir un proyecto demostrable en el CV, con un demo público que
  cualquier persona pueda tocar sin acceso a infraestructura real.
- Reforzar el roadmap DevOps en curso (Docker, Terraform, CI/CD,
  Prometheus/Grafana) aplicándolo a un caso de uso real en vez de ejercicios
  aislados.

## 2. Arquitectura (resumen)

Tres capas secuenciales:

1. **Datos e ingesta** — un generador sintético produce métricas de sistema
   con estacionalidad y ruido realista, con la opción de inyectar escenarios
   de anomalía. Un pipeline en pandas limpia y genera features (ventanas
   móviles, lags).
2. **Detección y servicio** — un modelo scikit-learn (Isolation Forest)
   detecta anomalías sobre los features. Se sirve vía FastAPI
   (`/predict`, `/alerts`).
3. **Agente de diagnóstico** — un agente LangChain consume un servidor MCP
   propio (herramientas: historial de métricas, diagnóstico simulado,
   sugerencia de remediación) y devuelve una explicación en lenguaje natural.
   Alimenta un dashboard/chat de alertas.

Deploy: Docker → GitHub Actions (CI/CD) → GCP (Cloud Run, Terraform para
infra, Artifact Registry). Todo esto se aborda en fases posteriores.

## 3. Stack tecnológico

| Capa | Tecnología |
|---|---|
| Datos/features | pandas, numpy |
| Modelo | scikit-learn |
| API | FastAPI |
| Agente | LangChain |
| Herramientas del agente | MCP (servidor propio) |
| Infra/deploy | Docker, Terraform, GitHub Actions, GCP (Cloud Run) |
| Testing | pytest |
| Gestión de dependencias | uv |

## 4. Fases del proyecto (mapa completo, alto nivel)

1. **Setup + generador sintético** ← *implementado en este repo (PR1-PR3)*
2. Pipeline de features con pandas sobre los datos generados
3. Entrenamiento y evaluación del modelo scikit-learn
4. Servicio FastAPI (`/predict`, `/ingest`, `/alerts`)
5. Servidor MCP propio con herramientas de diagnóstico
6. Agente LangChain que consume el MCP server
7. Orquestación (scheduler + disparo de alertas)
8. Dashboard/demo interactivo (selector de escenarios)
9. Deploy en GCP (Docker, Terraform, GitHub Actions)
10. Pulido para portfolio (README, demo grabada, métricas de resultado)

Cada fase siguiente tendrá su propio spec cuando la empecemos. No hace falta
diseñarlas en detalle ahora.

---

## 5. FASE 1 — Setup + generador sintético (detalle accionable)

### 5.1 Objetivo de la fase

Dejar el repo inicializado y un generador de métricas sintéticas
funcionando, testeado y explorado en un notebook. Esta es la base de datos
que van a usar todas las fases siguientes (entrenamiento del modelo,
demo pública, etc.).

### 5.2 Estructura de carpetas propuesta

```
argos/
├── README.md
├── pyproject.toml
├── src/
│   └── argos/
│       ├── __init__.py
│       └── data/
│           ├── __init__.py
│           ├── generator.py      # motor de generación sintética
│           └── scenarios.py      # definición de escenarios de anomalía
├── tests/
│   └── test_generator.py
├── notebooks/
│   └── 01_exploracion_datos_sinteticos.ipynb
├── docs/
│   └── spec.md                   # este archivo
└── .github/
    └── workflows/                 # vacío, se usa en fase 9
```

Las carpetas de `models/`, `api/`, `agent/`, `mcp/` se crean recién cuando
lleguen esas fases, para no dejar código muerto dando vueltas.

### 5.3 Requisitos funcionales del generador

Métricas base a simular (una fila por timestamp):
- `cpu_pct` (0-100)
- `memory_pct` (0-100)
- `disk_pct` (0-100)
- `latency_ms` (positivo, con cola larga)
- `requests_per_sec` (positivo)

Comportamiento del modo "normal" (sin anomalía):
- Estacionalidad diaria simple (ej: más tráfico en horario laboral) usando
  una onda senoidal + ruido gaussiano.
- Valores acotados a rangos realistas (nunca negativos, nunca fuera de 0-100
  para los porcentajes).

Escenarios de anomalía a implementar (mínimo estos 4):
- `memory_leak`: memoria sube de forma monótona hasta acercarse al 100%.
- `cpu_spike`: pico abrupto y breve de CPU, vuelve a la normalidad.
- `disk_fill`: disco sube linealmente hasta llenarse.
- `service_down`: `requests_per_sec` y `latency_ms` caen/se disparan
  simulando una caída de servicio.

API pública (firma corregida durante el ciclo de SDD de esta fase — ver
nota al inicio del documento):

```python
def generate(
    duration_minutes: int,
    interval_seconds: int = 10,
    scenario: str | None = None,
    scenario_start_minute: int | None = None,
    anomaly_duration_minutes: int | None = None,
    start_time: datetime | pandas.Timestamp | None = None,
    seed: int | None = None,
) -> pandas.DataFrame:
    ...
```

- Sin `scenario`, genera solo datos normales.
- Con `scenario`, inyecta el patrón de anomalía a partir de
  `scenario_start_minute` (o en un punto aleatorio si no se especifica).
- `anomaly_duration_minutes` controla el largo de la ventana de anomalía
  (default: el valor por escenario definido en `scenarios.py`; `0` es un
  valor explícito válido y produce una ventana de largo cero, no el
  default del escenario).
- `start_time` ancla la estacionalidad diaria; por default es un epoch UTC
  fijo (`2024-01-01T00:00:00Z`), nunca la hora de reloj real, para que
  `seed` garantice reproducibilidad total.
- `seed` para reproducibilidad determinística en tests (vía
  `numpy.random.Generator`, no el estado global legado de `numpy.random`).

Validación fail-fast: `generate()` lanza `ValueError` (nunca recorta ni
corrige en silencio) si `scenario_start_minute + anomaly_duration_minutes`
supera `duration_minutes`, o si `duration_minutes * 60` no es divisible por
`interval_seconds`.

### 5.4 Definition of Done

- [x] Repo inicializado con la estructura de carpetas de arriba
- [x] `pyproject.toml` con dependencias base (pandas, numpy) y dev
      (pytest, ruff), Python `>=3.14`
- [x] `generator.py` implementa `generate()` con modo normal + los 4
      escenarios de anomalía
- [x] `scenarios.py` separa la definición de cada escenario del motor base
      (fácil agregar un escenario nuevo sin tocar `generator.py`)
- [x] Tests unitarios: datos normales están dentro de rango esperado; cada
      escenario produce efectivamente el patrón esperado (ej: en
      `memory_leak`, `memory_pct` es monótonamente creciente durante la
      ventana de anomalía)
- [x] Notebook que grafica cada escenario para inspección visual
- [x] README con instrucciones de instalación (`uv sync`) y un ejemplo de
      uso de `generate()`

### 5.5 Fuera de alcance en esta fase

No se tocó en fase 1: modelo de ML, FastAPI, agente, MCP, Docker, deploy.
Quedan para su fase correspondiente.

### 5.6 Notas para el agente

- Python 3.14, gestión de dependencias con `uv`.
- Type hints en todas las funciones públicas.
- Docstrings y nombres de variables en inglés (estándar para un repo de
  portfolio internacional); el README va bilingüe.
- Tests con `pytest`, TDD estricto (test antes que implementación).
- Timestamps: `pandas.Timestamp` con timezone UTC (decisión tomada durante
  el ciclo de SDD). RNG: `numpy.random.Generator` vía
  `np.random.default_rng(seed)` (decisión tomada durante el ciclo de SDD).

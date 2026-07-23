# Spec — Fase 2: Pipeline de features con pandas

## 1. Objetivo de la fase

Transformar las métricas crudas que produce `generate()` (Fase 1) en un dataset de features listo para entrenar el modelo de detección de anomalías en la Fase 3. La Fase 2 es puramente de datos: no toca modelo, API, agente ni deploy.

## 2. Prerrequisito — ajuste chico a la Fase 1

Para poder testear que el pipeline de features "reacciona" bien durante una anomalía, y para poder evaluar el modelo más adelante, el generador necesita exponer la verdad de terreno. Si `generate()` todavía no lo hace, agregar antes de seguir:

- `is_anomaly` (bool): `True` en las filas dentro de la ventana de anomalía inyectada, `False` en el resto.
- `scenario` (str | None): nombre del escenario activo en esa fila (`"memory_leak"`, `"cpu_spike"`, etc.), `None` si no hay anomalía.

Es un cambio menor sobre `generator.py`, no hace falta reabrir toda la Fase 1.

## 3. Alcance funcional

### 3.1 Features de ventana móvil (rolling)

Para cada métrica cruda (`cpu_pct`, `memory_pct`, `disk_pct`, `latency_ms`, `requests_per_sec`), calcular sobre al menos dos ventanas (ej: 5min y 15min):

- media móvil
- desvío estándar móvil

### 3.2 Features de lag

Valor de cada métrica en `t-1` y `t-5` (en términos de filas/muestras, no de tiempo de reloj).

### 3.3 Features de variación

Diferencia de primer orden (`diff`) por métrica — capta la velocidad de cambio, útil para detectar spikes bruscos vs. subidas graduales.

### 3.4 Features temporales

A partir del timestamp: `hour`, `day_of_week`, `is_business_hours` (bool). Sirven para que el modelo distinga "es de noche, tráfico bajo es normal" de "tráfico bajo a media mañana es raro".

### 3.5 Manejo de NaNs

Las ventanas rolling y los lags generan NaN en las primeras filas (warm-up). Política: descartar esas filas en vez de rellenarlas — inventar valores placeholder podría confundirse con datos reales durante el entrenamiento del modelo.

### 3.6 Interfaz esperada

```python
def build_features(
    df: pandas.DataFrame,
    windows: list[str] = ["5min", "15min"],
) -> pandas.DataFrame:
    ...
```

Recibe el DataFrame crudo de `generate()` (con `is_anomaly`/`scenario` si ya se agregó el prerrequisito).

Devuelve un DataFrame con las columnas originales + todas las features nuevas, sin NaNs, y propagando `is_anomaly`/`scenario` sin modificar (el modelo de Fase 3 los va a necesitar para evaluación, aunque Isolation Forest sea no supervisado).

## 4. Estructura de archivos nuevos

```
src/predictive_monitoring_tool/
└── data/
    ├── generator.py      # fase 1
    ├── scenarios.py       # fase 1
    └── features.py        # ← nuevo, fase 2
tests/
└── test_features.py       # ← nuevo
notebooks/
└── 02_feature_engineering.ipynb   # ← nuevo
```

## 5. Definition of Done

- [ ] Prerrequisito de la sección 2 resuelto (`is_anomaly`/`scenario` en `generate()`)
- [ ] `features.py` implementa `build_features()` con rolling, lags, diff y features temporales
- [ ] Política de NaN aplicada (drop de filas incompletas) y documentada en un docstring
- [ ] Tests unitarios:
  - shape/columnas esperadas en la salida
  - no quedan NaNs en el resultado
  - durante una ventana de `memory_leak`, la media móvil de `memory_pct` es creciente (valida que el feature realmente capta la señal)
- [ ] Notebook 02 grafica, para al menos un escenario, la métrica cruda vs. su media móvil, marcando la ventana de anomalía
- [ ] README actualizado con una sección corta de "Feature engineering"

## 6. Fuera de alcance en esta fase

No tocar: modelo de ML (Fase 3), FastAPI, agente, MCP, deploy. Si surge la tentación de ya probar un modelo con estas features, anotarlo como siguiente paso pero no implementarlo todavía.

## 7. Notas para el agente

- Mantener `build_features()` como función pura (no lee ni escribe archivos) para que la Fase 3 la pueda importar y encadenar directo después de `generate()`.
- Usar operaciones vectorizadas de pandas (`.rolling()`, `.shift()`, `.diff()`) — nada de loops fila por fila, tanto por performance como porque es lo idiomático en pandas.
- Mismas convenciones que en Fase 1: type hints, docstrings en inglés, Python 3.14, `scikit-learn>=1.9.0` ya fijado aunque esta fase no lo use todavía.

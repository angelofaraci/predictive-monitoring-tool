# Spec — Fase 1.6: Conexión guiada con Prometheus (modo real)

> Convive con la Fase 1 (modo demo) — no la reemplaza. No depende de la
> Fase 2 ni de la 2.5, se puede hacer en cualquier orden respecto a esas.

## 1. Objetivo de la fase

Que cualquiera que instale la herramienta pueda conectarla a su propio
Prometheus sin leer documentación externa: la app lo guía, valida la
conexión paso a paso, y si algo falla dice exactamente qué y por qué —
nunca un stack trace críptico ni un fallo silencioso.

## 2. Por qué esto importa

Esta es la diferencia entre "un script que funciona si sabés lo que estás
haciendo" y "una herramienta que alguien más puede instalar y usar". Dado
que el objetivo del proyecto es justamente ser útil para otro dev/sysadmin
y no solo una demo, la calidad de este onboarding es tan importante como
que el modelo detecte bien las anomalías.

## 3. Alcance funcional

### 3.1 Cliente HTTP de Prometheus

Módulo propio (sin librería de terceros — la API de Prometheus es HTTP +
JSON simple, y escribir el cliente es más instructivo):

```python
def fetch_metrics(
    prometheus_url: str,
    queries: dict[str, str],
    start: datetime,
    end: datetime,
    step: str = "15s",
) -> pandas.DataFrame:
    ...
```

Devuelve un DataFrame con las mismas columnas que ya produce el generador
de la Fase 1 (`cpu_pct`, `memory_pct`, `disk_pct`, etc.) — el resto del
pipeline (`build_features()`, el modelo) no necesita saber de dónde
vinieron los datos.

Queries PromQL por defecto (basadas en node_exporter, ampliamente usadas
en el dashboard "Node Exporter Full" de Grafana):
- CPU: `100 - (avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)`
- Memoria: `100 * (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))`
- Disco: `100 - ((node_filesystem_avail_bytes{fstype!="tmpfs"} * 100) / node_filesystem_size_bytes{fstype!="tmpfs"})`

Deben ser configurables (no hardcodeadas sin más), porque los nombres de
label pueden variar entre versiones/instalaciones de node_exporter.

### 3.2 Chequeo de conexión en 3 niveles

Función central de esta fase — `test_connection(url) -> ConnectionCheckResult`,
con un resultado estructurado (no solo `bool`) para que tanto un CLI como
una futura UI web puedan mostrar el mismo nivel de detalle:

1. **¿Responde la URL?** — `GET /-/healthy` (o `/api/v1/status/config` si
   esa no está expuesta). Si falla: *"No pudimos conectar a esa URL.
   ¿Prometheus está corriendo y es accesible desde donde corre esta
   app?"*
2. **¿Hay targets activos de node_exporter?** — `GET /api/v1/targets`,
   filtrar por job esperado y ver `health: "up"`. Si no hay: *"Nos
   conectamos a Prometheus, pero no encontramos ningún target de
   node_exporter activo. ¿Está corriendo y agregado a la config de
   Prometheus?"*
3. **¿Existen las métricas específicas que necesitamos?** — una query
   instantánea corta por cada métrica core. Si falta alguna: listarla
   por nombre en vez de un error genérico (ej: *"Conectado, pero no
   encontramos `node_filesystem_avail_bytes`. ¿Tu versión de
   node_exporter la expone con ese nombre?"*).

Si los tres niveles pasan: guardar la configuración y mostrar qué
métricas se encontraron.

### 3.3 Persistencia de la configuración

La URL de Prometheus (y cualquier override de las queries) se guarda una
vez validada — en un archivo de config simple o variables de entorno,
según lo que ya esté usando el resto de la app. No repetir el setup en
cada arranque.

### 3.4 Modo demo como red de contención

Si todavía no hay Prometheus configurado, la app debe ofrecer
explícitamente "Probar con datos de demo" (usa el generador de la Fase 1)
en vez de bloquear al usuario — así puede ver el producto funcionando
mientras termina de configurar su Prometheus real.

### 3.5 Indicador de estado persistente

Una vez conectado, el estado de la conexión debe quedar visible en todo
momento (no solo en el setup inicial) — ej. un indicador en el dashboard
con "última consulta exitosa hace X segundos", para que si Prometheus se
cae más adelante sea evidente y no un misterio.

## 4. Estructura de archivos nuevos

```
src/predictive_monitoring_tool/
└── data/
    ├── generator.py         # fase 1 (modo demo)
    ├── scenarios.py          # fase 1 (modo demo)
    ├── prometheus_client.py  # ← nuevo: fetch_metrics()
    └── connection_check.py   # ← nuevo: test_connection(), ConnectionCheckResult
tests/
├── test_prometheus_client.py
└── test_connection_check.py
```

## 5. Definition of Done

- [ ] `fetch_metrics()` devuelve un DataFrame con las mismas columnas que
      el generador de la Fase 1, a partir de queries PromQL configurables
- [ ] `test_connection()` implementa los 3 niveles de chequeo y devuelve
      un resultado estructurado con el detalle de cada uno
- [ ] Mensajes de error específicos y accionables para cada escenario de
      falla (URL inalcanzable, sin targets, métrica faltante)
- [ ] Configuración persistida tras una validación exitosa
- [ ] Modo demo disponible como alternativa explícita si no hay Prometheus
      configurado todavía
- [ ] Tests: `test_connection()` contra un Prometheus real de prueba
      (se puede levantar uno con Docker en el propio test) cubriendo al
      menos un caso de éxito y uno de falla por cada nivel
- [ ] README con instrucciones de cómo apuntar la herramienta a un
      Prometheus existente, paso a paso

## 6. Fuera de alcance en esta fase

No tocar: features (Fase 2), modelo (Fase 3), la UI visual completa del
dashboard (Fase 8) — acá solo se resuelve la lógica de conexión y su
resultado estructurado; cómo se ve en pantalla es de la fase de UI.

## 7. Notas para el agente

- `test_connection()` no debe lanzar excepciones para fallas esperables
  (URL inalcanzable, target caído) — debe devolver el resultado
  estructurado con el detalle, para que quien lo consuma decida cómo
  mostrarlo. Reservar las excepciones para errores de programación, no
  de configuración del usuario.
- Mismas convenciones que fases anteriores: Python 3.14, type hints,
  docstrings en inglés, mensajes de usuario (los que ve la persona) en
  español.

# P6-07 · Métricas base G1–G7

`calcular_metricas.py` lee los JSONL ya persistidos de P6-03 (observaciones),
P6-05 (eventos) y P6-06 (alertas), y escribe un reporte JSON con **una métrica
base por grupo G1–G7**. No ejecuta el detector, no reasigna zonas, no crea
capturas ni alertas y no modifica los artefactos de entrada.

El catálogo oficial permite elegir una métrica por grupo. Este documento
declara la selección, la fórmula, la población, la ventana, las exclusiones y
las ausencias de cada una. No se reclama el catálogo completo ni se presentan
estas métricas como medida de exactitud: describen el servicio y sus artefactos,
no aciertos contra etiquetas reales.

## Comando reproducible

Desde la raíz del repositorio, con la corrida local ya verificada:

```powershell
$runId = "p6-05-verificacion-20261113-222520"
.\.venv\Scripts\python.exe .\calcular_metricas.py `
  --observaciones ".\datos_locales\observaciones\$runId.jsonl" `
  --eventos ".\datos_locales\eventos\$runId.jsonl" `
  --alertas ".\datos_locales\alertas\$runId.jsonl" `
  --root . `
  --generated-at "2026-11-14T04:26:00+00:00" `
  --output ".\datos_locales\reportes\p6-07-$runId.json"
```

`--generated-at` fija el instante del reporte y hace reproducible G6; sin él se
usa el reloj UTC actual. G3 siempre mide el disco en el momento de ejecución, de
modo que su valor puede cambiar aunque se repita el comando. Sin `--output` el
reporte completo se imprime en la salida estándar. La escritura del archivo es
atómica y crea la carpeta destino. `datos_locales/` está ignorado por Git: el
reporte es un resultado local recalculable, no una fuente versionada.

El reporte declara `schema: "p6-07/1"`, `run_id`, `inputs` con ruta, SHA-256 y
número de registros de cada JSONL, `metric_ids`, `metricas` y `limitaciones`.
Cada métrica incluye `id`, `grupo`, `nombre`, `unidad`, `formula`, `poblacion`,
`ventana`, `exclusiones`, `ausencias`, `fuente`, `numerador`, `denominador`,
`denominador_explicito`, `zero_safe`, `valor`, `estado`, `evidencia` y
`timestamp`.

## Inventario de métricas base

| Grupo | Métrica base | Fórmula | Unidad | Fuente |
| --- | --- | --- | --- | --- |
| G1 · Salud y rendimiento | Tasa de errores técnicos de inferencia | `detector_errors / frames_sampled` | ratio | resumen de observaciones |
| G2 · Captura y procesamiento | Tasa de cuadros descartados por la aplicación | `frames_discarded / frames_received` | ratio | resumen de observaciones |
| G3 · Uso de recursos | Proporción de disco disponible | `free_bytes / total_bytes` | ratio | `shutil.disk_usage(root)` |
| G4 · Comportamiento de predicciones | Proporción de detecciones en zona `low` | `detecciones_low / detecciones_puntuadas` | ratio | `events[].zones` |
| G5 · Datos y trazabilidad | Cobertura de trazabilidad de eventos | `eventos_trazables / eventos_persistidos` | ratio | eventos |
| G6 · Dispositivos y fuentes | Antigüedad del último reporte de observación | `generated_at - max(received_at)` | segundos | observaciones |
| G7 · Alertas y recuperación | Tiempo medio de detección | `suma(segundos(generated_at - occurred_at)) / alertas_triggered_válidas` | segundos | alertas |

### G1 · Tasa de errores técnicos

- **Población:** cuadros muestreados que llegan al detector en la corrida.
- **Ventana:** corrida completa descrita por el resumen P6-03.
- **Exclusión:** los cuadros descartados por muestreo y los errores de lectura
  no entran al numerador ni al denominador.
- **Ausencia:** sin cuadros muestreados el denominador es cero y el valor es
  nulo; no significa que el servicio esté sano.

### G2 · Tasa de descartes del stream

- **Población:** todos los cuadros recibidos por la fuente.
- **Ventana:** corrida completa del resumen P6-03.
- **Exclusión:** no se mezclan descartes por muestreo con errores de lectura.
  El reporte conserva `frames_processed` como evidencia, pero no lo usa como
  denominador.
- **Ausencia:** sin cuadros recibidos el valor es nulo.

### G3 · Disco disponible

- **Población:** sistema de archivos que contiene `--root`.
- **Ventana:** instante de `generated_at`; es una medida instantánea.
- **Exclusión:** no mide CPU, RAM ni GPU. No predice agotamiento ni mide la
  latencia de escritura.
- **Ausencia:** si `shutil.disk_usage` falla, el valor es nulo y la causa queda
  en `ausencias`.

### G4 · Detecciones en zona `low`

- **Población:** detecciones puntuadas dentro de `events[].zones` de P6-05.
- **Ventana:** eventos de la corrida; no se unen corridas distintas.
- **Exclusión:** eventos sin puntuaciones o con error técnico no aportan zonas.
  Se consumen las zonas ya calculadas por P6-04 y no se reclasifican.
- **Ausencia:** sin detecciones puntuadas el valor es nulo. Una tasa alta
  describe la distribución observada, no demuestra un fallo del modelo.

### G5 · Cobertura de trazabilidad

- **Población:** eventos con `event_id`, `frame_id`, `observation_id`,
  `occurred_at`, `capture.path` y `capture.sha256` no vacíos.
- **Ventana:** corrida completa.
- **Exclusión:** aquí se validan referencias como campos no vacíos; la
  verificación criptográfica de las capturas corresponde a
  `consultar_eventos.py`/`consultar_alertas.py`.
- **Ausencia:** sin eventos persistidos el valor es nulo.

### G6 · Antigüedad del último reporte

- **Población:** observaciones P6-03 con `received_at` ISO 8601 parseable.
- **Ventana:** desde el último `received_at` hasta `generated_at`.
- **Exclusión:** no estima frecuencia ni salud de la fuente. Un solo reporte no
  demuestra continuidad.
- **Ausencia:** sin timestamps parseables el valor es nulo. El denominador
  unitario (`1`) hace explícito que es una medida en segundos, no una razón.

### G7 · Tiempo medio de detección

- **Población:** alertas `record: "alert"` de P6-06 con
  `occurrence_state: "triggered"`, `occurred_at` y `generated_at` ISO 8601
  parseables y `generated_at >= occurred_at`; el manifiesto se excluye.
- **Fórmula:** `suma(segundos(generated_at - occurred_at)) / alertas_triggered_válidas`.
  El numerador suma segundos, el denominador cuenta alertas válidas y el valor
  es nulo cuando no hay ninguna.
- **Ventana:** desde `occurred_at` del evento primario hasta `generated_at` del
  lote que produjo la alerta.
- **Exclusión:** el sistema genera alertas **por lotes**, así que el delta
  incluye la espera hasta procesar el lote. **No es latencia en tiempo real**
  ni mide reconocimiento o recuperación: P6-06 no modela un ciclo de vida
  activa/resuelta. Se excluyen alertas `informational`, timestamps ausentes o
  inválidos y deltas negativos. `evidencia` enumera los IDs ausentes,
  inválidos y negativos, y conserva una muestra primaria con `alert_id`,
  timestamps y segundos.
- **Ausencia:** sin alertas `triggered` con deltas válidos el numerador y el
  valor son nulos, el estado es `sin_datos` y `ausencias` explica el motivo.

## Cero seguro

Toda razón usa la misma convención:

- denominador `0` → `valor: null`, `estado: "sin_datos"`;
- se agrega una entrada en `ausencias` si la métrica no la declaró;
- un numerador `0` con denominador positivo produce `0.0` y `estado: "ok"`, que
  es distinto de no tener datos.

Los valores se redondean a seis decimales. Los navegadores, hojas de cálculo o
scripts deben tratar `null` como desconocido, nunca como cero.

## Recalculo manual de una muestra

La corrida `p6-05-verificacion-20261113-222520` contiene 272 detecciones
puntuadas: 64 `accepted`, 129 `doubt` y 79 `low`. Para G4:

```text
G4 = detecciones_low / detecciones_puntuadas
   = 79 / (64 + 129 + 79)
   = 79 / 272
   = 0.290441
```

El reporte incluye `evidencia.zonas` con esos tres conteos, de modo que la
operación puede repetirse sin confiar en el valor ya calculado. De forma
equivalente, desde PowerShell:

```powershell
$reporte = Get-Content ".\datos_locales\reportes\p6-07-p6-05-verificacion-20261113-222520.json" -Raw | ConvertFrom-Json
$zonas = $reporte.metricas.G4.evidencia.zonas
$zonas.low / ($zonas.accepted + $zonas.doubt + $zonas.low)
```

Para G1 y G2 se usan los conteos del resumen P6-03; G6 usa el último
`received_at` y el `timestamp` del reporte; G3 usa los bytes libres y totales
del instante de cálculo; G5 cuenta eventos persistidos. Para G7, cada alerta
`triggered` válida aporta `segundos(generated_at - occurred_at)`. La corrida
`p6-09-video-20261113-230341` tiene 10 alertas `triggered` válidas que suman
64.973537 s:

```text
G7 = suma(segundos) / alertas_triggered_válidas
   = 64.973537 / 10
   = 6.497354
```

`evidencia.muestra_primaria` conserva los IDs, timestamps y segundos de la
muestra para repetir la operación. Esos conteos también aparecen en `evidencia`
o `inputs`.

## Resultado local de la corrida de referencia

Con `--generated-at 2026-11-14T04:26:00+00:00`:

| Grupo | Numerador | Denominador | Valor |
| --- | ---: | ---: | ---: |
| G1 | 0 | 10 | 0.0 |
| G2 | 126 | 136 | 0.926471 |
| G3 | bytes libres al momento | bytes totales | variable |
| G4 | 79 | 272 | 0.290441 |
| G5 | 10 | 10 | 1.0 |
| G6 | 27.04 s | 1 | 27.04 |
| G7 | sin deltas válidos | 0 | `null` |

G2 alto es esperado: la corrida muestrea uno de cada varios cuadros, por lo que
la mayoría de los cuadros recibidos se descarta por diseño. En la corrida de
referencia las 10 alertas `triggered` tienen `generated_at`
(`2026-11-13T23:30:00+00:00`) anterior a `occurred_at` (`2026-11-14T04:25:2x`):
los 10 deltas son negativos, se excluyen y G7 queda `sin_datos` en lugar de
inventar un valor. La corrida `p6-09-video-20261113-230341` sí tiene timestamps
consistentes y produce 6.497354 s. G1, G5 y G7 no acreditan calidad del modelo
ni exactitud de las alertas; G7 tampoco mide recuperación ni latencia en tiempo
real. G3 depende del disco de la máquina y cambia entre ejecuciones; el valor
anterior es solo una referencia local.

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_metricas -v
```

Las pruebas cubren las siete métricas, el recálculo manual, los denominadores
cero, un numerador cero con denominador positivo, el fallo de disco, los JSONL
ausentes o malformados y la escritura del CLI. El proyecto usa `unittest`; no se
agregó `pytest`.

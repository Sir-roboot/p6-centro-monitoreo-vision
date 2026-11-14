# P6-03 · Observaciones primarias versionadas

## Alcance

`observaciones.py` conecta la fuente de P6-01 con el detector de P6-02 y
persiste un **JSONL versionado**: una cabecera, una observación por cuadro y un
resumen final. No guarda píxeles, no calcula indicadores G1–G7 y no crea
eventos, capturas ni alertas. Esa instrumentación pertenece a etapas
posteriores.

El esquema es `p6-03/1` y la versión de pipeline es `p6-03`. Cada lote tiene un
`run_id` explícito o generado con marca UTC y sufijo aleatorio. La salida
predeterminada es `datos_locales/observaciones/<run_id>.jsonl`; `datos_locales/`
está ignorado por Git. El archivo se escribe en un temporal del mismo
directorio y se reemplaza con `os.replace` al terminar: una falla no deja un
JSONL parcial con nombre definitivo.

## Contrato del archivo

El orden es siempre cabecera, observaciones y resumen. Cada línea es un objeto
JSON independiente con `schema`, `record` y `run_id`.

### Cabecera

| Campo | Contenido |
|---|---|
| `created_at` | Instante UTC de inicio. |
| `pipeline` | `p6-03`. |
| `source` | `id`, `ref` y `kind` (`stream`, `directory`, `image`, `video`, `file`). |
| `config` | `sample_every`, `interval_ms`, `max_frames`, `max_seconds`. |
| `model` | Metadatos completos declarados por el detector de P6-02. |
| `filters` | Umbrales y preprocesamiento declarados por el detector. |
| `versions` | `schema`, `pipeline`, versión de Python y de OpenCV. |

`source.ref` elimina usuario, contraseña y parámetros de consulta de una URL
antes de persistirla. Una ruta local se conserva tal como se indicó.

### Observación

| Campo | Contenido |
|---|---|
| `observation_id` | `<run_id>:<frame_id>`; los registros terminales usan `<run_id>:terminal-NNNNNN`. |
| `sequence` | Entero consecutivo desde 1. |
| `source_id`, `frame_id`, `frame_number` | Identidad heredada de la fuente. |
| `received_at`, `started_at`, `finished_at`, `duration_ms` | Tiempos UTC y duración del procesamiento. |
| `source_status` | `sampled`, `discarded`, `read_error` o `end_of_stream`. |
| `status` | `processed`, `discarded`, `read_error`, `detector_error` o `end_of_stream`. |
| `technical_result` | `ok`, `no_detections`, `error` o `null` según el estado. |
| `reason` | Motivo concreto para descartes y fallas; `null` en los demás casos. |
| `image` | Solo `width` y `height`; nunca píxeles. |
| `detections` | Lista de `class_id`, `class_name`, `confidence` y `bbox_xyxy`. |
| `class_counts` | Conteo ordenado por clase, coherente con `detections`. |
| `detector_timings_ms` | Tiempos del detector cuando existe un resultado. |
| `model` | Subconjunto compacto: nombre, dataset, versión, SHA-256 y tamaño. |
| `error` | Detalle técnico solo en `read_error` y `detector_error`. |

Una observación `sampled` sin imagen o sin `frame_id`, o un detector que lanza
una excepción, devuelve un estado de error explícito. Una escena sin objetos es
`processed` con `technical_result="no_detections"` y `detections=[]`; no se
confunde con un detector caído. Un resultado de detector con identidad distinta
a la del cuadro, con `class_counts` incoherente o con un estado inesperado se
registra como `detector_error`.

### Resumen

Incluye la misma `source`, `config`, `model`, `filters` y `versions` de la
cabecera, además de `started_at`, `finished_at`, `duration_ms`, `end_state`,
`technical_result` y `counts`.

| Contador | Significado |
|---|---|
| `frames_received` | Cuadros `sampled` + `discarded` informados por la fuente. |
| `frames_sampled` | Cuadros entregados al pipeline para inferencia. |
| `frames_discarded` | Cuadros descartados por muestreo; incluye motivos. |
| `read_errors` | Errores de lectura, con motivos. |
| `frames_processed` | Cuadros con inferencia exitosa. |
| `frames_with_detections` | Cuadros procesados con al menos una detección. |
| `frames_without_detections` | Cuadros procesados sin detecciones. |
| `detector_calls` | Llamadas intentadas al detector (`processed` + `detector_error`). |
| `detector_errors` | Fallas del detector, con motivos. |
| `detections_total` | Suma de detecciones individuales. |
| `detections_by_class` | Detecciones agrupadas por `class_name`. |
| `end_of_stream_events` | Registros terminales de fin de flujo. |

`end_state` documenta por qué terminó el lote: `end_of_stream`, `max_frames`,
`max_duration`, `source_exhausted`, `source_exception` o el motivo del error de
lectura. `technical_result` es `with_errors` si hubo cualquier error de lectura
o detector, `no_data` si no se recibió ni procesó nada, y `ok` en el resto.
`max_seconds=0` o `None` significa sin límite de duración. El límite es
aproximado: se comprueba al terminar cada cuadro y no interrumpe una lectura
bloqueada o el intervalo de muestreo en curso.

Una imagen corrupta en un directorio se registra como `read_error` y el
recorrido continúa con los cuadros siguientes. Un fallo al abrir la fuente o
leer un stream termina el recorrido. Aunque se llegue al fin de una secuencia
que contenía una imagen corrupta, el resultado técnico sigue siendo
`with_errors`.

## CLIs

```powershell
.\.venv\Scripts\python.exe ejecutar_pipeline.py --source .\datos_locales\secuencia_demo --source-id demo --sample-every 3 --interval-ms 0 --output .\datos_locales\observaciones\demo-p6-03.jsonl --run-id demo-p6-03 --no-download
.\.venv\Scripts\python.exe recalcular_observaciones.py --input .\datos_locales\observaciones\demo-p6-03.jsonl
```

`ejecutar_pipeline.py` acepta las opciones de fuente y detector ya usadas en
P6-01/P6-02 (`--source`, `--source-id`, `--sample-every`, `--interval-ms`,
`--max-frames`, `--seconds`, `--weights`, `--score-threshold`,
`--nms-threshold`, `--no-download`) y agrega `--output`, `--run-id` y
`--overwrite`. Imprime un JSON con `output` y `summary`; devuelve 0 solo si el
resultado técnico es `ok` y 1 si es `with_errors` o `no_data`. No sobrescribe
una salida existente sin `--overwrite`.

`recalcular_observaciones.py` vuelve a sumar los contadores desde los campos
persistidos, sin abrir la fuente ni cargar el modelo. Compara `counts` y
`technical_result` con el resumen almacenado y devuelve 0 si coinciden o 1 si no.
También valida esquema, secuencia, IDs, coherencia de `class_counts` y forma del
resumen, y exige que fuente, configuración, modelo, filtros y versiones del
resumen coincidan con la cabecera. No recalcula ni valida `end_state` o tiempos.
La salida debe terminar en `.jsonl`; no se permite reemplazar el archivo de
entrada ni siquiera con `--overwrite`.

## Verificación observada

El 13 de noviembre de 2026, con Python 3.14.3, OpenCV 5.0.0 y los pesos
verificados de YOLOX-Tiny:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Resultado: **64 pruebas**, todas `ok`; no requieren red ni los pesos reales.
La corrida demo anterior sobre la secuencia sintética de 12 cuadros produjo 12
recibidos, 4 seleccionados, 8 descartados por muestreo, 0 errores,
`end_state="end_of_stream"` y 0 detecciones (la secuencia no contiene objetos
COCO). El lote duró 325.599 ms; la primera inferencia tardó 73.186 ms. El
recalculo devolvió `coincide: true` sobre 13 observaciones (12 cuadros y el
registro terminal). El archivo generado está en `datos_locales/` y no se
versiona.

## Límites conocidos

- Solo se registran observaciones técnicas primarias; no hay métricas G1–G7,
  comparación contra referencia, eventos, capturas ni alertas.
- No se guardan ni codifican píxeles en el JSONL.
- Un lote no se deduplica contra corridas anteriores; `run_id` y `--output`
  deben elegirse de forma deliberada.
- La escritura es atómica a nivel de archivo, pero no se fuerza `fsync` ni se
  protege contra fallas del sistema de archivos.
- El pipeline valida coherencia del resultado del detector, no la calidad de
  sus detecciones.
- La secuencia sintética verifica el flujo completo, no el desempeño en C1/C2.

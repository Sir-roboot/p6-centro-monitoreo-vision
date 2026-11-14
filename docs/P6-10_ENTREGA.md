# P6-10 · Entrega final reproducible

Fecha de verificación: **2026-11-13 (local) / 2026-11-14 UTC**.
Commit verificado: **`5185c1c9afebd142be9a0a26382f06d1f129fca8`** (`main`,
fast-forward desde `8b23b31`; sin push).
Autoría: **Santiago Ortiz**, proyecto individual de Servicios de Aprendizaje
Automático. Este documento no modifica código, README ni artefactos: solo
registra comprobaciones ejecutadas en el clon limpio
`p6-reproduccion-final`.

> **Alcance honesto.** En este clon **no** se reprodujeron C1/C2 idénticos. Su
> señal MJPEG (`http://127.0.0.1:8765/stream.mjpg`), el video de origen, los
> JSONL, los JPEG y la referencia de política son locales y están ignorados por
> Git. Aquí solo se verifican dependencias, suite, descarga/SHA del modelo y la
> secuencia sintética propia.

## Entorno y dependencias

| Elemento | Valor comprobado |
| --- | --- |
| Sistema | Windows, PowerShell |
| Intérprete usado | `..\proyecto6-santiago\.venv\Scripts\python.exe` (Python 3.14.3) |
| `pip` | 25.3 |
| `numpy` | 2.5.4 |
| `opencv-python` | 5.0.0.93 |
| Declaración | [`requirements.txt`](../requirements.txt) con versiones fijas |
| Pruebas | `unittest` de la biblioteca estándar; **no** se requiere `pytest` |

El intérprete indicado ya contenía exactamente las dos dependencias declaradas.
No se creó un `.venv` nuevo ni se instalaron paquetes en este clon; no hay
archivos versionados añadidos por la verificación (`models/` y `datos_locales/`
están en `.gitignore`).

```powershell
git pull --ff-only origin main
git rev-parse HEAD
..\proyecto6-santiago\.venv\Scripts\python.exe --version
..\proyecto6-santiago\.venv\Scripts\python.exe -m pip list --format=freeze
```

Salida real: `5185c1c9afebd142be9a0a26382f06d1f129fca8`, `Python 3.14.3`,
`numpy==2.5.4`, `opencv-python==5.0.0.93`, `pip==25.3`.

## Suite ejecutada

```powershell
..\proyecto6-santiago\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Resultado real: **`Ran 146 tests in 20.179s` · `OK (skipped=3)`**.

Las tres omisiones son `test_p6_09_demostraciones` y declaran
`datos_locales/ local no disponible; evidencia P6-09 no versionada`; no son
fallos. El README cita 129 pruebas en una sección histórica de P6-04; el
conteo vigente de este commit es 146.

## Modelo oficial y SHA-256

```powershell
New-Item -ItemType Directory -Force models | Out-Null
Invoke-WebRequest `
  -Uri "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx" `
  -OutFile ".\models\yolox_tiny.onnx" -UseBasicParsing
(Get-Item .\models\yolox_tiny.onnx).Length
(Get-FileHash .\models\yolox_tiny.onnx -Algorithm SHA256).Hash.ToLower()
```

| Dato | Valor comprobado |
| --- | --- |
| Archivo | `models/yolox_tiny.onnx` (ignorado por Git) |
| Tamaño | **20 219 662 bytes** |
| SHA-256 | **`427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7`** |
| Coincidencia con `SHA256_PESOS` | Sí, exacta ([`detector_yolox.py`](../detector_yolox.py)) |
| Descarga real | Correcta en 24.3 s el 2026-11-13 |

Procedencia: release oficial `0.1.1rc0` de Megvii-BaseDetection/YOLOX,
YOLOX-Tiny COCO en ONNX, ejecutado con OpenCV DNN en CPU. No se usa
`onnxruntime` ni pesos alternativos.

## Secuencia de prueba ejecutada

```powershell
..\proyecto6-santiago\.venv\Scripts\python.exe generar_secuencia_demo.py
..\proyecto6-santiago\.venv\Scripts\python.exe lector_opencv.py `
  --source .\datos_locales\secuencia_demo --source-id demo `
  --sample-every 3 --interval-ms 200 --no-preview
..\proyecto6-santiago\.venv\Scripts\python.exe detectar_opencv.py `
  --source .\datos_locales\secuencia_demo --source-id demo `
  --sample-every 3 --interval-ms 200 --no-preview --no-download
```

Resultados reales:

| Etapa | Resultado |
| --- | --- |
| Generador | 12 cuadros sintéticos en `datos_locales/secuencia_demo` |
| Lector | `end_of_stream`; `received=12`, `sampled=4`, `discarded=8`, `read_errors=0` |
| Detector | `end_of_stream`; `ok=4`, `error=0`, `detections=0` (la secuencia no contiene objetos COCO) |
| Inferencia (cuadro 10) | `preprocess=1.373 ms`, `inference=37.891 ms`, `postprocess=2.923 ms`, `total=42.238 ms` |

La secuencia sintética verifica entrada, muestreo e inferencia real; **no**
acredita precisión ni sustituye las escenas de C1/C2.


## Matriz R1–R7

Interpretación declarada: R1–R7 son los requisitos de reproducibilidad de esta
entrega, en el orden en que se verificaron. Los requisitos funcionales de la
cadena siguen mapeados a P6-01…P6-08 (fuente, detector, observaciones,
confianza, eventos, alertas, métricas y portal).

| ID | Requisito | Evidencia verificada | Estado |
| --- | --- | --- | --- |
| R1 | Clon limpio en el commit acordado | `git rev-parse HEAD` = `5185c1c…`; fast-forward `8b23b31..5185c1c` | Cumple |
| R2 | Requisitos y dependencias declarados | [`requirements.txt`](../requirements.txt); versiones instaladas coinciden | Cumple |
| R3 | Suite completa ejecutable | `146 tests`, `OK (skipped=3)`; omisiones P6-09 explicadas | Cumple |
| R4 | Modelo oficial descargable y verificado | 20 219 662 bytes; SHA-256 exacto; [`detector_yolox.py`](../detector_yolox.py) | Cumple |
| R5 | Secuencia de prueba sin datos privados | 12/4/8/0 en fuente; 4 inferencias `ok`, 0 errores con pesos oficiales | Cumple |
| R6 | Documentación con evidencia, autoría y límites | Este documento + [`P6-09`](P6-09_DEMOSTRACIONES.md) | Cumple |
| R7 | Higiene de entrega | Sin credenciales; `models/` y `datos_locales/` ignorados; commit local sin push | Cumple |

## Matriz G1–G7

Definiciones y fórmulas: [`docs/P6-07_METRICAS.md`](P6-07_METRICAS.md). Los
valores de C1/C2 están documentados en
[`docs/P6-09_DEMOSTRACIONES.md`](P6-09_DEMOSTRACIONES.md) a partir de artefactos
locales; **no se recalcularon en este clon** porque faltan esos artefactos.

| ID | Métrica base | Evidencia | Estado en clon limpio |
| --- | --- | --- | --- |
| G1 | `detector_errors / frames_sampled` | [P6-07 §G1](P6-07_METRICAS.md), [P6-09](P6-09_DEMOSTRACIONES.md) | Definida; valor C1/C2 solo local |
| G2 | `frames_discarded / frames_received` | [P6-07 §G2](P6-07_METRICAS.md) | Definida; valor C1/C2 solo local |
| G3 | `free_bytes / total_bytes` | [P6-07 §G3](P6-07_METRICAS.md) | Instantánea; no comparable entre equipos |
| G4 | `detecciones_low / detecciones_puntuadas` | [P6-07 §G4](P6-07_METRICAS.md), [P6-09](P6-09_DEMOSTRACIONES.md) | Definida; valor C1/C2 solo local |
| G5 | `eventos_trazables / eventos_persistidos` | [P6-07 §G5](P6-07_METRICAS.md) | Definida; valor C1/C2 solo local |
| G6 | `generated_at - max(received_at)` | [P6-07 §G6](P6-07_METRICAS.md) | Depende de `--generated-at`; no reproducible aquí |
| G7 | Media de `generated_at - occurred_at` | [P6-07 §G7](P6-07_METRICAS.md) | Latencia de reporte, no notificación en vivo |

G1–G7 describen operación y trazabilidad del servicio, **no** exactitud,
precisión ni recall del detector.

## Procedencia del modelo y autoría

- Modelo: **YOLOX-Tiny 0.1.1rc0 COCO**, ONNX, release oficial de
  Megvii-BaseDetection. URL y SHA-256 declarados en `detector_yolox.py` y
  verificados contra el archivo descargado.
- Implementación P6: independiente, con OpenCV DNN; no importa el paquete `p5`.
- Autoría: **Santiago Ortiz**, trabajo individual. Las pruebas, la cadena y la
  documentación se atribuyen a él; las fuentes externas se citan solo como
  procedencia.

## Limitaciones

1. **C1/C2 no reproducidos:** su señal, video, JSONL, JPEG y referencia son
   locales; un clon limpio no puede reabrirlos ni comparar hashes de artefactos.
2. **Muestra sintética:** 12 cuadros sin objetos COCO; no evalúa calidad del
   detector ni genera alertas.
3. **Umbrales exploratorios:** 0.17/0.61 (C1/C2) y 0.30/0.70 (política base) no
   son calibración; `robust_reference: false`.
4. **Sin ground truth:** no hay precisión, recall, falsos positivos/negativos.
5. **G3 y G6 son instantáneas** y dependen del equipo y del momento de cálculo.
6. **Dependencia de red** para crear un entorno nuevo o descargar los pesos; la
   suite en sí no usa red.
7. **Tres pruebas omitidas** por ausencia de artefactos P6-09 locales; no deben
   interpretarse como verificación superada.

## Extras reclamados

**Ninguno.** No se reclaman créditos adicionales: no hay demo en vivo,
video propio nuevo, optimización medida ni funcionalidad fuera de P6-01…P6-09
demostrada en este commit.

## Qué requiere acceso local

- Señal `http://127.0.0.1:8765/stream.mjpg` y grabador de pantalla fuera del
  repositorio, más el video público de origen.
- `datos_locales/` con observaciones, políticas, eventos, capturas, alertas y
  reportes de C1/C2, incluida la referencia
  `monitor-p6-04-20261113-214440.jsonl`.
- Pesos en `models/yolox_tiny.onnx` (recuperables por URL y SHA, pero no
  versionados) para repetir inferencia sin red.
- Para recalcular G1–G7 de C1/C2: los JSONL/alertas originales y el mismo
  `--generated-at`; de lo contrario los valores cambian.

## Cierre

- [x] Commit exacto y pull fast-forward verificados, sin push.
- [x] Dependencias y suite ejecutadas con el intérprete indicado.
- [x] Modelo oficial descargado y SHA-256 comprobado.
- [x] Secuencia sintética y detector ejecutados con resultados reales.
- [x] R1–R7 y G1–G7 documentados con enlaces, sin inventar valores.
- [x] Autoría, procedencia, limitaciones y extras declarados.
- [x] Sin credenciales y sin artefactos locales versionados.

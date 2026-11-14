# P6-10 · Entrega final reproducible

Fecha de verificación: **2026-11-13 (local) / 2026-11-14 UTC**.
Revisión de código y demostraciones originales: **`5185c1c9afebd142be9a0a26382f06d1f129fca8`**.
La documentación final se añadió después; por ello, el commit de entrega será
posterior a esa revisión de código. Responsable de la entrega individual:
**Santiago Ortiz**. Se usaron herramientas de IA como apoyo de desarrollo y
revisión; las verificaciones y límites de este documento se presentan como
resultados observados, no como autoría exclusiva de escritura manual.

> **Alcance honesto.** En el clon se ejecutó una **nueva corrida real** de la
> cadena completa, pero no se reprodujeron idénticos cuadros de C1/C2. La
> señal MJPEG, el video de origen y la referencia de política son recursos
> externos al repositorio. Sus JSONL y JPEG permanecen locales e ignorados por
> Git; un evaluador remoto no puede verificar los originales sin recibirlos.

## Entorno y dependencias

| Elemento | Valor comprobado |
| --- | --- |
| Sistema | Windows, PowerShell |
| Intérprete usado | `.\.venv\Scripts\python.exe` (Python 3.14.3), creado en el clon |
| `pip` | 25.3 |
| `numpy` | 2.5.4 |
| `opencv-python` | 5.0.0.93 |
| Declaración | [`requirements.txt`](../requirements.txt) con versiones fijas |
| Pruebas | `unittest` de la biblioteca estándar; **no** se requiere `pytest` |

Se creó un `.venv` nuevo en el clon y se instalaron exactamente las dos
dependencias declaradas. El primer intento de instalación falló por el
aislamiento de red del entorno; al permitir la conexión, la instalación
terminó correctamente. `.venv/`, `models/` y `datos_locales/` están ignorados
por Git.

```powershell
git clone https://github.com/Sir-roboot/p6-centro-monitoreo-vision.git p6-evaluacion
cd .\p6-evaluacion
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
git rev-parse HEAD
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip list --format=freeze
```

Salida real: `5185c1c9afebd142be9a0a26382f06d1f129fca8`, `Python 3.14.3`,
`numpy==2.5.4`, `opencv-python==5.0.0.93`, `pip==25.3`.

## Suite ejecutada

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Resultado real desde el entorno recién instalado: **`Ran 146 tests in 17.331s` · `OK (skipped=3)`**.

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
.\.venv\Scripts\python.exe generar_secuencia_demo.py
.\.venv\Scripts\python.exe lector_opencv.py `
  --source .\datos_locales\secuencia_demo --source-id demo `
  --sample-every 3 --interval-ms 200 --no-preview
.\.venv\Scripts\python.exe detectar_opencv.py `
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

## Nueva corrida real desde el clon

Con el grabador del monitor 2 activo y la referencia local **externa** al clon,
se ejecutó otra inferencia real desde el entorno recién instalado sobre el
commit de código `5185c1c`. No se cambiaron los umbrales de C1/C2 y no se
inyectaron puntuaciones.

```powershell
$runId = 'p6-10-repro-20261113-234003'
.\.venv\Scripts\python.exe .\ejecutar_eventos.py `
  --source http://127.0.0.1:8765/stream.mjpg --source-id monitor-video `
  --sample-every 15 --max-frames 136 --run-id $runId `
  --reference ..\proyecto6-santiago\datos_locales\observaciones\monitor-p6-04-20261113-214440.jsonl `
  --lower 0.17 --upper 0.61 `
  --threshold-justification 'Cuartiles redondeados de referencia previa; exploratorio' `
  --no-download
.\.venv\Scripts\python.exe .\calcular_metricas.py `
  --observaciones ".\datos_locales\observaciones\$runId.jsonl" `
  --eventos ".\datos_locales\eventos\$runId.jsonl" `
  --alertas ".\datos_locales\alertas\$runId.jsonl" `
  --root . --output ".\datos_locales\reportes\p6-07-$runId.json"
```

Resultado observado: `technical_result=ok`, 136 cuadros recibidos, 10
muestreados, 10 eventos, 10 capturas, 10 alertas `low-confidence` y
`policy_status=no_sustained_change`. La verificación posterior de alertas
comprobó los 10 eventos y las 10 capturas sin fallos. El reporte dio
G1 `0/10`, G2 `126/136`, G3 `157617057792/511040286720`, G4 `59/212`,
G5 `10/10`, G6 `3.516 s` y G7 `9.348112 s` (valor de lote).
El portal del clon respondió HTTP 200 en la corrida, métricas auxiliares,
vista G1–G7 y JSON G1–G7 (`127.0.0.1:8767`). Estos resultados son una
**nueva ejecución**, no los cuadros originales de C1/C2, y siguen mostrando
baja confianza; no constituyen una C1 libre de alertas.


## Matriz R1–R7 de la rúbrica docente

Esta tabla usa **los nombres de la rúbrica**, no identifica puntos concedidos:
la calificación corresponde al evaluador. «Local» significa que la evidencia
primaria no está disponible al clonar Git.

| Criterio | Evidencia y verificación | Límite |
| --- | --- | --- |
| R1 · Fuente visual y modelo | [Fuente](P6-01_FUENTE.md), [detector](P6-02_DETECTOR.md), [observaciones](P6-03_OBSERVACIONES.md); pesos oficiales y SHA comprobados en clon; nueva corrida real abajo | La secuencia sintética no sustituye C1/C2 |
| R2 · Métricas G1–G7 | [Definiciones y recálculo](P6-07_METRICAS.md); reporte de la nueva corrida abajo y [portal](P6-08_PORTAL.md) | G3 varía por equipo; G6/G7 por hora del reporte; no miden exactitud |
| R3 · Umbrales y monitoreo | [Política](P6-04_CONFIANZA.md), referencia local y umbrales 0.17/0.61 persistidos en las corridas | Referencia exploratoria, `robust_reference=false` |
| R4 · Eventos e imágenes | [Contrato](P6-05_EVENTOS.md); 10 eventos y 10 capturas de la nueva corrida, hashes verificados | JPEG originales no versionados |
| R5 · Alertas | [Reglas](P6-06_ALERTAS.md); 10 alertas reales de baja confianza, 10 vínculos verificados y consulta [portal](P6-08_PORTAL.md) | No se afirma cambio sostenido ni recuperación |
| R6 · C1/C2 | [Demostraciones originales](P6-09_DEMOSTRACIONES.md) y nueva corrida real abajo | C1 también alertó; originales no reproducibles idénticos desde Git |
| R7 · Git y reproducibilidad | [README](../README.md), este índice, instalación y suite desde clon limpio | Artefactos originales solo en laptop |

## Matriz G1–G7

Definiciones y fórmulas: [`docs/P6-07_METRICAS.md`](P6-07_METRICAS.md). Los
valores originales de C1/C2 están documentados en
[`docs/P6-09_DEMOSTRACIONES.md`](P6-09_DEMOSTRACIONES.md). Esos valores no se
recalcularon en el clon porque faltan sus artefactos; **sí se calculó un nuevo
reporte G1–G7** para `p6-10-repro-20261113-234003` como se indica arriba.

| ID | Métrica base | Evidencia | Estado en clon limpio |
| --- | --- | --- | --- |
| G1 | `detector_errors / frames_sampled` | [P6-07 §G1](P6-07_METRICAS.md), [P6-09](P6-09_DEMOSTRACIONES.md) | Nueva corrida: `0/10` |
| G2 | `frames_discarded / frames_received` | [P6-07 §G2](P6-07_METRICAS.md) | Nueva corrida: `126/136` |
| G3 | `free_bytes / total_bytes` | [P6-07 §G3](P6-07_METRICAS.md) | Nueva corrida: `157617057792/511040286720`; cambia con el disco |
| G4 | `detecciones_low / detecciones_puntuadas` | [P6-07 §G4](P6-07_METRICAS.md), [P6-09](P6-09_DEMOSTRACIONES.md) | Nueva corrida: `59/212` |
| G5 | `eventos_trazables / eventos_persistidos` | [P6-07 §G5](P6-07_METRICAS.md) | Nueva corrida: `10/10` |
| G6 | `generated_at - max(received_at)` | [P6-07 §G6](P6-07_METRICAS.md) | Nueva corrida: `3.516 s`; depende de hora del reporte |
| G7 | Media de `generated_at - occurred_at` | [P6-07 §G7](P6-07_METRICAS.md) | Nueva corrida: `9.348112 s`; latencia de lote |

G1–G7 describen operación y trazabilidad del servicio, **no** exactitud,
precisión ni recall del detector.

## Procedencia del modelo y autoría

- Modelo: **YOLOX-Tiny 0.1.1rc0 COCO**, ONNX, release oficial de
  Megvii-BaseDetection. URL y SHA-256 declarados en `detector_yolox.py` y
  verificados contra el archivo descargado.
- Implementación P6: independiente, con OpenCV DNN; no importa el paquete `p5`.
- Responsable: **Santiago Ortiz**, entrega individual, con apoyo de
  herramientas de IA durante implementación y documentación. Las fuentes
  externas se citan como procedencia.

## Limitaciones

1. **C1/C2 originales no reproducidos idénticos:** sus JSONL, JPEG y referencia
   son locales; el clon sí ejecutó una nueva corrida real usando la señal y
   referencia externas, pero no puede comparar hashes de los originales.
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
- [x] Entorno nuevo, dependencias y suite ejecutadas en el clon.
- [x] Modelo oficial descargado y SHA-256 comprobado.
- [x] Secuencia sintética y detector ejecutados; nueva corrida real, eventos,
      alertas, métricas y portal comprobados sin alterar los originales.
- [x] R1–R7 y G1–G7 documentados con enlaces, sin inventar valores.
- [x] Autoría, procedencia, limitaciones y extras declarados.
- [x] Sin credenciales y sin artefactos locales versionados.

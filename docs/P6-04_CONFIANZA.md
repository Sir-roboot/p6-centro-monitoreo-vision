# P6-04 · Política de confianza exploratoria

## Alcance

`politica_confianza.py` y `evaluar_confianza.py` construyen una **política de
confianza versionada** a partir de un JSONL ya persistido por P6-03. La
herramienta solo lee puntuaciones existentes: **no carga el modelo, no vuelve a
inferir, no aplica NMS, no modifica `confidence`** y no crea eventos, capturas,
alertas, portal ni métricas G1–G7. Esos contratos pertenecen a P6-05 y etapas
posteriores.

La política clasifica cada detección puntuada en tres zonas y evalúa cambio
sostenido por ventanas. Una decisión `accepted` significa aceptación por esta
política, **no** que la detección sea correcta ni que el modelo esté calibrado.

## Uso del CLI

Desde la raíz del repositorio, con el JSONL de P6-03 ya generado:

```powershell
.\.venv\Scripts\python.exe evaluar_confianza.py --input .\datos_locales\observaciones\demo-p6-03.jsonl
```

La salida predeterminada es
`datos_locales/politicas/<run_id>-p6-04.json`. Para fijar ruta, justificar los
umbrales o reemplazar un artefacto existente:

```powershell
.\.venv\Scripts\python.exe evaluar_confianza.py `
  --input .\datos_locales\observaciones\escena-p6-03.jsonl `
  --reference .\datos_locales\observaciones\referencia-p6-03.jsonl `
  --output .\datos_locales\politicas\escena-p6-04.json `
  --threshold-justification "Umbrales revisados con la escena C1 autorizada" `
  --overwrite
```

| Opción | Predeterminado | Efecto |
|---|---|---|
| `--input`, `-i` | obligatorio | JSONL P6-03 que se valida y evalúa. |
| `--reference` | ninguno | JSONL P6-03 separado usado como referencia externa. |
| `--output`, `-o` | `datos_locales/politicas/<run_id>-p6-04.json` | Destino del artefacto. |
| `--lower` | `0.30` | Frontera inferior de la zona `doubt`. |
| `--upper` | `0.70` | Frontera inferior de la zona `accepted`. |
| `--window-frames` | `5` | Frames por ventana no solapada. |
| `--window-min-scores` | `3` | Detecciones puntuadas mínimas por ventana completa. |
| `--consecutive-windows` | `2` | Ventanas completas degradadas consecutivas para cambio sostenido; mínimo 2. |
| `--low-fraction-min` | `0.5` | Fracción mínima de puntuaciones bajas en una ventana degradada. |
| `--mean-drop-min` | `0.10` | Caída mínima de la media frente a la referencia. |
| `--reference-min-frames` | `3` | Frames puntuados mínimos de la referencia. |
| `--reference-min-scores` | `5` | Detecciones puntuadas mínimas de la referencia. |
| `--threshold-justification` | ninguno | Texto no vacío que sustituye la justificación exploratoria. |
| `--overwrite` | desactivado | Permite reemplazar el artefacto de salida. |

El comando imprime **una línea JSON** con `output` y un `summary` compacto.
Devuelve `0` si el artefacto se escribió, `1` ante configuración, lectura,
validación o escritura inválida, y `130` si se interrumpe. Se rechaza que la
salida sea el mismo archivo que `--input` o `--reference`. La escritura es
atómica: usa un temporal en el mismo directorio y `os.replace`; nunca deja un
JSON definitivo a medias. `datos_locales/` está ignorado por Git.

## Artefacto versionado

El archivo es un objeto JSON con `schema: "p6-04/1"`,
`record: "confidence_policy"` y `policy_version: "p6-04"`. Su identidad es
`policy_id: "p6-04-<16 hex>"`, derivada de la huella SHA-256 de la configuración
completa; `configuration_digest` conserva la huella íntegra. Cambiar umbrales,
ventana, mínimos o el contrato de detección produce otro `policy_id`: **los
artefactos con distinto `policy_id` no son comparables**.

| Campo | Contenido |
|---|---|
| `input` | Ruta, `run_id`, fuente, esquema P6-03, número de registros, resultado técnico y ruta de referencia. |
| `identity` | Modelo, filtros y SHA-256 de identidad heredados de P6-03. |
| `configuration` | Umbrales, regla de ventana y mínimos de referencia efectivos. |
| `thresholds` | Fronteras, definiciones exactas de zona, semántica y justificación con su evidencia. |
| `detection_contract` | Piso de score, definición de score y NMS persistidos frente al contrato documentado de YOLOX. |
| `reference` | Origen, estado, identidad, mínimos, frames y puntuaciones de la referencia. |
| `evaluation` | Conteo por estado, denominador, zonas, medias, ventanas y puntuaciones evaluadas. |
| `sustained_change` | Regla, estado, motivo, ventanas completas, racha máxima y suficiencia de evidencia. |
| `normality_established` | Atajo del estado anterior; solo puede ser `true` con `no_sustained_change`. |
| `p6_05_contract` | Condiciones que P6-05 debe respetar al consumir la política. |
| `limitations` | Límites explícitos del artefacto. |

`validar_politica()` y `leer_politica()` permiten validar el sobre mínimo antes
de consumirlo; P6-05 no debe reemplazar estas reglas.


## Umbrales y zonas

Las fronteras son estrictas y no se redondean:

| Zona | Definición |
|---|---|
| `low` | `s < 0.30` |
| `doubt` | `0.30 <= s < 0.70` |
| `accepted` | `s >= 0.70` |

Los valores **0.30/0.70 son exploratorios**. Se eligieron a partir de cinco
puntuaciones reales de **una sola imagen** `dog.jpg` de los assets de YOLOX,
documentada en P6-02: **0.8840, 0.6717, 0.5386, 0.1594 y 0.1128**. Las cinco
detecciones de esa ejecución fueron `bicycle` 0.8840, `car` 0.6717, `dog`
0.5386, `tv` 0.1594 y `potted plant` 0.1128, con SHA-256
`5a9522051c3cec2bbd2f6323fccba32e8fbf3ddcc2b3e2fd46b04c720bc6f866`. No es una
muestra representativa ni una calibración; las dos últimas son detecciones
débiles atribuibles al piso bajo de 0.10.

Por lo tanto, **0.30/0.70 no constituyen una línea base válida para C1/C2** ni
acreditan normalidad, precisión o calibración. C1/C2 requieren escenas reales
reproducibles y autorizadas, con observaciones suficientes para revisar los
umbrales. El artefacto declara `origin: "exploratory_single_image"` y
`evidence.robust_reference: false`; si se usan otros umbrales, el origen pasa a
`operator_configured`. Cuando la referencia alcanza sus mínimos, el artefacto
incluye sus cuartiles descriptivos en `justification.observed_reference`; estos
datos permiten rastrear la elección, pero no prueban calibración ni acierto.

## Contrato de detección heredado

P6-04 no vuelve a ejecutar el detector. El artefacto audita lo que P6-03
persistió:

- `confidence` es `objectness * class_probability`, calculada por el detector.
- El piso documentado de YOLOX es **0.10**; el filtro de salida conserva
  puntuaciones **estrictamente mayores** que ese piso.
- La NMS es **class-agnostic**, con umbral documentado **0.45**.
- Si el `score_threshold` persistido es **estrictamente mayor que 0.10**, el
  artefacto marca `low_scores_observable: false` y emite una advertencia: las
  puntuaciones bajas pudieron descartarse antes de P6-03 y **no pueden
  recuperarse** en P6-04.
- Una diferencia de modelo, SHA-256, preprocesamiento, piso de score o NMS
  cambia la identidad y separa series; no se comparan puntuaciones entre
  identidades distintas.

## Denominador y estados

El denominador es el número de **detecciones puntuadas ya persistidas y
posteriores al filtrado del detector**, contadas una sola vez. No son frames,
no son eventos y no se reconstruyen detecciones descartadas. Los frames sin
detecciones, descartados por muestreo, con error técnico o sin puntuación
quedan fuera del denominador, aunque sí aparecen en el conteo de registros.

Estados de frame (`evaluation.records`):

| Estado | Significado |
|---|---|
| `scored` | Registro `processed` con al menos una detección puntuada. |
| `no_detections` | Registro `processed` con `detections: []`. |
| `discarded` | Frame descartado por muestreo. |
| `technical_error` | `read_error` o `detector_error`. |
| `no_score` | Registro de imagen con detecciones sin puntuaciones utilizables; el JSONL P6-03 válido actual no produce este caso. |
| `end_of_stream` | Marcador de cierre; no es un frame ni una detección sin score. |

Una observación que mezcla detecciones con y sin `confidence` es inválida: no
se puede construir un denominador auditable y el comando falla.

La referencia tiene estados `ok`, `insufficient` o `incompatible`. El cambio
sostenido tiene estados `sustained_change`, `no_sustained_change`,
`insufficient_evidence` o `reference_incompatible`.

## Referencia, ventanas y cambio sostenido

La referencia **externa** se declara con `--reference` y es un JSONL P6-03
separado. Sus frames no se cuentan en la evaluación del lote de entrada y sus
puntuaciones tampoco se mezclan con el denominador. Debe cumplir
`--reference-min-frames` y `--reference-min-scores`; además, su identidad de
modelo y filtros debe coincidir. Si no coincide, la referencia es
`incompatible`; si no alcanza los mínimos o tiene errores técnicos/registros
con detecciones sin score, es
`insufficient`. Si `--reference` apunta al mismo archivo que `--input`, el
comando falla: omite `--reference` para usar la referencia interna.

La referencia **interna** (sin `--reference`) toma el primer bloque de frames
puntuados y lo excluye de la evaluación. Un error técnico o registro con
detecciones sin score la deja insuficiente; los cuadros sin detecciones solo
quedan fuera de sus puntuaciones. No es evidencia independiente: sirve para operar
un lote único, no para acreditar C1/C2.

La evaluación se divide en **ventanas consecutivas no solapadas** de
`window_frames` (5 por omisión) sobre el orden real de los frames muestreados.
Una ventana es **completa** solo si todos esos frames tienen detecciones
puntuadas y hay al menos `window_min_scores` (3) puntuaciones. Un frame
muestreado sin detección o con error ocupa su lugar: no se unen cuadros
separados para fabricar continuidad. Los registros `discarded` son cuadros
**no muestreados** y no ocupan lugar en la ventana. Una ventana incompleta se marca con
`partial_window`, `unscored_or_missing_frame` o
`insufficient_scored_detections` y **corta la racha**.

Una ventana completa es **degradada** si se cumplen ambas condiciones:

1. la fracción de puntuaciones en zona `low` es al menos `low_fraction_min`
   (0.5 por omisión), y
2. la media de confianza cae al menos `mean_drop_min` (0.10) respecto de la
   media de la referencia.

Hay **cambio sostenido** si se observan `consecutive_windows` (2) ventanas
completas degradadas consecutivas. La política también exige al menos esa
cantidad de ventanas completas para poder concluir algo. Si no hay cambio
sostenido pero quedó alguna ventana incompleta, el estado también es
`insufficient_evidence`, no normalidad.

## Insuficiencia no es normalidad

`normality_established` solo puede ser `true` cuando el estado es
`no_sustained_change`, es decir, cuando la referencia fue `ok` y hubo suficientes
ventanas completas, ninguna incompleta y ninguna racha degradada. Si faltan detecciones, ventanas
completas, referencia válida o identidad compatible, el resultado es
`insufficient_evidence` (o `reference_incompatible`) y
`normality_established` permanece en `false`.

El artefacto declara explícitamente
`insufficiency_is_not_normality: true`. Un lote vacío, una secuencia sintética
sin objetos o una referencia insuficiente **no** se interpretan como escena
normal ni como ausencia de problema.

## Contrato para P6-05

El bloque `p6_05_contract` obliga al consumidor a:

- usar `policy_id` como identidad de la política y `sustained_change` como
  fuente de decisión;
- leer `sustained_change.status`, `evidence_sufficient`,
  `normality_established` y `evaluation.denominator`;
- no reclasificar las puntuaciones;
- no crear normalidad cuando `evidence_sufficient` sea `false`;
- no comparar políticas con `policy_id` distinto.

P6-04 no crea eventos, alertas ni capturas. P6-05 deberá decidir cómo
materializarlos **sin** alterar esta política ni presentar la insuficiencia como
normalidad.

## Reproducción local

Comandos usados para verificar esta etapa, sin descargar pesos ni abrir la
fuente:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe evaluar_confianza.py --input .\datos_locales\observaciones\demo-p6-03.jsonl
```

Resultado observado: **89 pruebas correctas**. Sobre el JSONL sintético
`demo-p6-03`, el artefacto contiene 13 registros (8 `discarded`, 4
`no_detections`, 1 `end_of_stream`, 0 `scored`), denominador 0, referencia
`insufficient` y cambio sostenido `insufficient_evidence` con
`normality_established: false`. La secuencia sintética no contiene objetos
COCO; por eso **no demuestra zonas ni cambio sostenido**, solo el manejo de
insuficiencia.

### Comprobación local con video real (13 nov 2026)

Se capturaron dos segmentos consecutivos de 60 s de un video urbano mostrado
en el monitor 2, mediante el grabador local MJPEG. P6-03 ejecutó YOLOX-Tiny
en CPU con `--sample-every 15`, piso 0.10 y NMS class-agnostic 0.45. El primer
segmento se usó solo como referencia externa y el segundo como evaluación;
ambos mantuvieron la misma identidad de modelo y filtros.

| Segmento | ID de corrida | Cuadros puntuados | Detecciones puntuadas | Sin detecciones | Errores técnicos |
|---|---|---:|---:|---:|---:|
| Referencia | `monitor-p6-04-20261113-214440` | 47 | 600 | 2 | 0 |
| Evaluación | `monitor-p6-04-20261113-214641` | 42 | 681 | 1 | 0 |

Los SHA-256 de los JSONL locales son, respectivamente,
`98216858e7323f52fd9c1b0c769325a48d1c748aa22fa4cb5631ab6e4423cc2e`
y `69bd3e340dc7fee6dcef386c0a371870d29365951377742e93090fab2975d0fc`.
Los archivos están en `datos_locales/observaciones/`, ignorados por Git:
esta tabla acredita la corrida local, pero **no permite reproducirla desde el
repositorio sin el video y el grabador**.

La referencia tuvo media 0.399193 y cuartiles de confianza q25=0.168422,
q50=0.328033, q75=0.611746. Con 0.30/0.70, la evaluación clasificó 353
puntuaciones como `low`, 193 como `doubt` y 135 como `accepted`. Se ensayó
además una configuración **exploratoria específica de esta fuente** con
0.17/0.61, redondeando q25/q75 de la referencia: 191 `low`, 313 `doubt` y
177 `accepted`. En ambos casos el estado global fue `insufficient_evidence`
y `normality_established: false` debido a un cuadro sin detecciones que dejó
una ventana incompleta y a la ventana parcial final. No hubo error técnico.

Con los JSONL locales disponibles, la segunda evaluación se reproduce así:

```powershell
.\.venv\Scripts\python.exe .\evaluar_confianza.py `
  --input .\datos_locales\observaciones\monitor-p6-04-20261113-214641.jsonl `
  --reference .\datos_locales\observaciones\monitor-p6-04-20261113-214440.jsonl `
  --lower 0.17 --upper 0.61 `
  --output .\datos_locales\politicas\monitor-p6-04-20261113-214641-repro.json `
  --threshold-justification "Cuartiles redondeados de 600 puntuaciones de referencia; solo exploratorio"
```

Estos puntajes **no son etiquetas de acierto**, los dos segmentos provienen
del mismo video y la cámara recorre escenas distintas. Por ello 0.17/0.61 no
se promueven a valores predeterminados ni se acredita C1/C2; para eso faltan
escenas de referencia y evaluación con condición conocida. La corrida sí
comprueba que se conservan scores bajos, se separan cuadros vacíos y no se
declara normalidad ante evidencia incompleta.

### Repetición con ventanas completas (13 nov 2026)

Se repitió la captura del mismo stream local con `--sample-every 15` y
`--max-frames 361`: los cuadros recibidos 1, 16, ..., 361 forman exactamente
25 cuadros muestreados y cinco ventanas de cinco. El detector procesó los 25
sin fallos ni cuadros sin detecciones; produjo 339 detecciones puntuadas.
`recalcular_observaciones.py` devolvió `coincide: true`.

El JSONL local de la corrida `monitor-p6-04-ventanas-20261113-220011` tiene
SHA-256 `ea8c905d34fc15540f6222e9f4ef5a28c745b882ea31d5795614683f9e952b01`.
Al evaluarlo contra la referencia externa anterior, sin modificar las
puntuaciones, con umbrales exploratorios 0.17/0.61, resultaron 96 `low`,
154 `doubt` y 89 `accepted`. Las cinco ventanas fueron completas y ninguna
degradada: `sustained_change.status: no_sustained_change`,
`evidence_sufficient: true` y `normality_established: true`. Esta última
marca significa **solo** ausencia de cambio sostenido de confianza bajo la
regla configurada; no demuestra seguridad de la escena, calidad del detector
ni los escenarios completos C1/C2.

Para recalcularla en el equipo que conserva ambos JSONL locales:

```powershell
.\.venv\Scripts\python.exe .\recalcular_observaciones.py --input .\datos_locales\observaciones\monitor-p6-04-ventanas-20261113-220011.jsonl
.\.venv\Scripts\python.exe .\evaluar_confianza.py `
  --input .\datos_locales\observaciones\monitor-p6-04-ventanas-20261113-220011.jsonl `
  --reference .\datos_locales\observaciones\monitor-p6-04-20261113-214440.jsonl `
  --lower 0.17 --upper 0.61 `
  --threshold-justification "Cuartiles redondeados de referencia previa; exploratorio" `
  --output .\datos_locales\politicas\monitor-p6-04-ventanas-20261113-220011-repro.json
```

Los JSONL, el video y el grabador no están versionados: esta comprobación
documenta el resultado local, pero **no es una reproducción autónoma desde un
clon limpio**. La evidencia completa de C1/C2 deberá conservar entradas o
capturas accesibles y los eventos, alertas y consultas correspondientes.

## Trazabilidad con la rúbrica R3

R3 corresponde al criterio de evaluación del curso; su texto no se versiona en
este repositorio. Esta etapa aporta la evidencia documental verificable que R3
exige para P6-04:

| Requisito verificable | Dónde se demuestra |
|---|---|
| Uso reproducible del CLI | Comandos y tabla de opciones de este documento; ayuda `--help`. |
| Artefacto versionado y auditable | `schema`, `policy_id`, `configuration_digest`, `input` e `identity`. |
| Umbrales justificados sin exagerar evidencia | Origen exploratorio, cinco scores de una imagen y `robust_reference: false`. |
| Separación entre C1/C2 y datos sintéticos | Se declara que 0.30/0.70 no son línea base válida y que la demo no demuestra la política. |
| Denominador explícito | Conteo de detecciones puntuadas post-filtro, no frames. |
| Estados y cambio sostenido verificables | Tablas de estados, ventanas no solapadas, mínimos y racha consecutiva. |
| Insuficiencia tratada como estado | `insufficient_evidence` y `normality_established: false`. |
| Contrato de consumo definido | Bloque `p6_05_contract` y validación de artefacto. |
| Límites y no sobreafirmación | `limitations`, referencias internas no independientes y ausencia de eventos/alertas. |

La rúbrica se satisface **solo para el alcance de P6-04**; no convierte esta
etapa en entrega completa ni sustituye la evidencia pendiente de C1/C2, P6-05,
métricas, eventos y portal.

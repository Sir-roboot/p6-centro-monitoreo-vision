# P6-06 · Reglas y alertas persistentes

`alertas.py` convierte **eventos P6-05 ya persistidos** y la **política P6-04**
que esos eventos referencian en alertas explícitas. No ejecuta el detector, no
aplica NMS, no reclasifica puntuaciones, no modifica eventos ni capturas y no
interpreta la falta de evidencia como normalidad. `ejecutar_alertas.py` publica
las alertas y `consultar_alertas.py` las relee y verifica después de reiniciar,
sin cargar el modelo.

La salida es un JSONL versionado con una línea de manifiesto y una línea por
alerta:

```text
datos_locales/alertas/<run_id>.jsonl
```

El manifiesto conserva las reglas declaradas, su configuración, los conteos, un
digest de las alertas, el contrato de consulta para el portal y las
limitaciones. Cada alerta vincula eventos y capturas mediante `event_ids` y
`window.event_refs`; no crea una detección ni una zona nueva. El archivo se
escribe con temporal y `os.replace`; una corrida ya publicada **no se
sobrescribe** y no hay expiración automática ni TTL.

## Reglas declaradas

Las tres reglas se registran en el manifiesto y se copian completas en cada
alerta (`rule`, `rule_config`, `rule_id`, `rule_version`). Cambiar sus umbrales
exige una corrida nueva y queda visible en el artefacto.

| `rule_id` | Severidad | Condición | Evidencia que usa |
| --- | --- | --- | --- |
| `p6-06-low-confidence` | `warning` | Uno o más eventos `scored` de una ventana contienen al menos `low_min_detections` detecciones en zona `low`; se exigen `low_min_events_with_low` eventos con baja dentro de `low_window_events`. | `events[].zones` persistidas de P6-05. **No** depende de `sustained_change`. |
| `p6-06-sustained-change` | `critical` | `sustained_change.status == "sustained_change"` **y** `evidence_sufficient == true` en P6-04. | Ventanas completas degradadas de `evaluation.windows`; sus frames se vinculan con eventos P6-05. |
| `p6-06-insufficient-evidence` | `info` | `evidence_sufficient == false` o estado `insufficient_evidence` / `reference_incompatible`. | La política y los eventos disponibles; la insuficiencia **no** se convierte en normalidad. |

Por omisión, la regla de baja confianza usa ventanas de **un** evento con **una**
detección `low` como mínimo. Es una regla exploratoria, no una calibración:
`p6-06-low-confidence` puede dispararse aunque P6-04 diga
`no_sustained_change`. Esa separación permite acreditar C2 sin inventar drift,
pero una detección `low` **no** demuestra por sí sola un fallo del detector ni
un cambio de la escena.

Los eventos `no_detections`, `technical_error` y `no_score` nunca cuentan como
baja confianza ni como normalidad. Si no hay eventos puntuados, la regla de baja
confianza no dispara y la ausencia de evidencia se reporta con la regla
informativa. `p6-06-sustained-change` no recalcula la política: solo consume el
estado que P6-04 ya declaró. `p6-06-insufficient-evidence` siempre lleva
`evidence_sufficient: false` y `normality_established: false`.

## Uso

El CLI de P6-05 invoca P6-06 automáticamente al final de una corrida:

```powershell
$runId = "p6-06-" + (Get-Date -Format "yyyyMMdd-HHmmss")
.\.venv\Scripts\python.exe .\ejecutar_eventos.py `
  --source http://127.0.0.1:8765/stream.mjpg `
  --source-id monitor-video --sample-every 15 --max-frames 100 `
  --run-id $runId --no-download
```

Al terminar se publican observaciones, política, eventos, capturas y
`datos_locales/alertas/<run_id>.jsonl`. Para regenerar solo las alertas de una
corrida ya publicada, sin volver a inferir:

```powershell
.\.venv\Scripts\python.exe .\ejecutar_alertas.py --run-id $runId
```

Opciones de la regla de baja confianza:

```powershell
.\.venv\Scripts\python.exe .\ejecutar_alertas.py --run-id $runId `
  --low-min-detections 2 --low-window-events 5 --low-min-events-with-low 3
```

`--policy` permite indicar explícitamente la política P6-04; `--generated-at`
fija el instante de generación para una prueba reproducible. El comando imprime
un JSON con la ruta del artefacto y el resumen. Falla si la corrida no existe,
si la política no corresponde a los eventos, si el archivo ya existe o si los
datos no permiten construir una alerta verificable.

## Consulta y verificación

```powershell
.\.venv\Scripts\python.exe .\consultar_alertas.py --run-id $runId
.\.venv\Scripts\python.exe .\consultar_alertas.py --run-id $runId --verify
.\.venv\Scripts\python.exe .\consultar_alertas.py `
  --run-id $runId --rule-id p6-06-low-confidence --severity warning --limit 5
.\.venv\Scripts\python.exe .\consultar_alertas.py --list-runs
```

`--verify` recorre cada `window.event_refs` y exige igualdad exacta con el
evento P6-05: `event_id`, `observation_id`, `frame_id`, `frame_number`,
`status`, `occurred_at`, `policy_id`, `policy_ref` y `capture` (con `path` y
`sha256`). No basta con que exista un JPEG válido: una captura sustituida por
otra captura válida también se rechaza porque el `sha256` no coincide con el del
evento de origen. Cada captura referenciada se abre además con `abrir_captura`
para comprobar que su contenido sigue intacto. También comprueba que el
`policy_ref` y el `policy_id` del manifiesto aparezcan en los eventos y, si la
corrida tiene **0 eventos**, que la política enlazada exista, coincida con
`policy_id` y pertenezca a la misma corrida. Devuelve un JSON con `ok`, conteos
y `failures`; el código de salida es distinto de cero si la verificación falla.

## Contrato de los artefactos

La primera línea es el manifiesto (`record: "alert_manifest"`,
`schema: "p6-06/1"`) con:

- identidad de la corrida y de la política: `run_id`, `policy_id`,
  `policy_status`, `policy_reason`, `policy_ref`, `events_ref`;
- conteos: `event_count`, `scored_event_count`, `unscored_event_count`,
  `alert_count`, `counts_by_rule`, `counts_by_severity`,
  `counts_by_occurrence_state`;
- `occurrence_state_contract`, que declara los valores permitidos, la asignación
  por severidad y que el campo es inmutable y **no** modela ciclo de vida;
- `rules`, `configuration`, `generated_at` y `alerts_digest`;
- `insufficiency_is_not_normality: true`, `persistence` sin expiración,
  `portal_contract` y `limitations`.

Cada línea siguiente es una alerta (`record: "alert"`) con `alert_id`
determinista, `dedupe_key`, `run_id`, `rule_id`, `rule_version`, `rule`,
`rule_config`, `severity`, `occurrence_state`, `reason`, `suggested_action`,
`occurred_at`, `generated_at`, `source`, `model`, `filters`, `policy_id`,
`policy_status`, `policy_reason`, `evidence_sufficient`,
`normality_established`, `policy_ref`, `events_ref`, `event_id`, `event_ids`,
`observation_ids`, `window`, `evidence` y `policy_config`.

`window` contiene `kind`, `event_ids`, `observation_ids`, `event_refs`,
`policy_window_indices`, el resumen de las ventanas y, cuando aplica, las zonas
`low` que dispararon la alerta. Cada `event_ref` copia `event_id`,
`observation_id`, `frame_id`, `frame_number`, `occurred_at`, `status`,
`policy_id`, `policy_ref` y `capture` (con `path` y `sha256`), de modo que el
portal puede enlazar la alerta con la evidencia sin volver a inferir.

La API pública para un consumidor posterior es:

```python
from alertas import (
    consultar_alertas, leer_manifiesto_alertas,
    listar_runs_con_alertas, resumen_alertas,
    verificar_evidencia_alertas,
)
```

`leer_manifiesto_alertas` valida el JSONL completo y rechaza un archivo alterado;
`consultar_alertas` devuelve solo las alertas en el orden persistido (baja
confianza, cambio sostenido, insuficiencia). El
`portal_contract` del manifiesto declara los campos mínimos que deberá mostrar
R5.3: `occurred_at`, `rule_id`, `severity`, `occurrence_state`, `reason`,
`suggested_action`, `event_id`, `event_ids` y `window`. También declara los
valores permitidos de `occurrence_state`, su asignación por severidad y que es
inmutable y ajeno al ciclo de vida.

## Estado de ocurrencia y corridas sin eventos

Cada alerta lleva `occurrence_state`, un estado **explícito e inmutable** que
explica por qué existe la alerta:

| Valor | Se asigna cuando | Significado |
| --- | --- | --- |
| `triggered` | severidad `warning` o `critical` | La regla se cumplió sobre la evidencia. |
| `informational` | severidad `info` | La alerta reporta calidad de evidencia (insuficiencia), no una condición de escena. |

La asignación se deriva de la severidad (`warning`/`critical` → `triggered`,
`info` → `informational`) y está declarada en
`occurrence_state_contract.assignment`. El campo **no** es un estado de ciclo de
vida: P6-06 no modela `active`, `resolved`, reconocimiento, cierre ni
expiración. La lectura rechaza cualquier otro valor, cualquier valor que no
corresponda a la severidad de la regla y, explícitamente, `active`/`resolved`.

Un stream que no aporta cuadros muestreados produce una corrida P6-05 válida con
**0 eventos** y un archivo `eventos/<run_id>.jsonl` vacío. En ese caso P6-06 no
fabrica detecciones, eventos ni capturas: emite exactamente una alerta
`p6-06-insufficient-evidence` con `severity: info`,
`occurrence_state: informational`, `event_id: null`, `occurred_at: null`,
`event_ids: []` y `event_refs: []`. La alerta queda enlazada a la política
mediante `policy_id`/`policy_ref`, y su `evidence` declara
`persisted_events: 0`, `evidence_origin: "policy_only"` y
`detections_created: 0`. La verificación comprueba entonces que la política
enlazada exista, coincida con `policy_id` y pertenezca a la misma corrida.

## Corrida local verificada

Sobre la corrida P6-05 `p6-05-verificacion-20261113-222520` (10 eventos, 10
JPEG) se ejecutó P6-06 después, en un proceso que solo leyó los artefactos
persistidos. El resultado fue `policy_status: no_sustained_change`,
`alert_count: 10`, todas `p6-06-low-confidence` con severidad `warning`;
`p6-06-sustained-change`: 0 y `p6-06-insufficient-evidence`: 0. La consulta
`--verify` comprobó los 10 eventos, los 10 hashes de captura y devolvió
`ok: true` sin fallos. Esto demuestra que una alerta de baja confianza puede
existir sin que P6-04 declare cambio sostenido; **no** demuestra drift ni
calidad del detector.

SHA-256 del manifiesto local de alertas:
`b90bb89bce3e77f749903889c4d21921890817e92cf4d906561ec8922b7311e0`.
El archivo se regeneró con el contrato actual (`occurrence_state`, `policy_id` en
cada `event_ref` y captura exacta) y está en `datos_locales/` (ignorado por
Git), por lo que un clon limpio no puede reabrir esta comprobación.

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_alertas -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Las pruebas de P6-06 usan eventos P6-05 reales producidos por el detector falso
de la suite; no inventan detecciones ni saltan la persistencia. Cubren:
disparo conjunto de baja confianza y cambio sostenido, baja confianza sin
cambio sostenido, insuficiencia que no acredita normalidad, corrida sin alertas,
persistencia/resumen/verificación, no sobrescritura, deduplicación determinista,
archivo corrupto, `run_id` inseguro y el ciclo completo del CLI. Además cubren
`occurrence_state` explícito, inmutable y ajeno al ciclo de vida; insuficiencia
marcada como `informational`; `policy_id` y captura exacta en cada `event_ref`;
detección de `policy_id` alterado; rechazo de una captura sustituida por otra
captura válida; y una corrida con **0 eventos** que produce solo la alerta
informativa enlazada a la política.

La suite completa ejecutó **111 pruebas** correctas el 13 nov 2026. Las
imágenes y los eventos de prueba son sintéticos: validan el contrato y **no
sustituyen** una demostración C1/C2.

## Límites

- **R5.3 sigue pendiente**: P6-06 no incluye portal, navegador ni interfaz. Solo
  deja artefactos, funciones de consulta y el contrato de campos para P6-07/P6-08.
- No hay envío de correo, notificaciones, reconocimiento de personas,
  resolución automática de alertas ni expiración automática.
- Las alertas se derivan de artefactos locales de P6-05/P6-04; un clon limpio no
  puede reabrir el video, los eventos ni las capturas de una corrida local.
- Una alerta de baja confianza no demuestra drift, fallo del detector ni
  calidad predictiva. Una alerta de cambio sostenido solo refleja la regla
  exploratoria de P6-04.
- La evidencia insuficiente se registra como alerta informativa, nunca como
  normalidad; no cerrar la revisión como escena normal.
- `occurrence_state` (`triggered`/`informational`) describe por qué existe la
  alerta y es inmutable; no es un estado activo/resuelto ni un ciclo de vida.
- Una corrida sin cuadros muestreados produce 0 eventos: solo la alerta
  informativa enlazada a la política, con `occurred_at: null` y sin detecciones,
  eventos ni capturas fabricados.
- Los umbrales 0.30/0.70 de P6-04 y la ventana de P6-06 siguen siendo
  exploratorios hasta completar C1/C2 con escenas reales y permiso de uso.

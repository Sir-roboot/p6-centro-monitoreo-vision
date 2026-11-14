# P6-09 · Demostraciones C1 y C2 sobre datos reales

Fecha de documentación: **2026-11-13 (local) / 2026-11-14 UTC**.
Issue: **#9 (P6-09)**. Este documento describe dos corridas ya realizadas y
persistidas en la laptop. **No vuelve a capturar, no reejecuta el detector, no
recalcula scores y no cambia umbrales, eventos ni alertas.** La evidencia
primaria son los artefactos locales; el repositorio solo versiona este texto y
un verificador de solo lectura.

## Veredicto honesto

Las dos corridas son **técnicamente coherentes**, pero **no son escenas
saludables ni están libres de baja confianza**:

| Corrida | Eventos | Capturas | Alertas | Alertas `low-confidence` | `technical_result` |
| --- | ---: | ---: | ---: | ---: | --- |
| C1 · `p6-09-c1-20261113-232205` | 10 | 10 | 10 | **10/10** | `ok` |
| C2 · `p6-09-video-20261113-230341` | 10 | 10 | 10 | **10/10** | `ok` |

En C1, **33 de 156 detecciones puntuadas** quedaron en zona `low`
(21.1538 %); en C2 fueron **60 de 217** (27.6498 %). Por tanto, sería incorrecto
afirmar que C1 no alerta, que C1/C2 no tienen baja confianza o que la escena es
normal. Lo que sí acreditan los artefactos es que el servicio adquirió la señal,
muestreó, infirió, persistió eventos y capturas, evaluó la política, generó
alertas y produjo métricas y vistas de portal de forma trazable.

Términos que se usan con precisión en este documento:

- **`technical_result=ok`**: no hubo errores técnicos de lectura o inferencia en
  la corrida; no significa acierto predictivo.
- **`normality_established=true`** en la política: no se observaron
  `consecutive_windows` ventanas completas degradadas. No significa que la
  escena sea saludable ni que no existan detecciones `low`.
- **Alerta `p6-06-low-confidence`**: regla independiente de
  `sustained_change`; puede dispararse aunque la política diga
  `no_sustained_change`, como ocurre en ambas corridas.
- **`accepted`**: decisión de la política de confianza, no una etiqueta de
  verdad ni una validación del detector.

## Fuente y cadena local

La señal observada fue un video público mostrado en el **monitor 2** de la
laptop y capturado por un grabador local de pantalla fuera de este repositorio:

- Fuente pública mostrada: <https://www.youtube.com/watch?v=s_qzxu4Xjao>
- Señal local consumida por el pipeline: `http://127.0.0.1:8765/stream.mjpg`
- `source.kind`: `stream`; `source.id`: `monitor-video`

La URL pública se cita solo como procedencia. Su disponibilidad, contenido y
derechos pueden cambiar; el video no se redistribuye. Los cuadros JPEG, los
JSONL, la política generada y los reportes permanecen **solo en la laptop** y
están excluidos por `.gitignore` (`datos_locales/`, `models/`, `*.mp4`, etc.).
Un clon limpio del repositorio no puede reconstruir estas corridas sin esos
artefactos y sin el modelo.

Cadena efectiva:

```text
video público en monitor 2
  → grabador local 127.0.0.1:8765 (fuera del repositorio)
  → ejecutar_eventos.py (P6-03 captura/muestreo + P6-02 detector + P6-04 política + P6-05 eventos + P6-06 alertas)
  → datos_locales/{observaciones,politicas,eventos,capturas,alertas}/
  → calcular_metricas.py (P6-07)
  → portal.py (P6-08)
```

## Comando reproducible equivalente

No se guardó la línea de shell original. El siguiente comando se reconstruye
desde `config`, `source`, `model`, `filters` y la política persistidos, y
reproduce la misma configuración de muestreo e inferencia:

```powershell
.\\.venv\\Scripts\\python.exe .\\ejecutar_eventos.py `
  --source http://127.0.0.1:8765/stream.mjpg `
  --source-id monitor-video `
  --sample-every 15 `
  --max-frames 136 `
  --seconds 0 `
  --interval-ms 200 `
  --run-id p6-09-c1-20261113-232205 `
  --reference .\\datos_locales\\observaciones\\monitor-p6-04-20261113-214440.jsonl `
  --lower 0.17 `
  --upper 0.61 `
  --threshold-justification "Cuartiles redondeados de referencia previa; exploratorio" `
  --no-download
```

Para C2, el único cambio de identidad es el `run_id`:

```powershell
.\\.venv\\Scripts\\python.exe .\\ejecutar_eventos.py `
  --source http://127.0.0.1:8765/stream.mjpg `
  --source-id monitor-video `
  --sample-every 15 `
  --max-frames 136 `
  --seconds 0 `
  --interval-ms 200 `
  --run-id p6-09-video-20261113-230341 `
  --reference .\\datos_locales\\observaciones\\monitor-p6-04-20261113-214440.jsonl `
  --lower 0.17 `
  --upper 0.61 `
  --threshold-justification "Cuartiles redondeados de referencia previa; exploratorio" `
  --no-download
```

`--max-frames 136` recibe 136 cuadros y muestrea los números 1, 16, 31, …, 136:
**10 inferencias**, equivalentes a dos ventanas completas de cinco cuadros. No
se debe reejecutar este comando para “mejorar” la evidencia: una corrida nueva
tendría otro `run_id` y otros resultados. Los comandos de consulta y verificación
de este documento son de solo lectura.


## Identidad de configuración, modelo y política

Los valores siguientes están en los artefactos de **ambas** corridas y son
idénticos salvo el `run_id` y los conteos de resultados:

| Campo | Valor persistido |
| --- | --- |
| Código de referencia al documentar | `8b23b31` (`chore: tidy metric and portal file endings`) |
| Modelo | YOLOX-Tiny COCO, formato ONNX, versión `0.1.1rc0` |
| SHA-256 del modelo | `427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7` |
| Runtime | `opencv-dnn`, OpenCV `5.0.0`, CPU |
| Entrada / filtros | 416×416, letterbox abajo-derecha, relleno 114, NMS class-agnostic 0.45, score threshold 0.10 |
| Python / esquema de captura | `3.14.3` / `p6-03/1` |
| `policy_id` | `p6-04-2a45934f907b96d9` |
| `configuration_digest` | `2a45934f907b96d9ac09557e36a2441cd6c0625246180b250f3e15d2a9a783f2` |
| `identity.sha256` de la política | `85cc8aab056805ab5f4963734872c0432b9a3eea62fdc2756cbd16211e543468` |
| Umbrales efectivos | `low < 0.17`; `doubt: 0.17 <= s < 0.61`; `accepted: s >= 0.61` |
| Referencia | `monitor-p6-04-20261113-214440`, 47 frames y 600 detecciones puntuadas |
| Justificación | `Cuartiles redondeados de referencia previa; exploratorio` |

La política declara los cuantiles de referencia
`q25=0.1684222478…`, `q50=0.3280327711…`, `q75=0.6117460944…` y
`robust_reference: false`. Los umbrales son **exploratorios**; no son una
calibración de precisión ni una validación de C1/C2 sin etiquetas. El artefacto
también advierte que `accepted` es una decisión de política, no una afirmación
de que la detección sea correcta.

> **Discrepancia documental detectada:** la cadena de texto heredada
> `limitations` de la política menciona “0.30/0.70”, pero los campos efectivos
> `thresholds.lower=0.17` y `thresholds.upper=0.61`, las zonas y la
> justificación usan 0.17/0.61. No se modificó el artefacto. El verificador y
> este documento usan los campos efectivos, no esa frase genérica.

El commit no está persistido dentro de los JSONL. `8b23b31` es el `HEAD` con el
que se redactó y verificó este documento; no se puede reconstruir el commit
desde los artefactos locales.

## C1 · `p6-09-c1-20261113-232205`

Corrida real con `technical_result=ok`, **10 eventos**, **10 capturas** y
**10 alertas** de baja confianza. No es una escena saludable.

### Captura, muestreo y política

| Dato | Valor |
| --- | --- |
| Inicio / fin (UTC) | `2026-11-14T05:22:06.377480+00:00` / `2026-11-14T05:22:17.090983+00:00` |
| Duración | `10713.459 ms` |
| Cuadros recibidos / procesados / descartados | `136 / 10 / 126` |
| Motivo de descarte | `sampling_interval` |
| Fin de captura | `max_frames` |
| Errores de lectura / detector | `0 / 0` |
| Detecciones totales | `156` |
| Eventos puntuados / sin puntuación | `10 / 0` |
| Zonas | `accepted=59`, `doubt=64`, `low=33` |
| Confianza media | `0.460695` |
| Ventanas completas / incompletas | `2 / 0` |
| Estado de política | `no_sustained_change`; `evidence_sufficient=true`; `normality_established=true` |

La política no declara cambio sostenido porque ninguna de las dos ventanas
alcanzó la fracción de baja confianza requerida (`low_fraction_min=0.5`) junto
con una caída media de `0.1`; `max_consecutive_degraded_windows=0`. Eso **no
elimina** las 33 detecciones `low`: por eso P6-06 emitió 10 alertas de baja
confianza, una por evento.

### Métricas G1–G7

Reporte: `datos_locales/reportes/p6-07-p6-09-c1-20261113-232205.json`,
`schema=p6-07/1`, generado en `2026-11-14T05:24:48.722465+00:00`.

| Métrica | Valor | Numerador / denominador | Lectura honesta |
| --- | ---: | ---: | --- |
| G1 · errores técnicos | `0.0` | `0 / 10` | Sin errores de inferencia en los 10 cuadros muestreados. |
| G2 · descartes | `0.926471` | `126 / 136` | 92.6471 % descartado por muestreo; no es pérdida por fallo. |
| G3 · disco disponible | `0.308893` | `157856677888 / 511040286720` | Instantánea del disco al generar el reporte; cambia con el tiempo. |
| G4 · detecciones `low` | `0.211538` | `33 / 156` | **21.1538 % de las detecciones puntuadas están en baja confianza.** |
| G5 · trazabilidad | `1.0` | `10 / 10` | Todos los eventos persistidos tienen captura y vínculo verificable. |
| G6 · antigüedad del reporte | `151.718 s` | `151.718 / 1` | Reporte generado ~2.5 min después del último cuadro; no es fallo de fuente. |
| G7 · tiempo medio de alerta | `5.36427 s` | `53.642697 / 10` | Promedio `generated_at - occurred_at` de las 10 alertas `triggered`. |

### Alertas

El JSONL `datos_locales/alertas/p6-09-c1-20261113-232205.jsonl` tiene
**11 líneas**: 1 manifiesto + 10 alertas.

| Regla | Severidad | Conteo | Estado de ocurrencia |
| --- | --- | ---: | --- |
| `p6-06-low-confidence` | `warning` | `10` | `triggered` |
| `p6-06-sustained-change` | `critical` | `0` | — |
| `p6-06-insufficient-evidence` | `info` | `0` | — |

Primer evento: `c5a84d6e641a2891e8f72fd9e97c0dcf`.
Primera alerta: `p6-06-18c3b761b5d8a860b322cafea5a7eaa4`.
Las 10 alertas están enlazadas a eventos existentes y sus capturas se
verificaron; no hay alertas huérfanas.


## C2 · `p6-09-video-20261113-230341`

Segunda corrida real, misma cadena y misma configuración de modelo y política.
También es `technical_result=ok`, con **10 eventos**, **10 capturas** y
**10 alertas** de baja confianza. No es una escena saludable.

### Captura, muestreo y política

| Dato | Valor |
| --- | --- |
| Inicio / fin (UTC) | `2026-11-14T05:03:42.462885+00:00` / `2026-11-14T05:03:55.575814+00:00` |
| Duración | `13112.932 ms` |
| Cuadros recibidos / procesados / descartados | `136 / 10 / 126` |
| Motivo de descarte | `sampling_interval` |
| Fin de captura | `max_frames` |
| Errores de lectura / detector | `0 / 0` |
| Detecciones totales | `217` |
| Eventos puntuados / sin puntuación | `10 / 0` |
| Zonas | `accepted=53`, `doubt=104`, `low=60` |
| Confianza media | `0.393105` |
| Ventanas completas / incompletas | `2 / 0` |
| Estado de política | `no_sustained_change`; `evidence_sufficient=true`; `normality_established=true` |

C2 tiene más detecciones y más baja confianza que C1, pero la política tampoco
declara cambio sostenido: `max_consecutive_degraded_windows=0`. La regla de baja
confianza sí se disparó en los 10 eventos. Esto refuerza la separación entre
**política de cambio sostenido** y **alerta de baja confianza puntual**.

### Métricas G1–G7

Reporte: `datos_locales/reportes/p6-07-p6-09-video-20261113-230341.json`,
`schema=p6-07/1`, generado en `2026-11-14T05:19:31.583759+00:00`.

| Métrica | Valor | Numerador / denominador | Lectura honesta |
| --- | ---: | ---: | --- |
| G1 · errores técnicos | `0.0` | `0 / 10` | Sin errores de inferencia en los 10 cuadros muestreados. |
| G2 · descartes | `0.926471` | `126 / 136` | 92.6471 % descartado por muestreo; no es pérdida por fallo. |
| G3 · disco disponible | `0.308867` | `157843542016 / 511040286720` | Instantánea del disco al generar el reporte; cambia con el tiempo. |
| G4 · detecciones `low` | `0.276498` | `60 / 217` | **27.6498 % de las detecciones puntuadas están en baja confianza.** |
| G5 · trazabilidad | `1.0` | `10 / 10` | Todos los eventos persistidos tienen captura y vínculo verificable. |
| G6 · antigüedad del reporte | `936.114 s` | `936.114 / 1` | Reporte generado ~15.6 min después del último cuadro; es antigüedad del reporte, no caída de la fuente. |
| G7 · tiempo medio de alerta | `6.497354 s` | `64.973537 / 10` | Promedio `generated_at - occurred_at` de las 10 alertas `triggered`. |

G6 y G3 son instantáneas del momento de cálculo. No deben “reproducirse” con
otro reloj o disco ni interpretarse como salud de la escena.

### Alertas

El JSONL `datos_locales/alertas/p6-09-video-20261113-230341.jsonl` tiene
**11 líneas**: 1 manifiesto + 10 alertas.

| Regla | Severidad | Conteo | Estado de ocurrencia |
| --- | --- | ---: | --- |
| `p6-06-low-confidence` | `warning` | `10` | `triggered` |
| `p6-06-sustained-change` | `critical` | `0` | — |
| `p6-06-insufficient-evidence` | `info` | `0` | — |

Primer evento: `6159796f01c804ae0cd9feb1b91ab1d2`.
Primera alerta: `p6-06-9315e32c6233ef3e8d86167f01899f8c`.
Las 10 alertas están enlazadas a eventos existentes y sus capturas se
verificaron; no hay alertas huérfanas.

## Comparación breve C1 ↔ C2

| Aspecto | C1 | C2 |
| --- | --- | --- |
| `technical_result` | `ok` | `ok` |
| Eventos / capturas / alertas | `10 / 10 / 10` | `10 / 10 / 10` |
| Detecciones puntuadas | `156` | `217` |
| `low` / total | `33/156` (21.1538 %) | `60/217` (27.6498 %) |
| G1 / G2 / G5 | `0/10`; `126/136`; `10/10` | `0/10`; `126/136`; `10/10` |
| G4 | `0.211538` | `0.276498` |
| G6 / G7 | `151.718 s`; `5.36427 s` | `936.114 s`; `6.497354 s` |
| Política | `no_sustained_change` | `no_sustained_change` |
| Alertas de baja confianza | `10` | `10` |

Las dos corridas acreditan la **operación técnica y la trazabilidad**, no la
calidad de la escena ni del detector. C2 no es una repetición de C1: tiene otros
conteos, otros eventos, otras capturas y otro instante de reporte.


## Artefactos y hashes locales

La tabla fija la copia exacta usada para este documento. Los SHA-256 de
`observaciones`, `eventos` y `alertas` son los que el reporte P6-07 registra en
`inputs`; los de la política y el reporte se calcularon sobre los archivos
locales. Un cambio de hash indica que el artefacto ya no corresponde a esta
evidencia, aunque el nombre del `run_id` sea el mismo.

| Corrida | Artefacto | Registros | SHA-256 |
| --- | --- | ---: | --- |
| C1 | `observaciones/p6-09-c1-20261113-232205.jsonl` | 138 | `d76fe227f85577dd37a1043d48f8a3d8665142488ae3e07a0de7ef8be8fbd437` |
| C1 | `eventos/p6-09-c1-20261113-232205.jsonl` | 10 | `587b3cccd83ed19006c1606de0a49cf2df6f5479f9833701feebd6ecc1cf5f36` |
| C1 | `alertas/p6-09-c1-20261113-232205.jsonl` | 11 | `64f15fda531e40356cb06e6ce97477b4e25c0ea33c2c9960a227cf2af8fd2a0c` |
| C1 | `politicas/p6-09-c1-20261113-232205-p6-04.json` | 1 | `7664c2f10d88994ed5e95ffe54d09ed712905a34ca3010fe5cda5660c31050b0` |
| C1 | `reportes/p6-07-p6-09-c1-20261113-232205.json` | 1 | `b6e9cee15852219fe7e9d9689a60e3e2a7ba66c308fa9797156f5d8ea5ceeea9` |
| C2 | `observaciones/p6-09-video-20261113-230341.jsonl` | 138 | `5dc7a7ba1e0b72db8db8b8f62b3c0ec8bd06ee93f87b8f647ebcce822171b1a7` |
| C2 | `eventos/p6-09-video-20261113-230341.jsonl` | 10 | `1b97abc30b25e6685779244a1b45bcb8e8cd125600a933adfca76c297537afc1` |
| C2 | `alertas/p6-09-video-20261113-230341.jsonl` | 11 | `2a448b780107a99946e8a58f0ecfd36c8c8ad2a46dd2c9d7c7200f92bbabb3f6` |
| C2 | `politicas/p6-09-video-20261113-230341-p6-04.json` | 1 | `8b8bc70ee72081678d8584230457f4662c52b46746ecffb57282f953d527f8db` |
| C2 | `reportes/p6-07-p6-09-video-20261113-230341.json` | 1 | `dabab485caa2d4f589755ea65c70cdd3125a645a164100eac004285a6886a508` |

Las **20 capturas** (10 por corrida) viven en
`datos_locales/capturas/<hash-run>/`. Cada evento guarda la ruta relativa y el
SHA-256 de su JPEG; el portal y `abrir_captura` vuelven a verificarlo. Ejemplos
de los extremos:

| Corrida | Captura | SHA-256 |
| --- | --- | --- |
| C1 | `capturas/80563f149388311c/000001.jpg` | `1657e1beb0dbe259d38b5dc7133512ae8650c5008cbfc5d4dbfa3e793bb6d36e` |
| C1 | último JPEG de la corrida | `71ab0de535c164059276b6f7153aa6e6b1c2a30750119a04c6c2d1cc7f20e087` |
| C2 | `capturas/9105c7d7c1601794/000001.jpg` | `f07221c223b2251ba1e9e11d7cb3f084e7370d970c481dab0a3e26c07bbe94e1` |
| C2 | último JPEG de la corrida | `0f79a181e9a80be94063501dac0d00ecf095406fe4065788685c94ffa10c7c54` |

Los JPEG y JSONL **no se suben** al repositorio. Los hashes permiten comprobar
la copia local, no redistribuir los cuadros.

## Portal local (solo lectura)

Con los artefactos en `datos_locales/`:

```powershell
.\\.venv\\Scripts\\python.exe .\\portal.py `
  --root .\\datos_locales --port 8080 --metrics
```

El portal escucha solo en `127.0.0.1`, no calcula métricas ni modifica archivos.
Rutas para C1 (sustituya el `run_id` por el de C2 para la otra corrida):

| Vista | Ruta |
| --- | --- |
| Índice de corridas | `http://127.0.0.1:8080/` |
| Corrida C1 | `http://127.0.0.1:8080/portal/runs/p6-09-c1-20261113-232205` |
| Evento de ejemplo C1 | `http://127.0.0.1:8080/portal/runs/p6-09-c1-20261113-232205/events/c5a84d6e641a2891e8f72fd9e97c0dcf` |
| Alerta de ejemplo C1 | `http://127.0.0.1:8080/portal/runs/p6-09-c1-20261113-232205/alerts/p6-06-18c3b761b5d8a860b322cafea5a7eaa4` |
| Captura de ejemplo C1 | `http://127.0.0.1:8080/portal/runs/p6-09-c1-20261113-232205/captures/c5a84d6e641a2891e8f72fd9e97c0dcf` |
| Conteos derivados C1 | `http://127.0.0.1:8080/portal/runs/p6-09-c1-20261113-232205/metrics` |
| G1–G7 C1 | `http://127.0.0.1:8080/portal/runs/p6-09-c1-20261113-232205/metrics/g1-g7` |
| G1–G7 JSON C1 | `http://127.0.0.1:8080/portal/runs/p6-09-c1-20261113-232205/metrics/g1-g7.json` |
| Corrida C2 | `http://127.0.0.1:8080/portal/runs/p6-09-video-20261113-230341` |
| Evento de ejemplo C2 | `http://127.0.0.1:8080/portal/runs/p6-09-video-20261113-230341/events/6159796f01c804ae0cd9feb1b91ab1d2` |
| Alerta de ejemplo C2 | `http://127.0.0.1:8080/portal/runs/p6-09-video-20261113-230341/alerts/p6-06-9315e32c6233ef3e8d86167f01899f8c` |
| Captura de ejemplo C2 | `http://127.0.0.1:8080/portal/runs/p6-09-video-20261113-230341/captures/6159796f01c804ae0cd9feb1b91ab1d2` |
| G1–G7 C2 | `http://127.0.0.1:8080/portal/runs/p6-09-video-20261113-230341/metrics/g1-g7` |


## Verificación reproducible de la evidencia

`verificar_demostracion.py` es un verificador **de solo lectura**. Reutiliza
`consultar_eventos`, `abrir_captura`, `leer_manifiesto_alertas`,
`verificar_evidencia_alertas`, `resumen_alertas`, `reporte_metricas` y el
helper SHA-256 de P6-07; no duplica la lógica de negocio. Comprueba:

- que el `run_id`, la fuente, el modelo, los filtros y el muestreo de la
  cabecera P6-03 coincidan con la configuración documentada;
- que la política persistida conserve `policy_id`, `configuration_digest`,
  referencia y umbrales efectivos `0.17/0.61`;
- los conteos de cuadros, errores y `technical_result`;
- los SHA-256 de observaciones, eventos y alertas contra los hashes
  documentados y contra `inputs` del reporte P6-07;
- que existan 10 eventos y 10 capturas verificables por evento;
- que las zonas sumen el total esperado y que G4/G5 coincidan con el reporte;
- que las 10 alertas enlacen eventos existentes y que su evidencia sea válida;
- que el manifiesto declare `p6-06-low-confidence=10`.

```powershell
.\\.venv\\Scripts\\python.exe .\\verificar_demostracion.py `
  --root .\\datos_locales
```

Salida esperada: JSON con `"ok": true` para ambas corridas y código de salida
`0`. Si falta un artefacto, se reporta un error de esa corrida; no se inventan
datos. El verificador no mide precisión, recall ni calidad predictiva.

Consultas equivalentes sin cargar el modelo:

```powershell
.\\.venv\\Scripts\\python.exe .\\consultar_eventos.py `
  --root .\\datos_locales `
  --run-id p6-09-c1-20261113-232205 `
  --verify-captures

.\\.venv\\Scripts\\python.exe .\\consultar_alertas.py `
  --root .\\datos_locales `
  --run-id p6-09-c1-20261113-232205 `
  --verify
```

Repita con `--run-id p6-09-video-20261113-230341` para C2.

## Reproducción y límites

Para volver a comprobar la documentación en esta laptop:

```powershell
# 1. Verificador de las dos corridas
.\\.venv\\Scripts\\python.exe .\\verificar_demostracion.py --root .\\datos_locales

# 2. Consultas de solo lectura
.\\.venv\\Scripts\\python.exe .\\consultar_eventos.py --root .\\datos_locales --run-id p6-09-c1-20261113-232205 --verify-captures
.\\.venv\\Scripts\\python.exe .\\consultar_eventos.py --root .\\datos_locales --run-id p6-09-video-20261113-230341 --verify-captures

# 3. Portal local con métricas persistidas
.\\.venv\\Scripts\\python.exe .\\portal.py --root .\\datos_locales --port 8080 --metrics

# 4. Pruebas (las de P6-09 se omiten si no existen los artefactos locales)
.\\.venv\\Scripts\\python.exe -m unittest discover -s tests -v
```

No se debe volver a capturar para “corregir” estas cifras ni editar los JSONL
para que coincidan con el documento. Si se necesita una corrida nueva, use otro
`run_id`, documente que es otra evidencia y no sobrescriba la anterior.

Límites que deben acompañar cualquier lectura de este documento:

1. **Muestra pequeña:** 10 inferencias (dos ventanas de cinco cuadros) por
   corrida; no es una muestra representativa de una escena ni de un día.
2. **Sin etiquetas de verdad:** no hay ground truth para precisión, recall,
   falsos positivos o falsos negativos. G1–G7 describen el servicio, no
   aciertos.
3. **Baja confianza real:** 10/10 alertas `low-confidence` en cada corrida. No
   se puede afirmar que las escenas sean saludables ni que el detector esté
   calibrado.
4. **Umbrales exploratorios:** 0.17/0.61 se derivan de cuantiles de una
   referencia local de 47 frames/600 detecciones, con `robust_reference: false`.
   No son una calibración.
5. **Texto heredado inconsistente:** `limitations` de la política menciona
   0.30/0.70; los campos efectivos son 0.17/0.61. Se documenta, no se oculta ni
   se modifica el artefacto.
6. **`normality_established` no es salud:** solo indica que no se alcanzaron
   dos ventanas completas degradadas consecutivas.
7. **G3 y G6 son instantáneas:** el disco y la antigüedad del reporte dependen
   del momento de cálculo; C2 tiene G6 alto porque el reporte se generó después.
8. **Misma fuente y configuración:** C1 y C2 no son experimentos
   independientes; comparten modelo, política, referencia y tipo de señal.
9. **Sin datos versionados:** un clon limpio no tiene los JPEG/JSONL ni el
   modelo. El repositorio solo puede ejecutar las pruebas que se omiten o usan
   fixtures; no puede reconstruir la evidencia real.
10. **Commit no persistido:** los JSONL no guardan el hash del commit; `8b23b31`
    es la referencia de redacción, no un campo verificable del artefacto.
11. **Derechos de la fuente:** el video público se cita como procedencia; no se
    redistribuye ni se garantiza su disponibilidad futura.

## Criterio de cierre de esta documentación

- [x] Dos corridas reales con `run_id` distintos: C1 y C2.
- [x] Comandos equivalentes y configuración efectiva documentados.
- [x] Fuente pública y cadena local identificadas.
- [x] Eventos, capturas, alertas, política y reportes con conteos y hashes.
- [x] G1–G7 de ambas corridas, incluyendo G4/G5 y la advertencia de baja
      confianza.
- [x] Rutas de portal para evento, alerta, captura y métricas.
- [x] Verificador de solo lectura y pruebas locales.
- [x] Sin re-captura, sin cambios de scores/umbrales y sin subir JPEG/JSONL.
- [x] Declaración explícita: **C1/C2 son técnicamente coherentes, pero no son
      escenas saludables ni están libres de baja confianza**.

Este documento cierra la parte documental de la evidencia P6-09. **No declara
que C1/C2 estén superadas como escenas saludables ni que el detector tenga
precisión validada.**


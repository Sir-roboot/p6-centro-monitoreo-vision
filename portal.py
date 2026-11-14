"""Portal local P6-08 para leer eventos, alertas y capturas ya persistidos.

El portal es de solo lectura: no infiere, no recalcula zonas ni confianzas, no
genera eventos/alertas y no escribe artefactos. Usa exclusivamente la biblioteca
estandar, escucha solo en 127.0.0.1 y resuelve capturas por ``event_id`` a traves
de ``abrir_captura``, que verifica SHA-256 y confina la ruta a ``capturas/``.

Las metricas son **opcionales** (``--metrics``). Cuando se habilitan se exponen
en ``/portal/runs/<run_id>/metrics`` como conteos derivados de los artefactos
persistidos; no son las metricas G1-G7 ni acreditan precision del detector.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from html import escape
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import sys
from typing import Any, Mapping
from urllib.parse import urlsplit

from alertas import PATRON_ALERT_ID, ErrorAlertas, consultar_alertas
from eventos import PATRON_RUN_ID, ErrorEventos, abrir_captura, consultar_eventos

HOST = "127.0.0.1"
PATRON_EVENT_ID = re.compile(r"^[0-9a-f]{32}$")
MAX_EVENTOS_PAGINA = 500

TIPOS = {
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".jpg": "image/jpeg",
}

CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


class ErrorPortal(ValueError):
    """Peticion o artefacto que el portal no puede mostrar de forma auditable."""


def _e(valor: Any) -> str:
    """Escapa todo valor interpolado en HTML, incluidas comillas."""

    if valor is None:
        return "sin dato"
    if isinstance(valor, bool):
        return "si" if valor else "no"
    return escape(str(valor), quote=True)


def _validar_run(run_id: str) -> str:
    if PATRON_RUN_ID.fullmatch(run_id) is None:
        raise ErrorPortal("run_id invalido")
    return run_id


def _validar_evento(event_id: str) -> str:
    if PATRON_EVENT_ID.fullmatch(event_id) is None:
        raise ErrorPortal("event_id invalido")
    return event_id


def _validar_alerta(alert_id: str) -> str:
    if PATRON_ALERT_ID.fullmatch(alert_id) is None:
        raise ErrorPortal("alert_id invalido")
    return alert_id


def listar_runs(root: str | Path) -> list[str]:
    """Une eventos y alertas persistidos; no interpreta su contenido."""

    base = Path(root)
    encontrados: set[str] = set()
    for carpeta in ("eventos", "alertas"):
        directorio = base / carpeta
        if not directorio.is_dir():
            continue
        for ruta in directorio.glob("*.jsonl"):
            if PATRON_RUN_ID.fullmatch(ruta.stem) is not None:
                encontrados.add(ruta.stem)
    return sorted(encontrados, reverse=True)


def _leer_eventos(root: Path, run_id: str) -> list[dict[str, Any]]:
    try:
        return consultar_eventos(root, run_id)
    except FileNotFoundError:
        return []
    except (ErrorEventos, OSError, json.JSONDecodeError, ValueError) as exc:
        raise ErrorPortal(f"eventos no legibles: {exc}") from exc


def _leer_alertas(root: Path, run_id: str) -> list[dict[str, Any]]:
    try:
        return consultar_alertas(root, run_id)
    except FileNotFoundError:
        return []
    except (ErrorAlertas, OSError, json.JSONDecodeError, ValueError) as exc:
        raise ErrorPortal(f"alertas no legibles: {exc}") from exc


def _estilo() -> str:
    return """
    :root { color-scheme: light dark; }
    body { font-family: system-ui, sans-serif; margin: 1.5rem auto; max-width: 1100px;
           padding: 0 1rem; line-height: 1.45; }
    h1, h2 { line-height: 1.2; }
    table { border-collapse: collapse; width: 100%; margin: .6rem 0 1.4rem; }
    th, td { border: 1px solid #8888; padding: .35rem .5rem; text-align: left;
             vertical-align: top; font-size: .92rem; }
    code, pre { font-family: ui-monospace, monospace; font-size: .85rem; }
    pre { overflow: auto; padding: .7rem; border: 1px solid #8888; }
    .alerta { border: 1px solid #8888; border-radius: 6px; padding: .8rem;
              margin: .8rem 0; }
    .meta { color: #666; font-size: .9rem; }
    .aviso { border-left: 4px solid #b8860b; padding: .5rem .8rem; margin: .8rem 0; }
    img { max-width: 100%; height: auto; border: 1px solid #8888; }
    nav a, a { overflow-wrap: anywhere; }
    """


def _plantilla(titulo: str, cuerpo: str, *, metricas: bool) -> str:
    enlace_metricas = (
        '<li><a href="metrics">Metricas derivadas (JSON)</a></li>' if metricas else ""
    )
    return (
        "<!doctype html><html lang=\"es\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_e(titulo)}</title><style>{_estilo()}</style></head><body>"
        f"<h1>{_e(titulo)}</h1>"
        "<nav><ul>"
        '<li><a href="/">Corridas</a></li>'
        f"{enlace_metricas}"
        "</ul></nav>"
        f"<main>{cuerpo}</main>"
        '<footer class="meta"><p>Portal P6-08 de solo lectura. Los datos son '
        "artefactos locales P6-04/P6-05/P6-06; una captura alterada no se "
        "muestra como valida.</p></footer></body></html>"
    )


def _indice(root: Path, *, metricas: bool) -> str:
    runs = listar_runs(root)
    if not runs:
        cuerpo = (
            '<p class="aviso">No hay corridas persistidas en esta raiz. '
            "Genere artefactos P6-05/P6-06 antes de abrir el portal.</p>"
        )
    else:
        filas = []
        for run_id in runs:
            eventos = _leer_eventos(root, run_id)
            alertas = _leer_alertas(root, run_id)
            filas.append(
                "<tr>"
                f'<td><a href="/portal/runs/{_e(run_id)}">{_e(run_id)}</a></td>'
                f"<td>{len(eventos)}</td><td>{len(alertas)}</td>"
                f"<td>{sum(1 for e in eventos if e.get('capture'))}</td>"
                "</tr>"
            )
        cuerpo = (
            "<table><thead><tr><th>Corrida</th><th>Eventos</th><th>Alertas</th>"
            "<th>Capturas</th></tr></thead><tbody>"
            + "".join(filas)
            + "</tbody></table>"
        )
    return _plantilla("Portal P6-08", cuerpo, metricas=metricas)


def _tabla_eventos(root: Path, run_id: str, eventos: list[Mapping[str, Any]]) -> str:
    if not eventos:
        return (
            '<p class="aviso">La corrida no tiene eventos P6-05. '
            "Esto no significa normalidad: revise las alertas y la politica.</p>"
        )
    filas = []
    for evento in eventos[:MAX_EVENTOS_PAGINA]:
        event_id = str(evento["event_id"])
        prediccion = evento.get("prediction") or {}
        clase = prediccion.get("class_name")
        confianza = prediccion.get("confidence")
        texto_prediccion = (
            f"{clase}: {confianza}" if clase is not None else "sin deteccion"
        )
        captura = ""
        if evento.get("capture"):
            captura = (
                f'<a href="/portal/runs/{_e(run_id)}/captures/{_e(event_id)}">'
                "ver JPEG</a>"
            )
        filas.append(
            "<tr>"
            f'<td><a href="/portal/runs/{_e(run_id)}/events/{_e(event_id)}">'
            f"{_e(event_id[:12])}</a></td>"
            f"<td>{_e(evento.get('occurred_at'))}</td>"
            f"<td>{_e(evento.get('status'))}</td>"
            f"<td>{_e(evento.get('zone'))}</td>"
            f"<td>{_e(texto_prediccion)}</td>"
            f"<td>{captura}</td>"
            "</tr>"
        )
    nota = ""
    if len(eventos) > MAX_EVENTOS_PAGINA:
        nota = (
            f'<p class="aviso">Se muestran {MAX_EVENTOS_PAGINA} de '
            f"{len(eventos)} eventos; el artefacto completo sigue disponible "
            "por la API de consulta.</p>"
        )
    return (
        nota
        + "<table><thead><tr><th>Evento</th><th>Instante</th><th>Estado</th>"
        "<th>Zona</th><th>Prediccion</th><th>Captura</th></tr></thead><tbody>"
        + "".join(filas)
        + "</tbody></table>"
    )


def _tarjeta_alerta(run_id: str, alerta: Mapping[str, Any]) -> str:
    event_ids = alerta.get("event_ids") or []
    ventana = alerta.get("window") or {}
    enlaces_captura = []
    for referencia in ventana.get("event_refs") or []:
        if referencia.get("capture"):
            enlaces_captura.append(
                f'<a href="/portal/runs/{_e(run_id)}/captures/'
                f'{_e(referencia.get("event_id"))}">captura '
                f'{_e(str(referencia.get("event_id"))[:12])}</a>'
            )
    return (
        '<article class="alerta">'
        f"<h3>{_e(alerta.get('rule_id'))} · {_e(alerta.get('severity'))} · "
        f"{_e(alerta.get('occurrence_state'))}</h3>"
        f'<p><a href="/portal/runs/{_e(run_id)}/alerts/'
        f'{_e(alerta.get("alert_id"))}">{_e(alerta.get("alert_id"))}</a></p>'
        f"<p><strong>Instante:</strong> {_e(alerta.get('occurred_at'))}<br>"
        f"<strong>Motivo:</strong> {_e(alerta.get('reason'))}<br>"
        f"<strong>Accion sugerida:</strong> "
        f"{_e(alerta.get('suggested_action'))}</p>"
        f'<p class="meta">event_id: {_e(alerta.get("event_id"))} · '
        f"event_ids: {_e(', '.join(str(i) for i in event_ids) or 'ninguno')} · "
        f"ventana: {_e(ventana.get('kind'))} · politica: "
        f"{_e(alerta.get('policy_id'))}</p>"
        f"<p>{' · '.join(enlaces_captura) or 'sin capturas enlazadas'}</p>"
        "</article>"
    )


def _detalle_run(root: Path, run_id: str, *, metricas: bool) -> str:
    eventos = _leer_eventos(root, run_id)
    alertas = _leer_alertas(root, run_id)
    if not eventos and not alertas:
        raise ErrorPortal("la corrida no tiene eventos ni alertas legibles")
    tarjetas = "".join(_tarjeta_alerta(run_id, alerta) for alerta in alertas)
    if not tarjetas:
        tarjetas = '<p class="aviso">La corrida no tiene alertas P6-06 persistidas.</p>'
    cuerpo = (
        f'<p class="meta">run_id: {_e(run_id)} · eventos: {len(eventos)} · '
        f"alertas: {len(alertas)}</p>"
        '<p class="aviso">Una corrida sin eventos o una alerta informativa no '
        "acreditan normalidad. Las zonas y confianzas se muestran tal como se "
        "persistieron.</p>"
        "<h2>Alertas</h2>"
        + tarjetas
        + "<h2>Eventos</h2>"
        + _tabla_eventos(root, run_id, eventos)
    )
    return _plantilla(f"Corrida {run_id}", cuerpo, metricas=metricas)


def _pagina_error(mensaje: str) -> str:
    return _plantilla("Error", f'<p class="aviso">{_e(mensaje)}</p>', metricas=False)


def _detalle_evento(root: Path, run_id: str, event_id: str, *, metricas: bool) -> str:
    eventos = _leer_eventos(root, run_id)
    evento = next((e for e in eventos if e.get("event_id") == event_id), None)
    if evento is None:
        raise ErrorPortal("evento no encontrado en la corrida")
    imagen = ""
    if evento.get("capture"):
        imagen = (
            f'<p><img src="/portal/runs/{_e(run_id)}/captures/{_e(event_id)}" '
            f'alt="Captura verificada del evento {_e(event_id)}"></p>'
        )
    cuerpo = (
        f'<p><a href="/portal/runs/{_e(run_id)}">Volver a la corrida</a></p>'
        + imagen
        + "<h2>Evento persistido</h2><pre>"
        + _e(json.dumps(evento, ensure_ascii=False, indent=2, sort_keys=True))
        + "</pre>"
    )
    return _plantilla(f"Evento {event_id}", cuerpo, metricas=metricas)


def _detalle_alerta(root: Path, run_id: str, alert_id: str, *, metricas: bool) -> str:
    alertas = _leer_alertas(root, run_id)
    alerta = next((a for a in alertas if a.get("alert_id") == alert_id), None)
    if alerta is None:
        raise ErrorPortal("alerta no encontrada en la corrida")
    cuerpo = (
        f'<p><a href="/portal/runs/{_e(run_id)}">Volver a la corrida</a></p>'
        + _tarjeta_alerta(run_id, alerta)
        + "<h2>Alerta persistida</h2><pre>"
        + _e(json.dumps(alerta, ensure_ascii=False, indent=2, sort_keys=True))
        + "</pre>"
    )
    return _plantilla(f"Alerta {alert_id}", cuerpo, metricas=metricas)


def metricas_run(root: str | Path, run_id: str) -> dict[str, Any]:
    """Conteos derivados de artefactos persistidos; no son metricas G1-G7."""

    run_id = _validar_run(run_id)
    base = Path(root)
    eventos = _leer_eventos(base, run_id)
    alertas = _leer_alertas(base, run_id)
    return {
        "schema": "p6-08/metrics/1",
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "event_count": len(eventos),
        "alert_count": len(alertas),
        "capture_count": sum(1 for e in eventos if e.get("capture")),
        "events_without_capture": sum(1 for e in eventos if not e.get("capture")),
        "counts_by_event_status": dict(Counter(e.get("status") for e in eventos)),
        "counts_by_zone": dict(Counter(e.get("zone") for e in eventos)),
        "counts_by_alert_severity": dict(
            Counter(a.get("severity") for a in alertas)
        ),
        "counts_by_rule": dict(Counter(a.get("rule_id") for a in alertas)),
        "counts_by_occurrence_state": dict(
            Counter(a.get("occurrence_state") for a in alertas)
        ),
        "limitations": [
            "Conteos operativos derivados de P6-05/P6-06; no son metricas G1-G7.",
            "No miden precision, recall ni calidad predictiva del detector.",
            "0 eventos no significa normalidad; revise la alerta informativa.",
        ],
    }


def _captura(root: Path, run_id: str, event_id: str) -> bytes:
    eventos = _leer_eventos(root, run_id)
    evento = next((e for e in eventos if e.get("event_id") == event_id), None)
    if evento is None:
        raise ErrorPortal("evento no encontrado en la corrida")
    try:
        return abrir_captura(root, evento)
    except ErrorEventos as exc:
        raise ErrorPortal(f"captura no verificable: {exc}") from exc



class ManejadorPortal(BaseHTTPRequestHandler):
    """Atiende solo GET/HEAD; cada ruta usa identificadores con patron estricto."""

    server_version = "P6Portal/1"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, formato: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), formato % args))

    def _cabeceras(self, estado: HTTPStatus, tipo: str, largo: int) -> None:
        self.send_response(estado)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(largo))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _responder(self, estado: HTTPStatus, cuerpo: bytes, tipo: str) -> None:
        self._cabeceras(estado, tipo, len(cuerpo))
        if self.command != "HEAD":
            self.wfile.write(cuerpo)

    def _html(self, estado: HTTPStatus, texto: str) -> None:
        self._responder(estado, texto.encode("utf-8"), TIPOS[".html"])

    def _json(self, estado: HTTPStatus, valor: Any) -> None:
        cuerpo = json.dumps(valor, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self._responder(estado, cuerpo, TIPOS[".json"])

    def _error(self, estado: HTTPStatus, mensaje: str) -> None:
        self._html(estado, _pagina_error(mensaje))

    @property
    def _root(self) -> Path:
        return self.server.root  # type: ignore[attr-defined]

    @property
    def _metricas(self) -> bool:
        return bool(self.server.metricas)  # type: ignore[attr-defined]

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "Solo se admite GET o HEAD.")

    do_PUT = do_POST
    do_DELETE = do_POST
    do_PATCH = do_POST

    def do_GET(self) -> None:  # noqa: N802
        ruta = urlsplit(self.path).path
        # No se decodifica ningun segmento: un '%' en la ruta se rechaza para
        # que variantes codificadas de traversal no lleguen al sistema de archivos.
        if "%" in ruta or "\\" in ruta or "\x00" in ruta:
            self._error(HTTPStatus.BAD_REQUEST, "Ruta no admitida.")
            return
        segmentos = [s for s in ruta.split("/") if s]
        try:
            self._despachar(segmentos)
        except ErrorPortal as exc:
            self._error(HTTPStatus.NOT_FOUND, str(exc))
        except (ErrorEventos, ErrorAlertas, OSError, ValueError, KeyError) as exc:
            self._error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"No fue posible leer los artefactos: {exc}",
            )

    def _despachar(self, segmentos: list[str]) -> None:
        if not segmentos or segmentos == ["portal"]:
            self._html(HTTPStatus.OK, _indice(self._root, metricas=self._metricas))
            return
        if segmentos == ["healthz"]:
            self._json(HTTPStatus.OK, {"ok": True, "read_only": True})
            return
        if len(segmentos) < 3 or segmentos[:2] != ["portal", "runs"]:
            self._error(HTTPStatus.NOT_FOUND, "Ruta no encontrada.")
            return
        run_id = _validar_run(segmentos[2])
        resto = segmentos[3:]
        if not resto:
            self._html(
                HTTPStatus.OK,
                _detalle_run(self._root, run_id, metricas=self._metricas),
            )
            return
        if resto == ["metrics"]:
            if not self._metricas:
                self._error(HTTPStatus.NOT_FOUND, "Metricas opcionales deshabilitadas.")
                return
            self._json(HTTPStatus.OK, metricas_run(self._root, run_id))
            return
        if len(resto) != 2:
            self._error(HTTPStatus.NOT_FOUND, "Ruta no encontrada.")
            return
        recurso, identificador = resto
        if recurso == "events":
            self._html(
                HTTPStatus.OK,
                _detalle_evento(
                    self._root, run_id, _validar_evento(identificador),
                    metricas=self._metricas,
                ),
            )
        elif recurso == "alerts":
            self._html(
                HTTPStatus.OK,
                _detalle_alerta(
                    self._root, run_id, _validar_alerta(identificador),
                    metricas=self._metricas,
                ),
            )
        elif recurso == "captures":
            datos = _captura(self._root, run_id, _validar_evento(identificador))
            self._responder(HTTPStatus.OK, datos, TIPOS[".jpg"])
        else:
            self._error(HTTPStatus.NOT_FOUND, "Ruta no encontrada.")



def crear_servidor(
    root: str | Path,
    puerto: int = 8080,
    *,
    metricas: bool = False,
) -> ThreadingHTTPServer:
    """Crea el servidor ligado exclusivamente a 127.0.0.1."""

    if not isinstance(puerto, int) or isinstance(puerto, bool) or not 0 <= puerto <= 65535:
        raise ValueError("puerto invalido")
    raiz = Path(root).resolve()
    if not raiz.is_dir():
        raise FileNotFoundError(f"la raiz no existe o no es un directorio: {raiz}")
    servidor = ThreadingHTTPServer((HOST, puerto), ManejadorPortal)
    servidor.daemon_threads = True
    servidor.root = raiz  # type: ignore[attr-defined]
    servidor.metricas = metricas  # type: ignore[attr-defined]
    return servidor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("datos_locales"))
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--metrics",
        action="store_true",
        help="habilita conteos derivados opcionales en /metrics",
    )
    args = parser.parse_args(argv)
    try:
        servidor = crear_servidor(args.root, args.port, metricas=args.metrics)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    host, puerto = servidor.server_address[:2]
    print(
        f"Portal P6-08 en http://{host}:{puerto}/ (solo lectura; Ctrl+C para salir)",
        flush=True,
    )
    if args.metrics:
        print("Metricas derivadas habilitadas en /portal/runs/<run_id>/metrics", flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


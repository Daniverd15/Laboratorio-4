#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Laboratorio 4 - UNAB-Ambiental
Cliente AMQP 1.0 EXPLICITO hacia Azure IoT Hub / IoT Central (Apache Qpid Proton).

Nota: el SDK 'azure-iot-device' de Python SOLO habla MQTT (y MQTT sobre
WebSockets); su parametro de transporte AMQP existe en los SDK de C#/Java/Node,
no en Python. Por eso aqui se usa un cliente AMQP 1.0 generico y se construye a
mano lo que el SDK ocultaba:

  1) TLS 5671 (verificado)  +  SASL ANONYMOUS      -> conexion AMQP
  2) CBS (Claims-Based Security): se envia un token SAS al nodo '$cbs'
     (operation=put-token, type=servicebus.windows.net:sastoken) y se espera
     status-code 200.   <- el SAS viaja DENTRO del protocolo, no en el CONNECT.
  3) Enlace (link) de telemetria  ->  /devices/{deviceId}/messages/events
  4) Cada lectura = 1 transfer con las 3 variables en JSON; el Hub contesta con
     una disposicion 'accepted' (ese es el ack). Hay control de flujo por
     credito: solo se envia si el Hub concedio credito al enlace.
  5) El SAS se RENUEVA en la misma conexion (put-token otra vez) antes de
     caducar -> sin reconectar (en MQTT hay que reconectar con otro token).

Modo alternativo IOTC_AMQP_AUTH=plain: SASL PLAIN con usuario
'{deviceId}@sas.{hubName}' y el SAS como clave (sin CBS).

Variables de entorno (ver .env.example):
  IOTC_ID_SCOPE, IOTC_DEVICE_ID, IOTC_DEVICE_KEY   credenciales (NO van en el repo)
  IOTC_HUB            (opcional) hostname del hub; si existe se salta DPS
  IOTC_SEND_INTERVAL  segundos entre lecturas (defecto 5)
  IOTC_N              numero de mensajes; 0 = infinito
  IOTC_SAS_TTL        vigencia del SAS en s (defecto 3600); se renueva al 80 %
  IOTC_AMQP_AUTH      cbs (defecto) | plain
  IOTC_BACKOFF_MAX    tope del backoff de reconexion en s (defecto 16)
  IOTC_CSV            ruta CSV de mediciones (opcional)
  PN_TRACE_FRM=1      (variable de Proton) imprime cada trama AMQP: open, begin,
                      attach, flow, transfer, disposition ...
"""
import os, sys, time, uuid
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))
from iotc_common import (log, cfg, generar_sas, obtener_hub, Productor, Medidor, BACKOFF_MAX)

from proton import Message, SSLDomain
from proton.handlers import MessagingHandler
from proton.reactor import Container

ID_SCOPE = cfg("IOTC_ID_SCOPE", requerida=True)
DEVICE_ID = cfg("IOTC_DEVICE_ID", requerida=True)
DEVICE_KEY = cfg("IOTC_DEVICE_KEY", requerida=True)
INTERVALO = float(cfg("IOTC_SEND_INTERVAL", "5"))
N_MSGS = int(cfg("IOTC_N", "0"))
SAS_TTL = int(cfg("IOTC_SAS_TTL", "3600"))
AUTH = cfg("IOTC_AMQP_AUTH", "cbs").lower()
CSV_PATH = cfg("IOTC_CSV", "")
PUERTO = 5671


def dominio_tls():
    """TLS verificado: cadena + nombre del servidor (como en MQTT/HTTPS)."""
    d = SSLDomain(SSLDomain.MODE_CLIENT)
    ca = os.environ.get("IOTC_CA_BUNDLE")
    if not ca and os.path.exists("/etc/ssl/certs/ca-certificates.crt"):
        ca = "/etc/ssl/certs/ca-certificates.crt"
    if ca:
        d.set_trusted_ca_db(ca)
    d.set_peer_authentication(SSLDomain.VERIFY_PEER_NAME, ca)
    return d


class ClienteAMQP(MessagingHandler):
    """Una instancia = una conexion AMQP. Si la conexion cae, main() crea otra
    y la cola del Productor conserva lo no confirmado (al menos una vez)."""

    def __init__(self, hub, prod, med):
        super().__init__(prefetch=10, auto_accept=True, auto_settle=True)
        self.hub, self.prod, self.med = hub, prod, med
        self.recurso = "%s/devices/%s" % (hub, DEVICE_ID)
        self.destino = "/devices/%s/messages/events" % DEVICE_ID
        self.t0 = self.t_open = self.t_put = None
        self.conn = self.cbs_tx = self.cbs_rx = self.tx = None
        self.en_vuelo = {}               # tag -> (n, t_envio, bytes, t_gen)
        self.n_en_vuelo = set()
        self.tarea = None
        self.cerrando = False
        self.perdida = False
        self.refrescar_en = 0
        self.token_pendiente = False
        self.cbs_ok = False

    @staticmethod
    def _es(link, ref):
        # Proton entrega un objeto Python nuevo por evento: se compara por nombre de enlace
        return link is not None and ref is not None and link.name == ref.name

    # ------------------------------ conexion ------------------------------
    def on_start(self, event):
        self.t0 = time.time()
        extra = {}
        if AUTH == "plain":
            token, exp = generar_sas(self.recurso, DEVICE_KEY, SAS_TTL)
            extra = dict(user="%s@sas.%s" % (DEVICE_ID, self.hub.split(".")[0]),
                         password=token, allowed_mechs="PLAIN")
            self.refrescar_en = exp - 0.2 * SAS_TTL
            log("[SAS] token generado a mano (SASL PLAIN, sr=%s, ttl=%ds)" % (self.recurso, SAS_TTL))
        else:
            extra = dict(allowed_mechs="ANONYMOUS")
        log("[AMQP] Conectando a amqps://%s:%d  (TLS verificado, SASL %s)"
            % (self.hub, PUERTO, "PLAIN" if AUTH == "plain" else "ANONYMOUS + CBS"))
        self.conn = event.container.connect(
            "amqps://%s:%d" % (self.hub, PUERTO), ssl_domain=dominio_tls(),
            sasl_enabled=True, reconnect=False, heartbeat=60, **extra)

    def on_connection_opened(self, event):
        self.t_open = time.time()
        ms = (self.t_open - self.t0) * 1000
        self.med.hito("tcp+tls+sasl+open", ms)
        log("[AMQP] OPEN del Hub recibido: conexion lista en %.0f ms (TCP+TLS+SASL+Open)" % ms)
        if AUTH == "plain":
            self.abrir_telemetria(event)
        else:
            # Dos enlaces hacia el nodo de seguridad $cbs: uno para pedir, otro para la respuesta
            self.cbs_tx = event.container.create_sender(self.conn, "$cbs", name="cbs-tx-" + uuid.uuid4().hex[:6])
            self.cbs_rx = event.container.create_receiver(self.conn, "$cbs", target="cbs",
                                                           name="cbs-rx-" + uuid.uuid4().hex[:6])
            log("[CBS] Enlaces abiertos hacia el nodo '$cbs' (sender + receiver)")
        self.tarea = event.container.schedule(0.05, self)

    # --------------------------------- CBS ---------------------------------
    def enviar_token(self):
        token, exp = generar_sas(self.recurso, DEVICE_KEY, SAS_TTL)
        self.refrescar_en = exp - 0.2 * SAS_TTL
        msg = Message(
            id=uuid.uuid4().hex, reply_to="cbs", body=token,
            properties={"operation": "put-token",
                        "type": "servicebus.windows.net:sastoken",
                        "name": self.recurso})
        self.t_put = time.time()
        self.cbs_tx.send(msg)
        self.token_pendiente = False
        log("[SAS] token generado a mano (sr=%s, ttl=%ds) -> put-token enviado al nodo $cbs"
            % (self.recurso, SAS_TTL))

    def on_message(self, event):
        if not self._es(event.receiver, self.cbs_rx):
            return
        p = event.message.properties or {}
        code, desc = int(p.get("status-code") or 0), p.get("status-description")
        ms = (time.time() - self.t_put) * 1000
        log("[CBS] respuesta put-token: status-code=%s (%s) en %.0f ms" % (code, desc, ms))
        if code != 200:
            self.cerrar(event, "CBS rechazo el token")
            return
        if not self.cbs_ok:
            self.cbs_ok = True
            self.med.hito("cbs_put_token", ms)
            self.med.hito("listo_para_enviar", (time.time() - self.t0) * 1000)
            self.abrir_telemetria(event)
        else:
            log("[CBS] SAS renovado SIN reconectar (la conexion AMQP sigue viva)")

    # ------------------------------ telemetria ------------------------------
    def abrir_telemetria(self, event):
        self.tx = event.container.create_sender(self.conn, self.destino,
                                                name="tx-" + uuid.uuid4().hex[:6])
        log("[AMQP] ATTACH enlace de telemetria -> %s" % self.destino)

    def on_link_opened(self, event):
        if self._es(event.link, self.tx):
            log("[AMQP] enlace de telemetria abierto (credito inicial=%s)" % self.tx.credit)
            if AUTH == "plain":
                self.med.hito("listo_para_enviar", (time.time() - self.t0) * 1000)

    def on_sendable(self, event):
        if self._es(event.sender, self.cbs_tx) and not self.cbs_ok and self.t_put is None:
            self.enviar_token()
        elif self._es(event.sender, self.tx):
            self.drenar()

    def drenar(self):
        """Envia lo pendiente SOLO mientras el Hub conceda credito."""
        if not self.tx or not self.tx.credit:
            return
        for (n, payload, t_gen) in self.prod.pendientes():
            if self.tx.credit <= 0:
                break
            if n in self.n_en_vuelo:
                continue
            msg = Message(id="%s-%d" % (DEVICE_ID, n), body=payload,
                          content_type="application/json", content_encoding="utf-8")
            msg.inferred = True          # bytes -> seccion AMQP 'data' (no 'amqp-value')
            d = self.tx.send(msg)
            self.en_vuelo[d.tag] = (n, time.time(), len(payload), t_gen)
            self.n_en_vuelo.add(n)
            log("[TX #%d] transfer bytes=%d credito_restante=%s payload=%s"
                % (n, len(payload), self.tx.credit, payload.decode()))

    def on_accepted(self, event):
        info = self.en_vuelo.pop(event.delivery.tag, None)
        if not info:
            return
        n, t_env, nb, t_gen = info
        ack = (time.time() - t_env) * 1000.0
        e2e = (time.time() - t_gen) * 1000.0
        self.n_en_vuelo.discard(n)
        self.med.mensaje(n, nb, ack, e2e)
        self.prod.quitar(n)
        log("[ACK #%d] disposicion 'accepted' del Hub en %.0f ms%s"
            % (n, ack, ("  (espera total %.0f ms)" % e2e) if e2e - ack > 500 else ""))
        if self.prod.terminado():
            self.cerrar(event, "completados %d mensajes" % len(self.med.filas))

    def _reencolar(self, event, que):
        info = self.en_vuelo.pop(event.delivery.tag, None)
        if info:
            self.n_en_vuelo.discard(info[0])
            log("[AMQP] mensaje #%d %s por el Hub -> se reenviara" % (info[0], que))

    def on_rejected(self, event):
        self._reencolar(event, "RECHAZADO")

    def on_released(self, event):
        self._reencolar(event, "LIBERADO")

    # -------------------------- temporizador / cierre -------------------------
    def on_timer_task(self, event):
        self.tarea = None
        if self.cerrando or self.perdida:
            return
        self.drenar()
        if (self.cbs_ok and AUTH != "plain" and time.time() > self.refrescar_en
                and self.cbs_tx.credit):
            log("[CBS] el SAS esta por caducar -> put-token de renovacion en la misma conexion")
            self.enviar_token()
        self.tarea = event.container.schedule(0.05, self)

    def cerrar(self, event, motivo):
        if self.cerrando:
            return
        self.cerrando = True
        log("[AMQP] cerrando conexion: %s" % motivo)
        if self.tarea:
            self.tarea.cancel()
        for l in (self.tx, self.cbs_tx, self.cbs_rx):
            if l:
                l.close()
        self.conn.close()

    def _caida(self, event, que):
        if self.perdida:
            return
        self.perdida = True
        if self.tarea:
            self.tarea.cancel()
        log("[AMQP] %s" % que)

    def on_transport_error(self, event):
        c = event.transport.condition
        self._caida(event, "ERROR de transporte: %s" % (c.description if c else "desconocido"))

    def on_disconnected(self, event):
        self._caida(event, "conexion perdida (socket cerrado)")

    def on_connection_error(self, event):
        c = event.connection.remote_condition
        self._caida(event, "ERROR de conexion: %s" % (c.description if c else ""))
        self.cerrar(event, "error de conexion")

    def on_session_error(self, event):
        c = event.session.remote_condition
        log("[AMQP] ERROR de sesion: %s" % (c.description if c else ""))

    def on_link_error(self, event):
        c = event.link.remote_condition
        log("[AMQP] ERROR de enlace %s: %s %s" % (event.link.name, c.name if c else "", c.description if c else ""))
        self.cerrar(event, "error de enlace")

    def on_connection_closed(self, event):
        if self.tarea:
            self.tarea.cancel()


def main():
    hub = obtener_hub(ID_SCOPE, DEVICE_ID, DEVICE_KEY)
    med = Medidor("amqp-plain" if AUTH == "plain" else "amqp")
    prod = Productor(INTERVALO, N_MSGS)
    prod.start()
    espera = 1
    try:
        while True:
            h = ClienteAMQP(hub, prod, med)
            Container(h).run()
            if prod.terminado():
                break
            log("[AMQP] reintentando en %ds (quedan %d lecturas sin confirmar en cola)"
                % (espera, len(prod.pendientes())))
            time.sleep(espera)
            espera = min(espera * 2, BACKOFF_MAX)
    except KeyboardInterrupt:
        log("[FIN] Interrumpido por el usuario.")
    finally:
        med.resumen()
        med.csv(CSV_PATH)


if __name__ == "__main__":
    main()

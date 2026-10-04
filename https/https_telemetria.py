#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Laboratorio 4 - UNAB-Ambiental
TERCER PROTOCOLO: HTTPS (REST) hacia Azure IoT Hub / IoT Central.

Publica las mismas 3 variables (temperature, humedad, illuminance) con una
peticion HTTPS por lectura:

    POST https://{hub}/devices/{deviceId}/messages/events?api-version=2021-04-12
    Authorization: SharedAccessSignature sr=...&sig=...&se=...
    Content-Type : application/json   +   Content-Encoding: utf-8   (Central lo exige)

Sin SDK ni broker: stdlib de Python (http.client + ssl).  El resultado es un
204 No Content por cada mensaje aceptado.

Modos (IOTC_HTTP_KEEPALIVE):
  1 (defecto)  una conexion TCP/TLS reutilizada (HTTP keep-alive)
  0            conexion nueva por mensaje (tipico de un sensor que despierta,
               envia y se vuelve a dormir)

Variables de entorno (ver .env.example):
  IOTC_ID_SCOPE, IOTC_DEVICE_ID, IOTC_DEVICE_KEY   credenciales (NO van en el repo)
  IOTC_HUB            (opcional) hostname del hub; si existe se salta DPS
  IOTC_SEND_INTERVAL  segundos entre lecturas (defecto 5)
  IOTC_N              numero de mensajes; 0 = infinito
  IOTC_HTTP_KEEPALIVE 1/0
  IOTC_BACKOFF_MAX    tope del backoff de reintentos en s (defecto 16)
  IOTC_CSV            ruta CSV de mediciones (opcional)
"""
import os, sys, time, http.client, ssl
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))
from iotc_common import (log, cfg, generar_sas, tls_context, obtener_hub,
                         Productor, Medidor, HUB_API, BACKOFF_MAX)

ID_SCOPE = cfg("IOTC_ID_SCOPE", requerida=True)
DEVICE_ID = cfg("IOTC_DEVICE_ID", requerida=True)
DEVICE_KEY = cfg("IOTC_DEVICE_KEY", requerida=True)
INTERVALO = float(cfg("IOTC_SEND_INTERVAL", "5"))
N_MSGS = int(cfg("IOTC_N", "0"))
KEEPALIVE = cfg("IOTC_HTTP_KEEPALIVE", "1") == "1"
CSV_PATH = cfg("IOTC_CSV", "")
SAS_TTL = int(cfg("IOTC_SAS_TTL", "3600"))


def main():
    hub = obtener_hub(ID_SCOPE, DEVICE_ID, DEVICE_KEY)
    recurso = "%s/devices/%s" % (hub, DEVICE_ID)
    ruta = "/devices/%s/messages/events?api-version=%s" % (DEVICE_ID, HUB_API)
    token, expira = generar_sas(recurso, DEVICE_KEY, SAS_TTL)
    log("[SAS] token generado a mano sr=%s ttl=%ds" % (recurso, SAS_TTL))
    log("[HTTPS] POST https://%s%s  keep-alive=%s" % (hub, ruta, KEEPALIVE))

    med = Medidor("https-ka" if KEEPALIVE else "https-nokeepalive")
    ctx = tls_context()
    conn = None
    prod = Productor(INTERVALO, N_MSGS)
    prod.start()
    espera_backoff = 1
    hechos = 0

    def conectar():
        nonlocal conn
        c = http.client.HTTPSConnection(hub, 443, context=ctx, timeout=20)
        t0 = time.time()
        c.connect()                                   # TCP + TLS (verificado)
        ms = (time.time() - t0) * 1000
        log("[HTTPS] TCP+TLS establecido en %.0f ms (%s, %s)"
            % (ms, c.sock.version(), c.sock.cipher()[0]))
        conn = c
        return ms

    try:
        while True:
            pend = prod.pendientes()
            if not pend:
                if prod.terminado():
                    break
                time.sleep(0.05)
                continue
            n, payload, t_gen = pend[0]
            if time.time() > expira - 60:             # SAS por caducar: renovar
                token, expira = generar_sas(recurso, DEVICE_KEY, SAS_TTL)
                log("[SAS] token renovado (en HTTPS no hay conexion que reautenticar)")
            try:
                t_tls = 0.0
                if conn is None:
                    t_tls = conectar()
                    if "tcp_tls" not in med.tiempos:
                        med.hito("tcp_tls", t_tls)
                headers = {"Authorization": token, "Content-Type": "application/json",
                           "Content-Encoding": "utf-8"}   # sin esto Central no parsea el JSON
                if not KEEPALIVE:
                    headers["Connection"] = "close"
                t0 = time.time()
                conn.request("POST", ruta, payload, headers)
                r = conn.getresponse()
                cuerpo = r.read()
                ack = (time.time() - t0) * 1000.0
                if not KEEPALIVE:
                    conn.close(); conn = None
                if r.status in (200, 204):
                    e2e = (time.time() - t_gen) * 1000.0
                    med.mensaje(n, len(payload), ack + t_tls, e2e)
                    log("[TX #%d] POST bytes=%d -> HTTP %d en %.0f ms%s  payload=%s"
                        % (n, len(payload), r.status, ack,
                           (" (+%.0f ms TLS)" % t_tls) if t_tls else "", payload.decode()))
                    prod.quitar(n); hechos += 1; espera_backoff = 1
                else:
                    log("[ERROR] HTTP %d %s -> %s" % (r.status, r.reason, cuerpo[:200]))
                    if r.status == 401:               # token invalido/caducado
                        token, expira = generar_sas(recurso, DEVICE_KEY, SAS_TTL)
                    conn = None
                    time.sleep(espera_backoff); espera_backoff = min(espera_backoff * 2, BACKOFF_MAX)
            except (OSError, http.client.HTTPException, ssl.SSLError) as e:
                log("[ERROR] %s: %s -> reintento en %ds (mensaje #%d queda en cola)"
                    % (type(e).__name__, e, espera_backoff, n))
                try:
                    if conn: conn.close()
                except Exception:
                    pass
                conn = None
                time.sleep(espera_backoff); espera_backoff = min(espera_backoff * 2, BACKOFF_MAX)
    except KeyboardInterrupt:
        log("[FIN] Interrumpido por el usuario.")
    finally:
        if conn:
            conn.close()
        med.resumen()
        med.csv(CSV_PATH)


if __name__ == "__main__":
    main()

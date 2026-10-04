#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Laboratorio 4 - UNAB-Ambiental
LINEA BASE: cliente MQTT explicito (paho) del Lab 3, adaptado para medir igual
que los clientes AMQP y HTTPS.

Cambios respecto al Lab 3:
  * Mismo aprovisionamiento DPS por HTTPS que los otros dos clientes.
  * Mismo Medidor (latencia envio->ack, hitos de conexion) y mismo CSV.
  * IOTC_MQTT_TRANSPORT=websockets  ->  MQTT sobre WebSockets, puerto 443
    (ruta /$iothub/websocket): el mismo modelo MQTT, pero por el puerto de HTTPS.

Variables de entorno (ver .env.example):
  IOTC_ID_SCOPE, IOTC_DEVICE_ID, IOTC_DEVICE_KEY   credenciales (NO van en el repo)
  IOTC_HUB             (opcional) hostname del hub; si existe se salta DPS
  IOTC_SEND_INTERVAL   segundos entre lecturas (defecto 5)
  IOTC_N               numero de mensajes; 0 = infinito
  IOTC_QOS             0 | 1 (defecto 1).  IoT Hub no implementa QoS 2 (Lab 3).
  IOTC_MQTT_TRANSPORT  tcp (8883, defecto) | websockets (443)
  IOTC_SAS_TTL         vigencia del SAS en segundos (defecto 3600)
  IOTC_BACKOFF_MAX     tope del backoff de reconexion en s (defecto 16)
  IOTC_CSV             ruta CSV de mediciones (opcional)
  IOTC_DEBUG=1         traza de paquetes MQTT crudos (paho on_log)
"""
import os, sys, time, json, threading
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))
from iotc_common import (log, cfg, generar_sas, tls_context, obtener_hub,
                         payload_json, Medidor, HUB_API, BACKOFF_MAX)

import paho.mqtt.client as mqtt

ID_SCOPE = cfg("IOTC_ID_SCOPE", requerida=True)
DEVICE_ID = cfg("IOTC_DEVICE_ID", requerida=True)
DEVICE_KEY = cfg("IOTC_DEVICE_KEY", requerida=True)
INTERVALO = float(cfg("IOTC_SEND_INTERVAL", "5"))
N_MSGS = int(cfg("IOTC_N", "0"))
QOS = int(cfg("IOTC_QOS", "1"))
TRANSPORTE = cfg("IOTC_MQTT_TRANSPORT", "tcp").lower()
SAS_TTL = int(cfg("IOTC_SAS_TTL", "3600"))
CSV_PATH = cfg("IOTC_CSV", "")


def nuevo_cliente(client_id, transporte):
    """Cliente paho compatible con paho-mqtt v1.x y v2.x."""
    try:  # paho-mqtt >= 2.0
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id,
                           protocol=mqtt.MQTTv311, transport=transporte)
    except (AttributeError, TypeError):  # paho-mqtt 1.x
        return mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311, transport=transporte)


def main():
    hub = obtener_hub(ID_SCOPE, DEVICE_ID, DEVICE_KEY)
    recurso = "%s/devices/%s" % (hub, DEVICE_ID)
    sas, exp = generar_sas(recurso, DEVICE_KEY, SAS_TTL)
    usuario = "%s/%s/?api-version=%s" % (hub, DEVICE_ID, HUB_API)
    topic_tx = "devices/%s/messages/events/" % DEVICE_ID
    topic_met = "$iothub/methods/POST/#"
    ws = TRANSPORTE.startswith("web")
    puerto = 443 if ws else 8883
    nombre = "mqtt-ws" if ws else "mqtt"

    log("[SAS] token generado a mano sr=%s ttl=%ds" % (recurso, SAS_TTL))
    log("[MQTT] Conectando a %s:%d (%s, TLS verificado, QoS=%d)" % (hub, puerto,
        "WebSockets /$iothub/websocket" if ws else "MQTT sobre TCP", QOS))

    med = Medidor("%s-qos%d" % (nombre, QOS))
    pend = {}                         # mid -> (n, t_envio, bytes)
    estado = {"conn": False, "t_conn": None, "primer_connack": None}
    t0 = time.time()

    c = nuevo_cliente(DEVICE_ID, "websockets" if ws else "tcp")
    c.username_pw_set(username=usuario, password=sas)
    c.tls_set_context(tls_context())
    if ws:
        c.ws_set_options(path="/$iothub/websocket")
    c.reconnect_delay_set(min_delay=1, max_delay=BACKOFF_MAX)

    def on_connect(cl, u, flags, rc):
        estado["conn"] = (rc == 0)
        if rc == 0 and estado["primer_connack"] is None:
            estado["primer_connack"] = (time.time() - t0) * 1000
            med.hito("tcp+tls+connect(connack)", estado["primer_connack"])
        log("[MQTT] CONNACK rc=%s (%s)" % (rc, "conectado" if rc == 0 else "RECHAZADO"))
        if rc == 0:
            cl.subscribe(topic_met, qos=0)
            log("[MQTT] Suscrito a metodos directos: %s" % topic_met)

    def on_disconnect(cl, u, rc):
        estado["conn"] = False
        log("[MQTT] DESCONECTADO (rc=%s). paho reintenta con backoff..." % rc)
        # En MQTT el SAS va en el CONNECT: para reconectar hay que presentar uno vigente.
        nuevo, _ = generar_sas(recurso, DEVICE_KEY, SAS_TTL)
        cl.username_pw_set(username=usuario, password=nuevo)
        log("[SAS] token nuevo generado para la reconexion (MQTT no puede renovarlo en caliente)")

    def on_publish(cl, u, mid):
        info = pend.pop(mid, None)
        if not info:
            return
        n, t_env, nb = info
        lat = (time.time() - t_env) * 1000.0
        if QOS == 0:
            med.mensaje(n, nb, None, None)
            log("[SENT #%d] enviado a la red (qos=0, sin PUBACK)" % n)
        else:
            med.mensaje(n, nb, lat, lat)
            log("[ACK #%d] PUBACK en %.0f ms (qos=%d)" % (n, lat, QOS))

    def on_message(cl, u, msg):
        try:
            rid = msg.topic.split("$rid=")[1]
            metodo = msg.topic.split("/POST/")[1].split("/")[0]
        except Exception:
            return
        payload = msg.payload.decode("utf-8", "replace")
        log("[C2D] Metodo directo '%s' rid=%s payload=%s" % (metodo, rid, payload))
        cl.publish("$iothub/methods/res/200/?$rid=%s" % rid,
                   json.dumps({"result": "LED ON"}), qos=0)

    c.on_connect, c.on_disconnect = on_connect, on_disconnect
    c.on_publish, c.on_message = on_publish, on_message
    if os.environ.get("IOTC_DEBUG"):
        c.on_log = lambda cl, u, level, buf: log("[MQTT-RAW] %s" % buf)
    c.connect(hub, puerto, keepalive=120)
    c.loop_start()
    for _ in range(100):
        if estado["conn"]:
            break
        time.sleep(0.1)

    n = 0
    try:
        while not N_MSGS or n < N_MSGS:
            n += 1
            payload = payload_json()
            info = c.publish(topic_tx, payload, qos=QOS)
            pend[info.mid] = (n, time.time(), len(payload))
            log("[TX #%d] PUBLISH topic=%s qos=%d bytes=%d payload=%s"
                % (n, topic_tx, QOS, len(payload), payload.decode()))
            if N_MSGS and n >= N_MSGS:
                # esperar los ultimos ACK (hasta 60 s: hay que dar margen a un corte de red)
                for _ in range(600):
                    if not pend:
                        break
                    time.sleep(0.1)
                break
            time.sleep(INTERVALO)
    except KeyboardInterrupt:
        log("[FIN] Interrumpido por el usuario.")
    finally:
        c.loop_stop(); c.disconnect()
        med.resumen()
        med.csv(CSV_PATH)


if __name__ == "__main__":
    main()

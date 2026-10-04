#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diagrama de flujo del Lab 4: tres caminos (MQTT, AMQP, HTTPS) -> IoT Hub -> IoT Central.
Uso: python medicion/diagrama.py   (requiere Pillow)  ->  evidencias/00-arquitectura-flujo.png"""
import os
from PIL import Image, ImageDraw, ImageFont

RAIZ = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
OUT = os.path.join(RAIZ, "evidencias", "00-arquitectura-flujo.png")
F = ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/calibri.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
FB = ["C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/calibrib.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
FM = ["C:/Windows/Fonts/consola.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"]


def fnt(lista, n):
    for f in lista:
        if os.path.exists(f):
            return ImageFont.truetype(f, n)
    return ImageFont.load_default()


W, H = 1500, 800
img = Image.new("RGB", (W, H), (255, 255, 255))
d = ImageDraw.Draw(img)
t_tit, t_b, t_s, t_m = fnt(FB, 24), fnt(FB, 17), fnt(F, 15), fnt(FM, 14)

AZUL, VERDE, NARANJA, GRIS, ROJO = (31, 111, 235), (30, 150, 90), (222, 120, 20), (90, 98, 110), (200, 60, 60)


def caja(x, y, w, h, titulo, lineas, color, relleno=(247, 249, 252)):
    d.rounded_rectangle([x, y, x + w, y + h], 10, fill=relleno, outline=color, width=3)
    d.text((x + 12, y + 8), titulo, font=t_b, fill=color)
    for i, l in enumerate(lineas):
        d.text((x + 12, y + 38 + i * 21), l, font=t_s, fill=(40, 44, 52))


def flecha(x1, y1, x2, y2, texto, color, dy=-22):
    d.line([x1, y1, x2, y2], fill=color, width=4)
    d.polygon([(x2, y2), (x2 - 14, y2 - 8), (x2 - 14, y2 + 8)], fill=color)
    d.text(((x1 + x2) // 2 - d.textlength(texto, font=t_m) / 2, y1 + dy), texto, font=t_m, fill=color)


d.text((20, 12), "Laboratorio 4 - Mismo dispositivo, tres protocolos hacia Azure IoT Central", font=t_tit, fill=(25, 30, 40))
d.text((20, 46), "Las 3 variables (temperature, humedad, illuminance) = 53 bytes JSON.  El primer salto es siempre DPS por HTTPS.", font=t_s, fill=GRIS)

# DPS (columna central superior)
caja(20, 85, 330, 100, "VM Azure (Ubuntu) / ESP32 Wokwi", ["Python 3.10: paho / Proton / http.client", "SAS firmado a mano (HMAC-SHA256)", "Credenciales por variables de entorno"], GRIS)
caja(560, 85, 380, 100, "DPS (aprovisionamiento)", ["global.azure-devices-provisioning.net:443", "PUT .../register  ->  GET .../operations", "devuelve  assignedHub = iotc-xxxx.azure-devices.net"], AZUL)
flecha(350, 135, 560, 135, "HTTPS 443 + SAS", AZUL)

# carriles
carriles = [
    ("MQTT 3.1.1", "8883 TLS", AZUL, 250,
     ["CONNECT user={hub}/{id}/?api-version", "         pass=SAS   (el SAS va en el CONNECT)",
      "PUBLISH devices/{id}/messages/events/  QoS1", "<- PUBACK   (QoS 2: el Hub lo degrada a 1)"]),
    ("AMQP 1.0", "5671 TLS", VERDE, 430,
     ["SASL ANONYMOUS -> open -> begin", "$cbs: put-token(SAS)  ->  status 200", "attach /devices/{id}/messages/events (credito 50)",
      "transfer  ->  disposition 'accepted'"]),
    ("HTTPS (REST)", "443 TLS", NARANJA, 610,
     ["POST /devices/{id}/messages/events?api-version", "Authorization: SAS   en CADA peticion",
      "Content-Type + Content-Encoding: utf-8", "<- 204 No Content  (sin sesion: Central muestra Desconectado)"]),
]
for nombre, puerto, col, y, lineas in carriles:
    caja(20, y, 330, 150, nombre, ["Puerto " + puerto, "cliente: " + {"MQTT 3.1.1": "paho-mqtt 2.x", "AMQP 1.0": "python-qpid-proton 0.40", "HTTPS (REST)": "http.client (stdlib)"}[nombre],
                                  "ESP32: " + {"MQTT 3.1.1": "PubSubClient (testigo)", "AMQP 1.0": "no implementado (sin lib ligera)", "HTTPS (REST)": "HTTPClient (Wokwi OK)"}[nombre]], col)
    flecha(350, y + 75, 560, y + 75, "TLS " + puerto.split()[0], col)
    caja(560, y, 380, 150, "IoT Hub  (" + nombre.split()[0] + ")", lineas, col)
    flecha(940, y + 75, 1080, y + 75, "", col)

# IoT Central
d.rounded_rectangle([1080, 250, 1480, 760], 12, fill=(240, 247, 255), outline=AZUL, width=3)
d.text((1096, 258), "Azure IoT Central", font=t_b, fill=AZUL)
for i, l in enumerate(["app: lab4-medidordeclima-unab", "plantilla: Hobo MX-100 v3", "", "telemetria modelada:", "   temperature  (degC)", "   humedad      (%)", "   illuminance  (lux)", "",
                       "dispositivos (1 por protocolo):", "   mqtt-lab4   ->  MQTT 8883", "   amqp-lab4   ->  AMQP 5671", "   https-lab4  ->  HTTPS 443", "   esp32-lab4  ->  Wokwi (MQTT/HTTPS)", "",
                       "Dashboard + Datos sin procesar"]):
    d.text((1096, 292 + i * 25), l, font=t_s, fill=(40, 44, 52))
d.line([940, 135, 1000, 135, 1000, 235, 1280, 235, 1280, 250], fill=AZUL, width=3)
d.text((1010, 210), "assignedHub", font=t_m, fill=AZUL)

d.text((20, 775), "Los clientes de la VM verifican el certificado TLS (el firmware ESP32 de laboratorio usa setInsecure()). Ninguna clave va en el repositorio.", font=t_s, fill=GRIS)
img.save(OUT)
print("OK", OUT)

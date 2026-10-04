#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Renderiza como imagenes tipo terminal los LOGS REALES de ../mediciones
(no se edita ningun dato: solo se recortan lineas y se colorean las etiquetas).
Uso (Windows/Linux, requiere Pillow):  python medicion/render_evidencias.py"""
import os, re, sys
from PIL import Image, ImageDraw, ImageFont

RAIZ = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
MED = os.path.join(RAIZ, "mediciones")
OUT = os.path.join(RAIZ, "evidencias")
os.makedirs(OUT, exist_ok=True)

FUENTES = ["C:/Windows/Fonts/consola.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"]
NEGRITA = ["C:/Windows/Fonts/consolab.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"]


def fuente(lista, tam):
    for f in lista:
        if os.path.exists(f):
            return ImageFont.truetype(f, tam)
    return ImageFont.load_default()


FONDO, TEXTO, GRIS = (22, 24, 29), (212, 212, 212), (120, 126, 138)
COLORES = [  # etiqueta -> color
    (r"\[ERROR\]|\[CORTE\]|RECHAZ|DESCONECT|conexion perdida|ERROR de", (255, 95, 95)),
    (r"\[SAS\]", (240, 200, 90)),
    (r"\[CBS\]", (200, 140, 255)),
    (r"\[DPS\]", (130, 170, 255)),
    (r"\[AMQP\]|\[HTTPS\]|\[MQTT\]|\[HUB\]|\[MQTT-RAW\]", (90, 200, 230)),
    (r"\[ACK|\[SENT", (120, 220, 140)),
    (r"\[TX", (150, 230, 160)),
    (r"\[MED\]|---- RESUMEN", (255, 170, 80)),
    (r"\[WiFi\]|\[TIME\]|\[DHT\]", (150, 160, 175)),
]


def color_de(linea):
    for patron, col in COLORES:
        if re.search(patron, linea):
            return col
    return TEXTO


def render(titulo, comando, lineas, salida, ancho_car=150, tam=15):
    f, fb = fuente(FUENTES, tam), fuente(NEGRITA, tam)
    ancho_c = f.getlength("M")
    alto_l = tam + 6
    lineas = [(l if len(l) <= ancho_car else l[:ancho_car - 1] + "…") for l in lineas]
    W = int(ancho_c * (ancho_car + 3)) + 24
    H = 56 + alto_l * (len(lineas) + 2) + 14
    img = Image.new("RGB", (W, H), FONDO)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 34], fill=(38, 41, 50))
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([12 + i * 22, 11, 24 + i * 22, 23], fill=c)
    d.text((90, 8), titulo, font=fb, fill=(230, 230, 230))
    y = 46
    d.text((12, y), "$ " + comando, font=fb, fill=(130, 220, 130))
    y += alto_l + 2
    for l in lineas:
        d.text((12, y), l, font=f, fill=color_de(l))
        y += alto_l
    img.save(os.path.join(OUT, salida))
    print("OK", salida, "%dx%d" % (W, H), len(lineas), "lineas")


def leer(nombre, filtro=None, desde=None, hasta=None):
    ruta = os.path.join(MED, nombre)
    L = [l.rstrip("\r\n") for l in open(ruta, encoding="utf-8", errors="replace")]
    L = [l for l in L if l.strip() and "DeprecationWarning" not in l and "return mqtt.Client" not in l]
    if filtro:
        L = [l for l in L if re.search(filtro, l)]
    return L[desde:hasta]


def corta(l, n=150):
    return l if len(l) <= n else l[:n - 1] + "…"


def main():
    # 01 AMQP: log de la VM (DPS por HTTPS, CBS, enlace, transferencias, acks, resumen)
    L = leer("log_amqp.txt")
    render("AMQP 1.0 (Proton) -> IoT Central  |  vm-lab4-iot", "python amqp/amqp_iothub.py   # IOTC_N=5", L, "01-amqp-vm-log.png", 156)

    # 02 AMQP: tramas reales de Proton (PN_TRACE_FRM=1), resumidas por tipo de trama
    T = []
    for l in leer("log_amqp_frames.txt"):
        m = re.match(r"\[0x[0-9a-f]+\]:\s*(.*)", l)
        if m:
            cuerpo = m.group(1)
            cuerpo = re.sub(r"container-id=\"[^\"]+\", ", "", cuerpo)
            cuerpo = cuerpo.replace("AMQP:FRAME:0 ", "")
            if "transfer" in cuerpo and "\\x00SpE" in cuerpo:
                cuerpo = re.sub(r"\] \(\d+\) .*", "] ... (cuerpo AMQP: properties + application-properties + data)", cuerpo) if "amqp-lab4-" not in cuerpo else \
                    re.sub(r"\] \((\d+)\) .*", r"] (\1 bytes) message-id, content-type=application/json, content-encoding=utf-8, data={3 variables}", cuerpo)
            T.append(corta(cuerpo, 156))
        elif re.search(r"\[(AMQP|CBS|SAS|TX|ACK)", l):
            T.append(corta(l, 156))
    T = [t for t in T if "EOS" not in t]
    render("Tramas AMQP 1.0 reales (PN_TRACE_FRM=1)  |  <- Hub   -> dispositivo", "PN_TRACE_FRM=1 python amqp/amqp_iothub.py", T[:36], "02-amqp-tramas-proton.png", 156, 14)
    render("Tramas AMQP 1.0 reales, sesion completa (PN_TRACE_FRM=1)", "PN_TRACE_FRM=1 python amqp/amqp_iothub.py   # IOTC_N=5", T, "02b-amqp-tramas-completo.png", 156, 14)

    # 03 HTTPS
    render("HTTPS REST -> IoT Hub / IoT Central  |  vm-lab4-iot", "python https/https_telemetria.py   # IOTC_N=5", leer("log_https.txt"), "03-https-vm-log.png", 156)

    # 04 MQTT baseline
    render("MQTT 8883 (paho) -> IoT Central, baseline Lab 3  |  vm-lab4-iot", "python mqtt/mqtt_baseline.py   # IOTC_N=5", leer("log_mqtt.txt"), "04-mqtt-vm-log.png", 156)

    # 06 corte de red: extractos de los tres protocolos (antes / durante / despues del corte)
    def extracto(arch, ev):
        L = leer(arch)
        iB = next(i for i, l in enumerate(L) if "BLOQUEANDO" in l)
        iL = next(i for i, l in enumerate(L) if "PUERTO LIBERADO" in l)
        out = [L[iB - 1], L[iB]]
        durante = [l for l in L[iB + 1:iL] if re.search(ev, l)]
        out += durante[:3]
        out += [L[iL]]
        despues = [l for l in L[iL + 1:] if "[MED]" not in l and "RESUMEN" not in l and "DESCONECTADO (rc=0)" not in l and "SAS] token nuevo" not in l]
        out += despues[:5]
        out += [l for l in L if "latencia envio->ack" in l or "espera generacion->ack" in l]
        return [corta(l, 150) for l in out]
    S = []
    for etiqueta, arch, ev in [
        ("MQTT QoS1", "log_corte_reset_mqtt-qos1.txt", r"DESCONECT|SAS\]"),
        ("AMQP", "log_corte_reset_amqp.txt", r"conexion perdida|reintentando"),
        ("HTTPS keep-alive", "log_corte_reset_https-ka.txt", r"ERROR"),
    ]:
        S.append("=== %s  (REJECT --reject-with tcp-reset 16 s; 1 msg/s; 40 mensajes) ===" % etiqueta)
        S += extracto(arch, ev)
    render("Prueba de corte de red 16 s  |  iptables en la VM", "MODO=reset CORTE=16 ./medicion/corte_red.sh", S, "06-corte-red.png", 150, 14)

    # 07 SAS: renovacion en caliente (AMQP) vs MQTT (sin mecanismo equivalente)
    A = [corta(l, 156) for l in leer("log_sas_ttl120_amqp.txt", r"CBS|SAS\]|OPEN del|RESUMEN|latencia|mensajes confirmados")]
    M = [corta(l, 156) for l in leer("log_sas_ttl120_mqtt.txt", r"SAS\]|CONNACK|DESCONECT|RESUMEN|latencia|mensajes confirmados")]
    render("SAS con vigencia de 120 s durante 330 s  |  AMQP renueva en la misma conexion",
           "IOTC_SAS_TTL=120 IOTC_N=66 python amqp/amqp_iothub.py   (y lo mismo con mqtt/mqtt_baseline.py)",
           ["=== AMQP (cliente) ==="] + A[:16] + ["", "=== MQTT (cliente): un unico CONNECT; sin DESCONECTADO hasta que el cliente termina ==="] + M, "07-sas-renovacion.png", 156, 14)

    # 08 el SDK de Python no tiene AMQP
    render("azure-iot-device (Python) 2.14.0  |  no existe transporte AMQP", "pip show azure-iot-device ; grep -ril amqp .../azure/iot/device",
           [corta(l, 150) for l in leer("log_sdk_python_sin_amqp.txt") if "Pipe" not in l and "Broken" not in l and "Exception ignored" not in l], "08-sdk-python-sin-amqp.png", 150, 15)

    # 09/10 ESP32 en Wokwi
    render("ESP32 virtual (Wokwi) -> HTTPS REST -> IoT Hub / Central", "./wokwi/build_y_simular.sh esp32-https 75",
           [corta(l, 130) for l in leer("log_esp32_https_wokwi.txt")][:26], "09-esp32-wokwi-https.png", 130, 15)
    render("ESP32 virtual (Wokwi) -> MQTT/TLS -> IoT Hub / Central (testigo)", "./wokwi/build_y_simular.sh esp32-mqtt 70",
           [corta(l, 130) for l in leer("log_esp32_mqtt_wokwi.txt")][:26], "10-esp32-wokwi-mqtt.png", 130, 15)

    # 11 HTTPS depurado con curl (facil de depurar)
    render("curl contra IoT Hub  |  204 con SAS valido, 401 con firma alterada", "curl -i -X POST https://<hub>/devices/https-lab4/messages/events ...",
           [corta(l, 140) for l in leer("log_curl_https.txt")], "11-https-curl-debug.png", 140, 15)

    # 05 tabla de medicion (salida real de bench_resumen.py)
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(RAIZ, "medicion", "bench_resumen.py")], capture_output=True, text=True, encoding="utf-8")
    render("Bytes en el cable y latencia por protocolo  |  N=51 mensajes de 53 B", "python3 medicion/bench_resumen.py",
           r.stdout.rstrip().splitlines(), "05-medicion-bytes-latencia.png", 140, 15)
    r = subprocess.run([sys.executable, os.path.join(RAIZ, "medicion", "corte_resumen.py")], capture_output=True, text=True, encoding="utf-8")
    render("Resumen de cortes de red (16 s)  |  reset = RST de firewall; drop = agujero negro", "python3 medicion/corte_resumen.py",
           r.stdout.rstrip().splitlines(), "06b-corte-resumen.png", 140, 15)


if __name__ == "__main__":
    main()

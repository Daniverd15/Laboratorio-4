#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resume mediciones/bytes.csv + CSV/logs de latencia en una tabla comparativa.
Uso:  python3 medicion/bench_resumen.py [carpeta_mediciones]"""
import csv, os, re, sys, math

DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mediciones")
PROTOCOLOS = ["mqtt-qos1", "mqtt-qos0", "mqtt-ws", "amqp", "https-ka", "https-nk"]
ETIQUETA = {"mqtt-qos1": "MQTT 8883 QoS1", "mqtt-qos0": "MQTT 8883 QoS0", "mqtt-ws": "MQTT-WS 443 QoS1",
            "amqp": "AMQP 5671", "https-ka": "HTTPS 443 keep-alive", "https-nk": "HTTPS 443 sin keep-alive"}


def pct(v, p):
    v = sorted(v)
    if not v:
        return float("nan")
    k = (len(v) - 1) * p / 100.0
    f, c = math.floor(k), math.ceil(k)
    return v[int(k)] if f == c else v[f] + (v[c] - v[f]) * (k - f)


def bytes_csv():
    d = {}
    with open(os.path.join(DIR, "bytes.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d[(r["protocolo"], int(r["n"]))] = (int(r["bytes_salida"]) + int(r["bytes_entrada"]),
                                                int(r["paquetes_salida"]) + int(r["paquetes_entrada"]))
    return d


def lat(nombre, n):
    p = os.path.join(DIR, "%s_n%d.csv" % (nombre, n))
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [float(r["ack_ms"]) for r in csv.DictReader(f) if r["ack_ms"]]


def conexion_ms(nombre):
    """Primer hito 'listo' del log N=1: tiempo hasta poder enviar."""
    p = os.path.join(DIR, "log_%s_n1.txt" % nombre)
    if not os.path.exists(p):
        return None
    txt = open(p, encoding="utf-8", errors="replace").read()
    m = re.findall(r"\[MED\] conexion: (\S+)\s+(\d+) ms", txt)
    d = {k: int(v) for k, v in m}
    for k in ("listo_para_enviar", "tcp+tls+connect(connack)", "tcp_tls"):
        if k in d:
            return d[k]
    return None


def main():
    b = bytes_csv()
    n_grande = max(n for (_, n) in b)
    filas = []
    print("| Protocolo | Setup hasta enviar (ms) | ACK p50 (ms) | ACK p95 (ms) | Bytes/msg en cable | Bytes conexión+1 msg | Paq/msg |")
    print("|---|---|---|---|---|---|---|")
    for p in PROTOCOLOS:
        if (p, 1) not in b or (p, n_grande) not in b:
            continue
        b1, p1 = b[(p, 1)]
        bn, pn = b[(p, n_grande)]
        por_msg = (bn - b1) / float(n_grande - 1)
        paq_msg = (pn - p1) / float(n_grande - 1)
        l = lat(p, n_grande)
        con = conexion_ms(p)
        l50 = "%.0f" % pct(l, 50) if l else "n/a (QoS0: sin ack)"
        l95 = "%.0f" % pct(l, 95) if l else "n/a"
        print("| %s | %s | %s | %s | %.0f | %d | %.1f |" % (
            ETIQUETA[p], "n/d" if con is None else con, l50, l95, por_msg, b1, paq_msg))
        filas.append((p, con, l, por_msg, b1, paq_msg))
    return filas


if __name__ == "__main__":
    main()

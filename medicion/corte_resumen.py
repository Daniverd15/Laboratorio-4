#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resume las pruebas de corte de red (mediciones/log_corte_<modo>_<proto>.txt).
Por cada protocolo calcula: mensajes generados/confirmados, perdidos, reenvios
(TX repetido del mismo #n), tiempo desde que vuelve la red hasta el primer ACK y
espera maxima generacion->ack.   Uso: python3 medicion/corte_resumen.py [carpeta]"""
import csv, os, re, sys, glob
from datetime import datetime

DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mediciones")


def t(linea):
    m = re.match(r"(\d\d):(\d\d):(\d\d)\.(\d+)", linea)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + float("0." + m.group(4)) if m else None


def analizar(ruta):
    lineas = open(ruta, encoding="utf-8", errors="replace").read().splitlines()
    t_corte = t_libre = None
    acks, tx = [], {}
    reconex = 0
    for l in lineas:
        if "BLOQUEANDO" in l:
            t_corte = t(l)
        elif "PUERTO LIBERADO" in l:
            t_libre = t(l)
        m = re.search(r"\[(?:ACK|SENT) #(\d+)\]", l) or re.search(r"\[TX #(\d+)\] POST .* HTTP 20\d", l)
        if m:
            acks.append((int(m.group(1)), t(l)))
        m = re.search(r"\[TX #(\d+)\] (?:transfer|PUBLISH)", l)
        if m:
            tx[int(m.group(1))] = tx.get(int(m.group(1)), 0) + 1
        if re.search(r"CONNACK rc=0|OPEN del Hub recibido|TCP\+TLS establecido", l):
            reconex += 1
    unicos = {n for n, _ in acks}
    primer = min((ts for n, ts in acks if t_libre and ts and ts > t_libre), default=None)
    base = os.path.basename(ruta)[len("log_corte_"):-4]
    modo, proto = base.split("_", 1)
    csvp = os.path.join(DIR, "corte_%s.csv" % base)
    e2e = []
    if os.path.exists(csvp):
        with open(csvp, encoding="utf-8") as f:
            e2e = [float(r["e2e_ms"]) for r in csv.DictReader(f) if r["e2e_ms"]]
    return dict(modo=modo, proto=proto, confirmados=len(unicos), reenvios=sum(1 for v in tx.values() if v > 1),
                recuperacion=(primer - t_libre) if primer and t_libre else None,
                conexiones=reconex, espera_max=max(e2e) / 1000 if e2e else None)


def main():
    print("| Modo | Protocolo | Confirmados/40 | Perdidos | Reenvíos | Conexiones | Red vuelve -> 1.er ACK | Espera máx gen->ack |")
    print("|---|---|---|---|---|---|---|---|")
    for ruta in sorted(glob.glob(os.path.join(DIR, "log_corte_*.txt"))):
        r = analizar(ruta)
        rec = "n/d" if r["recuperacion"] is None else "%.1f s" % r["recuperacion"]
        esp = "n/d" if r["espera_max"] is None else "%.1f s" % r["espera_max"]
        print("| %s | %s | %d | %d | %d | %d | %s | %s |" % (r["modo"], r["proto"], r["confirmados"],
              40 - r["confirmados"], r["reenvios"], r["conexiones"], rec, esp))


if __name__ == "__main__":
    main()

#!/bin/bash
# Laboratorio 4 - Prueba de corte de red (se ejecuta EN LA VM).
# Corre cada cliente 40 mensajes (1 por segundo); a los 10 s bloquea el puerto
# del protocolo durante CORTE segundos y lo libera. Muestra como cada protocolo
# detecta la caida, reconecta y si pierde o duplica mensajes.
#
#   MODO=reset  (defecto)  REJECT --reject-with tcp-reset  (un firewall/NAT que corta)
#   MODO=drop              DROP silencioso                  (agujero negro: la red "se traga" los paquetes)
set -u
BASE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$BASE/mediciones"; mkdir -p "$OUT"
PY="${PY:-$BASE/.venv/bin/python}"
CORTE="${CORTE:-16}"
MODO="${MODO:-reset}"
INTERVALO="${INTERVALO:-1}"
N="${N:-40}"

probar() {   # nombre puerto envfile script [VAR=valor ...]
  local nombre=$1 puerto=$2 envf=$3 script=$4; shift 4
  local log="$OUT/log_corte_${MODO}_${nombre}.txt"
  : > "$log"
  (
    set -a; . "$envf"; set +a
    export IOTC_N="$N" IOTC_SEND_INTERVAL="$INTERVALO" IOTC_BACKOFF_MAX="${BACKOFF_MAX:-2}" IOTC_CSV="$OUT/corte_${MODO}_${nombre}.csv"
    for kv in "$@"; do export "$kv"; done
    exec "$PY" "$BASE/$script"
  ) >> "$log" 2>&1 &
  local pid=$!
  sleep 10
  if [ "$MODO" = "drop" ]; then ACCION="DROP"; else ACCION="REJECT --reject-with tcp-reset"; fi
  echo "$(date +%H:%M:%S.%3N) [CORTE] === BLOQUEANDO puerto $puerto ($ACCION) durante ${CORTE}s ===" | tee -a "$log"
  sudo iptables -I OUTPUT 1 -p tcp --dport "$puerto" -j $ACCION
  sleep "$CORTE"
  sudo iptables -D OUTPUT -p tcp --dport "$puerto" -j $ACCION
  echo "$(date +%H:%M:%S.%3N) [CORTE] === PUERTO LIBERADO ===" | tee -a "$log"
  wait "$pid"
  echo "--- $nombre ($MODO) ---"; grep -E "CORTE|ERROR|DESCONECT|reintent|Reconect|CONNACK|conexion perdida|ESPERA|RESUMEN|\[MED\]" "$log" | head -30
}

probar mqtt-qos1 8883 "$BASE/env/mqtt.env"  mqtt/mqtt_baseline.py    IOTC_QOS=1
probar amqp      5671 "$BASE/env/amqp.env"  amqp/amqp_iothub.py
probar https-ka  443  "$BASE/env/https.env" https/https_telemetria.py IOTC_HTTP_KEEPALIVE=1

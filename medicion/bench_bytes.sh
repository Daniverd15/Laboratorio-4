#!/bin/bash
# Laboratorio 4 - Bytes en el cable + latencias por protocolo (se ejecuta EN LA VM).
#
# Para cada protocolo corre el cliente dos veces:
#   N=1   -> costo de "abrir la conexion, mandar 1 mensaje y cerrar"
#   N=51  -> idem + 50 mensajes mas  => bytes/mensaje en regimen = (B51-B1)/50
# Los bytes salen de contadores iptables sobre el puerto del protocolo
# (IP completo: cabeceras IP+TCP, TLS, protocolo, ACKs). Las latencias salen de
# los CSV que escribe cada cliente (envio -> confirmacion del Hub).
#
# Requisitos: sudo (iptables), venv en ./.venv y un archivo de credenciales por
# dispositivo en ./env/{mqtt,amqp,https}.env  (NO versionados; ver .env.example):
#     IOTC_ID_SCOPE=...  IOTC_DEVICE_ID=...  IOTC_DEVICE_KEY=...  IOTC_HUB=<hub>.azure-devices.net
set -u
BASE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$BASE/mediciones"; mkdir -p "$OUT"
PY="${PY:-$BASE/.venv/bin/python}"
N_GRANDE="${N_GRANDE:-51}"
INTERVALO="${INTERVALO:-1}"
: > "$OUT/bytes.csv"
echo "protocolo,n,bytes_salida,bytes_entrada,paquetes_salida,paquetes_entrada" >> "$OUT/bytes.csv"

correr() {   # nombre puerto envfile script N [VAR=valor ...]
  local nombre=$1 puerto=$2 envf=$3 script=$4 n=$5; shift 5
  sudo iptables -I OUTPUT 1 -p tcp --dport "$puerto" -m comment --comment lab4bench
  sudo iptables -I INPUT  1 -p tcp --sport "$puerto" -m comment --comment lab4bench
  sudo iptables -Z OUTPUT 1; sudo iptables -Z INPUT 1
  (
    set -a; . "$envf"; set +a
    export IOTC_N="$n" IOTC_SEND_INTERVAL="$INTERVALO" IOTC_CSV="$OUT/${nombre}_n${n}.csv"
    for kv in "$@"; do export "$kv"; done
    exec "$PY" "$BASE/$script"
  ) > "$OUT/log_${nombre}_n${n}.txt" 2>&1
  sleep 1
  local o i
  o=$(sudo iptables -L OUTPUT -v -n -x | awk '/lab4bench/{print $2","$1; exit}')
  i=$(sudo iptables -L INPUT  -v -n -x | awk '/lab4bench/{print $2","$1; exit}')
  sudo iptables -D OUTPUT -p tcp --dport "$puerto" -m comment --comment lab4bench
  sudo iptables -D INPUT  -p tcp --sport "$puerto" -m comment --comment lab4bench
  echo "$nombre,$n,${o%,*},${i%,*},${o#*,},${i#*,}" | tee -a "$OUT/bytes.csv"
}

for N in 1 "$N_GRANDE"; do
  correr mqtt-qos1  8883 "$BASE/env/mqtt.env"  mqtt/mqtt_baseline.py   "$N" IOTC_QOS=1
  correr mqtt-qos0  8883 "$BASE/env/mqtt.env"  mqtt/mqtt_baseline.py   "$N" IOTC_QOS=0
  correr mqtt-ws    443  "$BASE/env/mqtt.env"  mqtt/mqtt_baseline.py   "$N" IOTC_QOS=1 IOTC_MQTT_TRANSPORT=websockets
  correr amqp       5671 "$BASE/env/amqp.env"  amqp/amqp_iothub.py     "$N"
  correr https-ka   443  "$BASE/env/https.env" https/https_telemetria.py "$N" IOTC_HTTP_KEEPALIVE=1
  correr https-nk   443  "$BASE/env/https.env" https/https_telemetria.py "$N" IOTC_HTTP_KEEPALIVE=0
done
echo "Listo: $OUT/bytes.csv y CSV/logs por protocolo"

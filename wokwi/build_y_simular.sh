#!/bin/bash
# Laboratorio 4 - compila el firmware del ESP32 y lo simula en Wokwi (sin abrir el navegador).
#
#   ESP_ID_SCOPE=0ne... ESP_DEVICE_ID=esp32-lab4 ESP_DEVICE_KEY=<primary key> \
#   WOKWI_CLI_TOKEN=<token CI de wokwi.com/dashboard/ci> \
#   ./build_y_simular.sh esp32-https [segundos_de_simulacion]
#
# Las credenciales entran como -D en tiempo de compilacion: NO se escriben en el codigo ni en el repo.
# Requisitos: PlatformIO (pio), esptool (pip install esptool) y wokwi-cli (github.com/wokwi/wokwi-cli).
set -e
PROY="${1:?uso: $0 esp32-mqtt|esp32-https [segundos]}"
SEG="${2:-100}"
AQUI="$(cd "$(dirname "$0")" && pwd)"
PIO="${PIO:-pio}"
WOKWI_CLI="${WOKWI_CLI:-wokwi-cli}"
BOOT_APP0="${BOOT_APP0:-$HOME/.platformio/packages/framework-arduinoespressif32/tools/partitions/boot_app0.bin}"
command -v cygpath >/dev/null && BOOT_APP0="$(cygpath -m "$BOOT_APP0")"   # Git Bash en Windows: ruta que entienda Python
: "${ESP_ID_SCOPE:?}" "${ESP_DEVICE_ID:?}" "${ESP_DEVICE_KEY:?}" "${WOKWI_CLI_TOKEN:?}"
export WOKWI_CLI_TOKEN
export PLATFORMIO_BUILD_FLAGS="-DID_SCOPE=\\\"$ESP_ID_SCOPE\\\" -DDEVICE_ID=\\\"$ESP_DEVICE_ID\\\" -DDEVICE_KEY=\\\"$ESP_DEVICE_KEY\\\""

cd "$AQUI/$PROY"
"$PIO" run
B=.pio/build/esp32dev
# Wokwi necesita una imagen de flash completa (bootloader + particiones + boot_app0 + firmware)
python -m esptool --chip esp32 merge-bin -o "$B/firmware_merged.bin" --flash-mode dio --flash-size 4MB \
  0x1000 "$B/bootloader.bin" 0x8000 "$B/partitions.bin" 0xe000 "$BOOT_APP0" 0x10000 "$B/firmware.bin"
"$WOKWI_CLI" --timeout $((SEG * 1000)) .

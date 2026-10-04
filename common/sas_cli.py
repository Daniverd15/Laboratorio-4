#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Imprime un token SAS (para depurar con curl).
Uso:  python common/sas_cli.py <recurso> <clave_base64> [ttl_s] [policy]
  recurso = "<hub>.azure-devices.net/devices/<id>"      (telemetria)
          | "<idScope>/registrations/<id>"  + policy=registration   (DPS)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from iotc_common import generar_sas

if len(sys.argv) < 3:
    sys.exit(__doc__)
ttl = int(sys.argv[3]) if len(sys.argv) > 3 else 3600
print(generar_sas(sys.argv[1], sys.argv[2], ttl, sys.argv[4] if len(sys.argv) > 4 else None)[0])

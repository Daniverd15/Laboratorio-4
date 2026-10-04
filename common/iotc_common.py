# -*- coding: utf-8 -*-
"""
Laboratorio 4 - UNAB-Ambiental
Utilidades COMUNES a los tres clientes (MQTT, AMQP, HTTPS) para que la
comparacion sea justa: mismo SAS, mismo aprovisionamiento DPS, mismas 3
variables y misma forma de medir.

  generar_sas()          Token SAS construido a mano (HMAC-SHA256).
  provisionar_dps_https  Primer salto: DPS por HTTPS (REST) -> IoT Hub asignado.
  lectura_sensor()       Las 3 variables de la plantilla Hobo MX-100 v3.
  Medidor                Latencias, bytes y resumen/CSV.
"""
import os, sys, time, json, hmac, hashlib, base64, random, ssl, math
import threading, collections
import urllib.parse, urllib.request, urllib.error

DPS_HOST = "global.azure-devices-provisioning.net"
DPS_API = "2021-06-01"
HUB_API = "2021-04-12"
BACKOFF_MAX = int(os.environ.get("IOTC_BACKOFF_MAX", "16"))   # tope de espera entre reintentos (s)


def log(msg):
    """Linea de log con hora y milisegundos (los logs son la evidencia)."""
    t = time.time()
    print("%s.%03d %s" % (time.strftime("%H:%M:%S", time.localtime(t)),
                          int((t % 1) * 1000), msg), flush=True)


def cfg(nombre, defecto=None, requerida=False):
    v = os.environ.get(nombre, defecto)
    if requerida and not v:
        sys.exit("Falta la variable de entorno %s (ver .env.example)" % nombre)
    return v


# --------------------------- Generacion del SAS ---------------------------
def generar_sas(resource_uri, key_b64, expiry_seconds=3600, policy_name=None):
    """SharedAccessSignature sr=<uri>&sig=<firma>&se=<expira>[&skn=<policy>]
    firma = base64( HMAC-SHA256( base64decode(clave), "<uri-url-encoded>\\n<expira>" ) )
    Devuelve (token, epoch_de_expiracion)."""
    encoded_uri = urllib.parse.quote(resource_uri, safe="")
    expiry = int(time.time()) + int(expiry_seconds)
    to_sign = ("%s\n%d" % (encoded_uri, expiry)).encode("utf-8")
    signature = base64.b64encode(
        hmac.new(base64.b64decode(key_b64), to_sign, hashlib.sha256).digest()
    ).decode("utf-8")
    token = "SharedAccessSignature sr=%s&sig=%s&se=%d" % (
        encoded_uri, urllib.parse.quote(signature, safe=""), expiry)
    if policy_name:
        token += "&skn=%s" % policy_name
    return token, expiry


# ----------------------------- TLS (verificado) ----------------------------
def tls_context():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_default_certs()          # CA del sistema (DigiCert Global Root G2)
    return ctx


# --------------------- DPS por HTTPS (primer salto) ------------------------
def _dps_req(metodo, url, sas, cuerpo=None):
    data = None if cuerpo is None else json.dumps(cuerpo).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=metodo)
    req.add_header("Authorization", sas)
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30, context=tls_context()) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def provisionar_dps_https(id_scope, device_id, device_key, model_id=None):
    """Registra el dispositivo en DPS por REST y devuelve el hostname del IoT Hub.
    PUT .../registrations/{id}/register  ->  202 + operationId
    GET .../registrations/{id}/operations/{op} hasta status=assigned"""
    reg = "%s/registrations/%s" % (id_scope, device_id)
    sas, _ = generar_sas(reg, device_key, policy_name="registration")
    base = "https://%s/%s" % (DPS_HOST, reg)
    cuerpo = {"registrationId": device_id}
    if model_id:
        cuerpo["payload"] = {"iotcModelId": model_id}
    log("[DPS] HTTPS PUT %s/register  (SAS sr=%s)" % (base, reg))
    code, r = _dps_req("PUT", "%s/register?api-version=%s" % (base, DPS_API), sas, cuerpo)
    if code not in (200, 202):
        raise RuntimeError("DPS register respondio %s: %s" % (code, r))
    op = r.get("operationId")
    for _ in range(30):
        if r.get("status") == "assigned":
            break
        time.sleep(2)
        code, r = _dps_req("GET", "%s/operations/%s?api-version=%s" % (base, op, DPS_API), sas)
        log("[DPS] HTTPS GET operations -> %s status=%s" % (code, r.get("status")))
    hub = (r.get("registrationState") or {}).get("assignedHub")
    if not hub:
        raise RuntimeError("DPS no asigno hub: %s" % r)
    log("[DPS] asignado -> %s" % hub)
    return hub


def obtener_hub(id_scope, device_id, device_key):
    """Si IOTC_HUB esta definido se salta DPS (util en los benchmarks)."""
    hub = os.environ.get("IOTC_HUB")
    if hub:
        log("[DPS] omitido, IOTC_HUB=%s" % hub)
        return hub
    return provisionar_dps_https(id_scope, device_id, device_key,
                                 os.environ.get("IOTC_MODEL_ID"))


# ------------------------------- Telemetria --------------------------------
def lectura_sensor():
    """Mismas 3 variables que el Lab 2/3, con los nombres EXACTOS de la
    plantilla Hobo MX-100 v3 (temperature / humedad / illuminance)."""
    return {
        "temperature": round(random.uniform(20.0, 32.0), 1),
        "humedad":     round(random.uniform(40.0, 70.0), 1),
        "illuminance": random.randint(150, 750),
    }


def payload_json():
    return json.dumps(lectura_sensor(), separators=(",", ":")).encode("utf-8")


class Productor(threading.Thread):
    """Genera una lectura cada `intervalo` s aunque no haya red (cola en memoria).
    El sensor mide a su ritmo; el protocolo solo decide COMO y CUANDO entrega.
    Es el equivalente del encolado de paho con QoS 1 del Lab 3, para que MQTT,
    AMQP y HTTPS se comparen con la misma semantica 'al menos una vez'."""

    def __init__(self, intervalo, n_total=0):
        super().__init__(daemon=True)
        self.intervalo = intervalo
        self.n_total = n_total          # 0 = infinito
        self.cola = collections.deque()
        self.lock = threading.Lock()
        self.generados = 0

    def run(self):
        while not self.n_total or self.generados < self.n_total:
            self.generados += 1
            with self.lock:
                self.cola.append((self.generados, payload_json(), time.time()))
            time.sleep(self.intervalo)

    def pendientes(self):
        with self.lock:
            return list(self.cola)

    def quitar(self, n):
        with self.lock:
            for it in list(self.cola):
                if it[0] == n:
                    self.cola.remove(it)
                    return

    def terminado(self):
        """True cuando ya se generaron y confirmaron las N lecturas."""
        with self.lock:
            return bool(self.n_total) and self.generados >= self.n_total and not self.cola


# -------------------------------- Mediciones -------------------------------
def percentil(valores, p):
    if not valores:
        return float("nan")
    v = sorted(valores)
    k = (len(v) - 1) * p / 100.0
    f, c = math.floor(k), math.ceil(k)
    return v[int(k)] if f == c else v[f] + (v[c] - v[f]) * (k - f)


class Medidor:
    """Recoge por mensaje: bytes de payload, latencia envio->confirmacion
    (ack_ms) y latencia generacion->confirmacion (e2e_ms, incluye la espera en
    cola durante un corte de red)."""

    def __init__(self, protocolo):
        self.protocolo = protocolo
        self.filas = []          # (n, bytes, ack_ms, e2e_ms)
        self.tiempos = {}        # hitos de conexion (ms)

    def hito(self, nombre, ms):
        self.tiempos[nombre] = ms

    def mensaje(self, n, nbytes, ack_ms, e2e_ms=None):
        self.filas.append((n, nbytes, ack_ms, e2e_ms if e2e_ms is not None else ack_ms))

    def resumen(self):
        log("---- RESUMEN [%s] ----" % self.protocolo)
        for k, v in self.tiempos.items():
            log("[MED] conexion: %-26s %8.0f ms" % (k, v))
        if not self.filas:
            log("[MED] sin mensajes confirmados")
            return
        nb = [f[1] for f in self.filas]
        ack = [f[2] for f in self.filas if f[2] is not None]
        e2e = [f[3] for f in self.filas if f[3] is not None]
        log("[MED] mensajes confirmados=%d  payload_bytes prom=%.1f" % (len(self.filas), sum(nb) / len(nb)))
        if ack:
            log("[MED] latencia envio->ack ms: min=%.0f p50=%.0f p95=%.0f max=%.0f prom=%.0f"
                % (min(ack), percentil(ack, 50), percentil(ack, 95), max(ack), sum(ack) / len(ack)))
        if e2e:
            log("[MED] espera generacion->ack ms: p50=%.0f max=%.0f" % (percentil(e2e, 50), max(e2e)))

    def csv(self, path):
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write("protocolo,n,payload_bytes,ack_ms,e2e_ms\n")
            for (n, nb, ack, e2e) in self.filas:
                f.write("%s,%d,%d,%s,%s\n" % (
                    self.protocolo, n, nb,
                    "" if ack is None else "%.1f" % ack,
                    "" if e2e is None else "%.1f" % e2e))
        log("[MED] CSV escrito en %s" % path)

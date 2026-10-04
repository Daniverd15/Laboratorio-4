/*
 * main.cpp  -  UNAB-Ambiental  (Laboratorio 4)  -  ESP32 + HTTPS (REST)
 * =====================================================================
 * TERCER PROTOCOLO en el microcontrolador (Wokwi):
 *   1) DPS por HTTPS:  PUT .../registrations/{id}/register  +  GET .../operations/{op}
 *   2) Telemetria:     POST https://{hub}/devices/{id}/messages/events  -> 204
 *
 * Mismas 3 variables que el resto del laboratorio:
 *   DHT22          -> temperature (C), humedad (% RH)   [GPIO 15]
 *   Potenciometro  -> illuminance (lux, 0..1000)        [GPIO 34]
 *
 * Sin PubSubClient ni broker MQTT: solo HTTPClient (viene en el core de ESP32).
 * El SAS se firma a mano con mbedTLS (HMAC-SHA256), igual que en el firmware MQTT.
 *
 * SEGURIDAD: las credenciales se inyectan al compilar
 *   (-DID_SCOPE=\"..\" -DDEVICE_ID=\"..\" -DDEVICE_KEY=\"..\"); nunca van en el repo.
 *   Simplificacion de laboratorio: setInsecure() (no valida el certificado del
 *   servidor), igual que el firmware del Lab 2. En produccion: setCACert().
 * =====================================================================
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <time.h>
#include "mbedtls/md.h"
#include "mbedtls/base64.h"

#ifndef ID_SCOPE
#define ID_SCOPE      "PEGA_AQUI_EL_ID_SCOPE"
#endif
#ifndef DEVICE_ID
#define DEVICE_ID     "esp32-lab4"
#endif
#ifndef DEVICE_KEY
#define DEVICE_KEY    "PEGA_AQUI_LA_CLAVE_DEL_ESP32"
#endif

#define WIFI_SSID     "Wokwi-GUEST"
#define WIFI_PASS     ""

#define DHT_PIN   15
#define DHT_TYPE  DHT22
#define POT_PIN   34
#define SEND_INTERVAL_MS  5000

const char* DPS_HOST = "global.azure-devices-provisioning.net";
const char* DPS_API  = "2021-06-01";
const char* HUB_API  = "2021-04-12";

DHT dht(DHT_PIN, DHT_TYPE);
WiFiClientSecure net;
HTTPClient http;

uint8_t  decodedKey[64];
size_t   decodedKeyLen = 0;
String   assignedHub = "";
unsigned long lastSend = 0;
unsigned long nTx = 0;

// ------------------------------ SAS a mano --------------------------------
String urlEncode(const String &src) {
  String out = "";
  const char *hex = "0123456789ABCDEF";
  for (size_t i = 0; i < src.length(); i++) {
    char c = src[i];
    if (('a' <= c && c <= 'z') || ('A' <= c && c <= 'Z') ||
        ('0' <= c && c <= '9') || c == '-' || c == '_' || c == '.' || c == '~') {
      out += c;
    } else {
      out += '%'; out += hex[(c >> 4) & 0xF]; out += hex[c & 0xF];
    }
  }
  return out;
}

String hmacSha256Base64(const String &message) {
  uint8_t hmacResult[32];
  mbedtls_md_context_t ctx;
  const mbedtls_md_info_t *info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  mbedtls_md_init(&ctx);
  mbedtls_md_setup(&ctx, info, 1);
  mbedtls_md_hmac_starts(&ctx, decodedKey, decodedKeyLen);
  mbedtls_md_hmac_update(&ctx, (const unsigned char *)message.c_str(), message.length());
  mbedtls_md_hmac_finish(&ctx, hmacResult);
  mbedtls_md_free(&ctx);
  unsigned char b64[64];
  size_t olen = 0;
  mbedtls_base64_encode(b64, sizeof(b64), &olen, hmacResult, 32);
  b64[olen] = 0;
  return String((char *)b64);
}

String createSasToken(const String &resourceUri, unsigned long expiry, const char *policy = nullptr) {
  String encUri = urlEncode(resourceUri);
  String sig = hmacSha256Base64(encUri + "\n" + String(expiry));
  String tok = "SharedAccessSignature sr=" + encUri + "&sig=" + urlEncode(sig) + "&se=" + String(expiry);
  if (policy) tok += String("&skn=") + policy;
  return tok;
}

unsigned long tokenExpiry() { return (unsigned long)time(nullptr) + 3600UL; }

// ------------------------------ DPS por HTTPS ------------------------------
String dpsHttps() {
  String reg  = String(ID_SCOPE) + "/registrations/" + DEVICE_ID;
  String sas  = createSasToken(reg, tokenExpiry(), "registration");
  String base = String("https://") + DPS_HOST + "/" + reg;
  net.setInsecure();

  Serial.printf("[DPS] HTTPS PUT %s/register\n", base.c_str());
  http.begin(net, base + "/register?api-version=" + DPS_API);
  http.addHeader("Authorization", sas);
  http.addHeader("Content-Type", "application/json");
  int code = http.PUT(String("{\"registrationId\":\"") + DEVICE_ID + "\"}");
  String body = http.getString();
  http.end();
  Serial.printf("[DPS] register -> HTTP %d\n", code);
  if (code != 200 && code != 202) { Serial.println(body); return ""; }

  StaticJsonDocument<512> d0;
  deserializeJson(d0, body);
  String op = String((const char *)(d0["operationId"] | ""));

  for (int i = 0; i < 12; i++) {
    delay(2000);
    http.begin(net, base + "/operations/" + op + "?api-version=" + DPS_API);
    http.addHeader("Authorization", sas);
    code = http.GET();
    body = http.getString();
    http.end();
    StaticJsonDocument<1024> d;
    if (deserializeJson(d, body)) continue;
    String st = String((const char *)(d["status"] | ""));
    Serial.printf("[DPS] GET operations -> HTTP %d status=%s\n", code, st.c_str());
    if (st == "assigned") {
      String hub = String((const char *)d["registrationState"]["assignedHub"]);
      Serial.printf("[DPS] assignedHub = %s\n", hub.c_str());
      return hub;
    }
  }
  return "";
}

// ------------------------------- Telemetria --------------------------------
void publishTelemetry() {
  float t = dht.readTemperature();
  float h = dht.readHumidity();
  int lux = map(analogRead(POT_PIN), 0, 4095, 0, 1000);
  if (isnan(t) || isnan(h)) { Serial.println("[DHT] lectura invalida, se omite envio."); return; }

  StaticJsonDocument<128> doc;
  doc["temperature"] = t;
  doc["humedad"] = h;
  doc["illuminance"] = lux;
  char buf[128];
  size_t n = serializeJson(doc, buf);

  String resource = assignedHub + "/devices/" + DEVICE_ID;
  String sas = createSasToken(resource, tokenExpiry());
  String url = "https://" + assignedHub + "/devices/" + DEVICE_ID + "/messages/events?api-version=" + HUB_API;

  http.setReuse(true);                         // HTTP keep-alive: reutiliza la conexion TLS
  http.begin(net, url);
  http.addHeader("Authorization", sas);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Content-Encoding", "utf-8"); // sin esto IoT Central no interpreta el JSON
  unsigned long t0 = millis();
  int code = http.POST((uint8_t *)buf, n);
  unsigned long dt = millis() - t0;
  http.end();
  Serial.printf("[TX #%lu] POST bytes=%u -> HTTP %d en %lu ms  %s\n", ++nTx, (unsigned)n, code, dt, buf);
}

void connectWiFi() {
  Serial.printf("[WiFi] Conectando a %s ...\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) { delay(300); Serial.print("."); }
  Serial.printf("\n[WiFi] OK  IP=%s\n", WiFi.localIP().toString().c_str());
}

void syncTime() {
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  Serial.print("[TIME] Sincronizando NTP");
  while (time(nullptr) < 1700000000) { delay(300); Serial.print("."); }
  Serial.printf("\n[TIME] epoch=%ld\n", (long)time(nullptr));
}

void setup() {
  Serial.begin(115200);
  delay(500);
  dht.begin();
  if (mbedtls_base64_decode(decodedKey, sizeof(decodedKey), &decodedKeyLen,
        (const unsigned char *)DEVICE_KEY, strlen(DEVICE_KEY)) != 0) {
    Serial.println("[ERR] DEVICE_KEY no es base64 valido. Revisa la Primary Key.");
  }
  connectWiFi();
  syncTime();
  assignedHub = dpsHttps();
  if (assignedHub == "") Serial.println("[FATAL] DPS fallo. Revisa ID_SCOPE / DEVICE_ID / DEVICE_KEY.");
  else Serial.println("[HTTPS] listo: enviando telemetria por REST");
}

void loop() {
  if (assignedHub != "" && millis() - lastSend > SEND_INTERVAL_MS) {
    lastSend = millis();
    publishTelemetry();
  }
  delay(20);
}

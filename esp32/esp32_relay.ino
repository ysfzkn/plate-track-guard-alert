/*
 * GateGuard — ESP32 Alarm Relay Controller
 *
 * Listens for HTTP requests from the FastAPI backend
 * and toggles a relay (siren/strobe) on GPIO 4.
 *
 * Endpoints:
 *   GET /alarm/on   → Relay HIGH (siren ON)
 *   GET /alarm/off  → Relay LOW  (siren OFF)
 *   GET /status     → JSON relay state
 *
 * Hardware:
 *   ESP32 GPIO 4 → Relay IN
 *   Relay COM/NO  → 12V Siren + Strobe
 */

#include <WiFi.h>
#include <WebServer.h>

// ==================== CONFIGURATION ====================
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

const int RELAY_PIN    = 4;     // GPIO pin connected to relay
const int LED_PIN      = 2;     // Built-in LED for status
const int SERVER_PORT  = 80;
// =======================================================

WebServer server(SERVER_PORT);
bool relayState = false;

// --- WiFi Connection ---
void connectWiFi() {
    Serial.print("WiFi'a baglaniyor: ");
    Serial.println(WIFI_SSID);

    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 30) {
        delay(500);
        Serial.print(".");
        digitalWrite(LED_PIN, !digitalRead(LED_PIN)); // Blink while connecting
        attempts++;
    }

    if (WiFi.status() == WL_CONNECTED) {
        Serial.println();
        Serial.print("Baglandi! IP: ");
        Serial.println(WiFi.localIP());
        digitalWrite(LED_PIN, HIGH);
    } else {
        Serial.println();
        Serial.println("WiFi baglantisi basarisiz! Yeniden deneniyor...");
        delay(5000);
        ESP.restart();
    }
}

// --- HTTP Handlers ---
void handleAlarmOn() {
    relayState = true;
    digitalWrite(RELAY_PIN, HIGH);
    digitalWrite(LED_PIN, HIGH);

    Serial.println("[ALARM] Relay ON — Siren aktif!");

    server.sendHeader("Access-Control-Allow-Origin", "*");
    server.send(200, "application/json", "{\"status\":\"ok\",\"relay\":\"on\"}");
}

void handleAlarmOff() {
    relayState = false;
    digitalWrite(RELAY_PIN, LOW);
    digitalWrite(LED_PIN, LOW);

    Serial.println("[ALARM] Relay OFF — Siren kapandi.");

    server.sendHeader("Access-Control-Allow-Origin", "*");
    server.send(200, "application/json", "{\"status\":\"ok\",\"relay\":\"off\"}");
}

void handleStatus() {
    String json = "{\"relay\":\"";
    json += relayState ? "on" : "off";
    json += "\",\"uptime\":";
    json += String(millis() / 1000);
    json += ",\"ip\":\"";
    json += WiFi.localIP().toString();
    json += "\"}";

    server.sendHeader("Access-Control-Allow-Origin", "*");
    server.send(200, "application/json", json);
}

void handleNotFound() {
    server.send(404, "text/plain", "Endpoint bulunamadi");
}

// --- Setup ---
void setup() {
    Serial.begin(115200);
    Serial.println();
    Serial.println("================================");
    Serial.println("  GateGuard ESP32 Relay v1.0");
    Serial.println("================================");

    // Pin setup
    pinMode(RELAY_PIN, OUTPUT);
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(RELAY_PIN, LOW);
    digitalWrite(LED_PIN, LOW);

    // WiFi
    connectWiFi();

    // HTTP routes
    server.on("/alarm/on",  HTTP_GET, handleAlarmOn);
    server.on("/alarm/off", HTTP_GET, handleAlarmOff);
    server.on("/status",    HTTP_GET, handleStatus);
    server.onNotFound(handleNotFound);

    server.begin();
    Serial.print("HTTP Server baslatildi, port: ");
    Serial.println(SERVER_PORT);
    Serial.println("Hazir. Komut bekleniyor...");
}

// --- Loop ---
void loop() {
    server.handleClient();

    // Reconnect WiFi if disconnected
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("WiFi baglantisi kesildi, yeniden baglaniyor...");
        connectWiFi();
    }

    // Heartbeat blink when idle (no alarm)
    static unsigned long lastBlink = 0;
    if (!relayState && millis() - lastBlink > 2000) {
        digitalWrite(LED_PIN, !digitalRead(LED_PIN));
        lastBlink = millis();
    }
}

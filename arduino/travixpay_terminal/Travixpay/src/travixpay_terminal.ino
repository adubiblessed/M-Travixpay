// ============================================
// TravixPay Transit Payment Terminal
// Arduino Uno + MFRC522 + USB Serial
// ============================================
//
// Architecture: Non-blocking state machine
// - RFID polling every 50ms (non-blocking)
// - Serial send/receive with timeout
// - LED/buzzer feedback without delay()
// - Duplicate tap prevention via cooldown
//
// Flow: IDLE → CARD_DETECTED → WAITING_RESPONSE → FEEDBACK → IDLE
//
// Pin Mapping:
//   MFRC522 SDA  → Pin 10
//   MFRC522 RST  → Pin 9
//   MFRC522 MOSI → Pin 11
//   MFRC522 MISO → Pin 12
//   MFRC522 SCK  → Pin 13
//   Green LED    → Pin 4
//   Red LED      → Pin 5
//   Power LED    → Pin 6
//   Buzzer       → Pin 7
//

#include <SPI.h>
#include <MFRC522.h>
#include <ArduinoJson.h>
#include "config.h"

// ============================================
// State Machine
// ============================================
enum TerminalState {
  STATE_IDLE,
  STATE_CARD_DETECTED,
  STATE_WAITING_RESPONSE,
  STATE_FEEDBACK_APPROVED,
  STATE_FEEDBACK_DECLINED,
  STATE_ERROR
};

TerminalState currentState = STATE_IDLE;

// ============================================
// Hardware
// ============================================
MFRC522 rfid(RFID_SS_PIN, RFID_RST_PIN);

// ============================================
// Timing
// ============================================
unsigned long lastCardReadTime = 0;
unsigned long lastTapTime = 0;
unsigned long feedbackStartTime = 0;
unsigned long responseTimeoutStart = 0;
unsigned long lastHeartbeatTime = 0;
unsigned long reconnectAttemptTime = 0;

String lastCardUid = "";
String pendingCardUid = "";
String pendingTapRef = "";

// ============================================
// Response data
// ============================================
String responseStatus = "";
String responseReason = "";
long responseBalance = 0;
String responseTransactionId = "";

// ============================================
// Setup
// ============================================
void setup() {
  Serial.begin(SERIAL_BAUD);
  while (!Serial) { ; }  // Wait for serial (Leonardo/USB)

  // LEDs
  pinMode(LED_GREEN_PIN, OUTPUT);
  pinMode(LED_RED_PIN, OUTPUT);
  pinMode(LED_POWER_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);

  // Startup sequence
  startupSequence();

  // SPI + RFID
  SPI.begin();
  rfid.PCD_Init();
  delay(100);

  // Check RFID reader
  byte v = rfid.PCD_ReadRegister(rfid.VersionReg);
  if (v == 0x00 || v == 0xFF) {
    errorBlink();
  }

  // Ready
  digitalWrite(LED_POWER_PIN, HIGH);
  currentState = STATE_IDLE;
}

// ============================================
// Main Loop (non-blocking)
// ============================================
void loop() {
  unsigned long now = millis();

  switch (currentState) {
    case STATE_IDLE:
      pollRFID(now);
      sendHeartbeat(now);
      break;

    case STATE_CARD_DETECTED:
      sendTapRequest();
      currentState = STATE_WAITING_RESPONSE;
      responseTimeoutStart = now;
      break;

    case STATE_WAITING_RESPONSE:
      checkSerialResponse(now);
      if (now - responseTimeoutStart > SERIAL_TIMEOUT) {
        currentState = STATE_ERROR;
        feedbackStartTime = now;
      }
      break;

    case STATE_FEEDBACK_APPROVED:
      showApproved(now);
      break;

    case STATE_FEEDBACK_DECLINED:
      showDeclined(now);
      break;

    case STATE_ERROR:
      showError(now);
      break;
  }
}

// ============================================
// RFID Polling
// ============================================
void pollRFID(unsigned long now) {
  if (now - lastCardReadTime < CARD_READ_INTERVAL) return;
  lastCardReadTime = now;

  if (!rfid.PICC_IsNewCardPresent()) return;
  if (!rfid.PICC_ReadCardSerial()) return;

  // Read card UID
  String uid = "";
  for (byte i = 0; i < rfid.uid.size; i++) {
    if (rfid.uid.uidByte[i] < 0x10) uid += "0";
    uid += String(rfid.uid.uidByte[i], HEX);
  }
  uid.toUpperCase();

  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();

  // Duplicate tap check
  if (uid == lastCardUid && (now - lastTapTime < TAP_COOLDOWN_MS)) {
    return;  // Too soon for same card
  }

  // Generate tap reference
  pendingCardUid = uid;
  pendingTapRef = generateTapRef(uid, now);
  lastCardUid = uid;
  lastTapTime = now;

  currentState = STATE_CARD_DETECTED;
}

// ============================================
// Serial Communication
// ============================================
void sendTapRequest() {
  StaticJsonDocument<256> doc;
  doc["type"] = MSG_TYPE_CARD_TAP;
  doc["card_uid"] = pendingCardUid;
  doc["terminal_id"] = TERMINAL_ID;
  doc["timestamp"] = millis() / 1000;
  doc["tap_ref"] = pendingTapRef;

  String output;
  serializeJson(doc, output);
  Serial.println(output);  // Newline-delimited
}

void sendHeartbeat(unsigned long now) {
  if (now - lastHeartbeatTime < 30000) return;  // Every 30s
  lastHeartbeatTime = now;

  StaticJsonDocument<128> doc;
  doc["type"] = MSG_TYPE_HEARTBEAT;
  doc["terminal_id"] = TERMINAL_ID;
  doc["uptime"] = now / 1000;

  String output;
  serializeJson(doc, output);
  Serial.println(output);
}

void checkSerialResponse(unsigned long now) {
  if (!Serial.available()) return;

  String line = Serial.readStringUntil('\n');
  line.trim();
  if (line.length() == 0) return;

  StaticJsonDocument<512> doc;
  DeserializationError err = deserializeJson(doc, line);
  if (err) return;  // Malformed — ignore

  String msgType = doc["type"] | "";
  if (msgType != MSG_TYPE_RESPONSE) return;

  responseStatus = doc["status"] | "UNKNOWN";
  responseReason = doc["reason"] | "";
  responseBalance = doc["balance"] | 0;
  responseTransactionId = doc["transaction_id"] | "";

  feedbackStartTime = now;

  if (responseStatus == "APPROVED") {
    currentState = STATE_FEEDBACK_APPROVED;
  } else {
    currentState = STATE_FEEDBACK_DECLINED;
  }
}

// ============================================
// Feedback: LEDs + Buzzer (non-blocking)
// ============================================
void showApproved(unsigned long now) {
  if (now - feedbackStartTime < LED_DURATION_MS) {
    digitalWrite(LED_GREEN_PIN, HIGH);
    if (now - feedbackStartTime < BUZZ_APPROVE_MS) {
      digitalWrite(BUZZER_PIN, HIGH);
    } else {
      digitalWrite(BUZZER_PIN, LOW);
    }
  } else {
    digitalWrite(LED_GREEN_PIN, LOW);
    digitalWrite(BUZZER_PIN, LOW);
    currentState = STATE_IDLE;
  }
}

void showDeclined(unsigned long now) {
  if (now - feedbackStartTime < LED_DURATION_MS) {
    digitalWrite(LED_RED_PIN, HIGH);
    if (now - feedbackStartTime < BUZZ_DECLINE_MS) {
      digitalWrite(BUZZER_PIN, HIGH);
    } else {
      digitalWrite(BUZZER_PIN, LOW);
    }
  } else {
    digitalWrite(LED_RED_PIN, LOW);
    digitalWrite(BUZZER_PIN, LOW);
    currentState = STATE_IDLE;
  }
}

void showError(unsigned long now) {
  if (now - feedbackStartTime < 2000) {
    // Rapid red blink
    digitalWrite(LED_RED_PIN, (now / 100) % 2);
    digitalWrite(BUZZER_PIN, LOW);
  } else {
    digitalWrite(LED_RED_PIN, LOW);
    currentState = STATE_IDLE;
  }
}

// ============================================
// Helpers
// ============================================
String generateTapRef(String uid, unsigned long now) {
  return "TAP-" + uid + "-" + String(now);
}

void startupSequence() {
  // All LEDs on
  digitalWrite(LED_GREEN_PIN, HIGH);
  digitalWrite(LED_RED_PIN, HIGH);
  digitalWrite(LED_POWER_PIN, HIGH);
  tone(BUZZER_PIN, 1000, 200);
  delay(500);
  digitalWrite(LED_GREEN_PIN, LOW);
  digitalWrite(LED_RED_PIN, LOW);
  digitalWrite(LED_POWER_PIN, LOW);
  noTone(BUZZER_PIN);
}

void errorBlink() {
  for (int i = 0; i < 5; i++) {
    digitalWrite(LED_RED_PIN, HIGH);
    delay(100);
    digitalWrite(LED_RED_PIN, LOW);
    delay(100);
  }
}

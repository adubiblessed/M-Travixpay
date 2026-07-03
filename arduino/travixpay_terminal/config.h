#ifndef CONFIG_H
#define CONFIG_H

// ============================================
// TravixPay Terminal - Arduino Configuration
// ============================================

// MFRC522 RFID Reader Pins (SPI)
#define RFID_SS_PIN    10    // SDA/SS
#define RFID_RST_PIN   9     // RST
// MOSI = 11, MISO = 12, SCK = 13 (hardware SPI)

// LED Indicators
#define LED_GREEN_PIN  4     // Approved tap
#define LED_RED_PIN    5     // Declined tap
#define LED_POWER_PIN  6     // System power/ready

// Buzzer
#define BUZZER_PIN     7     // Piezo buzzer

// Serial Configuration
#define SERIAL_BAUD    9600
#define SERIAL_TIMEOUT 1000  // ms - response timeout

// Timing Constants
#define TAP_COOLDOWN_MS    3000   // Min ms between same card taps
#define LED_DURATION_MS    2000   // LED on duration after tap
#define BUZZ_APPROVE_MS    100    // Short beep for approved
#define BUZZ_DECLINE_MS    500    // Long beep for declined
#define RECONNECT_DELAY_MS 5000   // Serial reconnect delay
#define CARD_READ_INTERVAL 50     // ms between RFID polls

// Terminal ID (set per device)
#define TERMINAL_ID    "TRM-DEFAULT"

// Protocol Constants
#define MSG_TYPE_CARD_TAP    "CARD_TAP"
#define MSG_TYPE_RESPONSE    "RESPONSE"
#define MSG_TYPE_HEARTBEAT   "HEARTBEAT"
#define MSG_TYPE_STATUS      "STATUS"

#endif

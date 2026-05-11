/**
 * Lebanon Kolgrill & Pizzeria
 * Google Apps Script — Orders + Logs sheet setup
 *
 * HOW TO USE (Orders):
 * 1. Open your Google Sheet
 * 2. Click Extensions > Apps Script
 * 3. Delete any existing code
 * 4. Paste this entire script
 * 5. Click Save, then Run > setupOrdersSheet
 * 6. Accept permissions when prompted
 *
 * Logs tab (ElevenLabs post-call webhook rows):
 * — Run > setupLogsSheet (does not delete other tabs; safe alongside Orders)
 */

function setupOrdersSheet() {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
  
    // ── 1. Get or create the "Orders" tab ──────────────────────────────────────
    let sheet = ss.getSheetByName("Orders");
    if (!sheet) {
      sheet = ss.insertSheet("Orders");
    } else {
      sheet.clear(); // wipe existing content if re-running
    }
  
    // Delete all other sheets (optional — comment out if you want to keep them)
    ss.getSheets().forEach(s => {
      if (s.getName() !== "Orders") {
        ss.deleteSheet(s);
      }
    });
  
    // ── 2. Write header row A1:N1 ─────────────────────────────────────────────
    const headers = [
      "order_id",       // A
      "customer_name",  // B
      "customer_phone", // C
      "order_status",   // D
      "created_at",     // E
      "order_type",     // F
      "order_items",    // G
      "party_size",     // H
      "dine_in_time",   // I
      "pickup_time",    // J
      "total",          // K
      "notes",          // L
      "source",         // M
      "cancellation_reason" // N
    ];
  
    const headerRange = sheet.getRange(1, 1, 1, headers.length);
    headerRange.setValues([headers]);
  
    // Style the header row
    headerRange.setFontWeight("bold");
    headerRange.setBackground("#1a1a2e");
    headerRange.setFontColor("#ffffff");
    headerRange.setFontSize(11);
    headerRange.setHorizontalAlignment("center");
  
    // ── 3. Freeze header row ──────────────────────────────────────────────────
    sheet.setFrozenRows(1);
  
    // ── 4. Column widths ──────────────────────────────────────────────────────
    sheet.setColumnWidth(1, 160);  // A: order_id
    sheet.setColumnWidth(2, 160);  // B: customer_name
    sheet.setColumnWidth(3, 160);  // C: customer_phone
    sheet.setColumnWidth(4, 130);  // D: order_status
    sheet.setColumnWidth(5, 180);  // E: created_at
    sheet.setColumnWidth(6, 120);  // F: order_type
    sheet.setColumnWidth(7, 320);  // G: order_items (wider for readability)
    sheet.setColumnWidth(8, 100);  // H: party_size
    sheet.setColumnWidth(9, 180);  // I: dine_in_time
    sheet.setColumnWidth(10, 180); // J: pickup_time
    sheet.setColumnWidth(11, 110); // K: total
    sheet.setColumnWidth(12, 220); // L: notes
    sheet.setColumnWidth(13, 120); // M: source
    sheet.setColumnWidth(14, 260); // N: cancellation_reason
  
    // ── 5. Plain text format for phone, dates ─────────────────────────────────
    // customer_phone (C) — prevent Google from mangling + and leading zeros
    sheet.getRange("C2:C1000").setNumberFormat("@");
  
    // created_at, dine_in_time, pickup_time (E, I, J) — store ISO strings as-is
    sheet.getRange("E2:E1000").setNumberFormat("@");
    sheet.getRange("I2:I1000").setNumberFormat("@");
    sheet.getRange("J2:J1000").setNumberFormat("@");

    // total (K) — numeric currency
    sheet.getRange("K2:K1000").setNumberFormat("0.00");
  
    // ── 6. Dropdown validation for order_status (D) ───────────────────────────
    const statusValues = [
      "submitted",
      "confirmed",
      "preparing",
      "ready",
      "completed",
      "cancelled"
    ];
  
    const statusRule = SpreadsheetApp.newDataValidation()
      .requireValueInList(statusValues, true)  // true = show dropdown arrow
      .setAllowInvalid(true)                   // won't block invalid writes from backend
      .setHelpText("Select order status")
      .build();
  
    sheet.getRange("D2:D1000").setDataValidation(statusRule);
  
    // ── 7. Alternating row colors for readability ─────────────────────────────
    // Light alternating bands — applied to data rows only
    const dataRange = sheet.getRange(2, 1, 998, headers.length);
    dataRange.applyRowBanding(SpreadsheetApp.BandingTheme.LIGHT_GREY, false, false);
  
    // ── 8. Wrap text for order_items column ───────────────────────────────────
    sheet.getRange("G2:G1000").setWrap(true);
  
    // ── 9. Center-align specific columns ─────────────────────────────────────
    sheet.getRange("D2:D1000").setHorizontalAlignment("center"); // order_status
    sheet.getRange("F2:F1000").setHorizontalAlignment("center"); // order_type
    sheet.getRange("H2:H1000").setHorizontalAlignment("center"); // party_size
    sheet.getRange("K2:K1000").setHorizontalAlignment("center"); // total
    sheet.getRange("M2:M1000").setHorizontalAlignment("center"); // source
    sheet.getRange("N2:N1000").setWrap(true); // cancellation_reason
  
    // ── 10. Done ──────────────────────────────────────────────────────────────
    SpreadsheetApp.getUi().alert(
      "✅ Orders sheet created successfully!\n\n" +
      "Headers: A (order_id) → M (source)\n" +
      "Dropdown set on column D (order_status)\n" +
      "Plain text set on columns C, E, I, J\n" +
      "Total column is K\n\n" +
      "You're ready to connect your backend."
    );
  }

function setupLogsSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  // ── 1. Get or create the "Logs" tab (do not delete other sheets) ───────────
  let sheet = ss.getSheetByName("Logs");
  if (!sheet) {
    sheet = ss.insertSheet("Logs");
  } else {
    sheet.clear();
  }

  // Keep in sync with LOGS_HEADERS in app/services/logs_repo.py
  const headers = [
    "logged_at",
    "webhook_type",
    "conversation_id",
    "agent_id",
    "status",
    "duration_secs",
    "caller_number",
    "has_audio",
    "has_user_audio",
    "has_response_audio",
    "transcript_text",
    "payload_json"
  ];

  const headerRange = sheet.getRange(1, 1, 1, headers.length);
  headerRange.setValues([headers]);

  headerRange.setFontWeight("bold");
  headerRange.setBackground("#1a1a2e");
  headerRange.setFontColor("#ffffff");
  headerRange.setFontSize(11);
  headerRange.setHorizontalAlignment("center");

  sheet.setFrozenRows(1);

  sheet.setColumnWidth(1, 200);   // logged_at
  sheet.setColumnWidth(2, 180);   // webhook_type
  sheet.setColumnWidth(3, 220);   // conversation_id
  sheet.setColumnWidth(4, 200);   // agent_id
  sheet.setColumnWidth(5, 120);  // status
  sheet.setColumnWidth(6, 110);  // duration_secs
  sheet.setColumnWidth(7, 160);  // caller_number
  sheet.setColumnWidth(8, 90);   // has_audio
  sheet.setColumnWidth(9, 120);  // has_user_audio
  sheet.setColumnWidth(10, 140); // has_response_audio
  sheet.setColumnWidth(11, 400); // transcript_text
  sheet.setColumnWidth(12, 500); // payload_json

  sheet.getRange("A2:A1000").setNumberFormat("@");
  sheet.getRange("C2:C1000").setNumberFormat("@");
  sheet.getRange("G2:G1000").setNumberFormat("@");

  sheet.getRange("K2:L1000").setWrap(true);

  const dataRange = sheet.getRange(2, 1, 998, headers.length);
  dataRange.applyRowBanding(SpreadsheetApp.BandingTheme.LIGHT_GREY, false, false);

  sheet.getRange("F2:F1000").setHorizontalAlignment("center"); // duration_secs
  sheet.getRange("H2:J1000").setHorizontalAlignment("center");   // audio flags

  SpreadsheetApp.getUi().alert(
    "✅ Logs sheet created successfully.\n\n" +
      "Headers match the backend LOGS_HEADERS list.\n" +
      "Point ElevenLabs post-call webhook to: POST /webhooks/elevenlabs/post-call"
  );
}
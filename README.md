## Restaurant Inbound (FastAPI + Google Sheets)

Backend API for an ElevenLabs inbound-call receptionist. Stores orders in **Google Sheets** via the **Google Sheets API** (service account).

### What you get
- **`POST /submit-order`**: append a new order row to **Google Sheets** and **Supabase** (`orders` table)
- **`GET /check-order-status`**: list matching orders for a caller within a lookahead window (handles midnight crossover) — reads from Sheets only
- **`POST /update-order`**: update a caller's active order — Sheets + Supabase
- **`POST /cancel-order`**: cancel by `order_id` or cancel the most recent active order for caller — Sheets + Supabase
- **`POST /webhooks/elevenlabs/post-call`**: ElevenLabs post-call webhook — verifies signature and appends one row to **Logs** (Sheets + Supabase `call_logs`)

### 1) Create the Google Sheet
Option A (recommended): run the included Apps Script to create the sheet with correct headers.

- File: [`scripts/appscript.js`](scripts/appscript.js)
- Steps:
  - Open your Google Sheet
  - Click Extensions -> Apps Script
  - Paste the script and run `setupOrdersSheet`
  - For ElevenLabs call logs: run `setupLogsSheet` (adds a **Logs** tab; does not remove **Orders**)

Option B: manually create a sheet/tab named `Orders` with header row 1 exactly:

`order_id, customer_name, customer_phone, order_status, created_at, order_type, order_items, party_size, dine_in_time, pickup_time, notes, source`

### 2) Service account setup (Sheets API)
1. Create a Google Cloud project and enable **Google Sheets API**.
2. Create a **Service Account** and generate a JSON key.
3. Share your spreadsheet with the service account email (Editor access).

### 3) Configure environment variables
- Copy `.env.example` → `.env` (if present), or create `.env` beside the project
- Fill:
  - `GOOGLE_SHEET_ID`
  - `GOOGLE_SERVICE_ACCOUNT_JSON` (either paste the full JSON contents, or set it to the path of the JSON key file)
  - `RESTAURANT_TIMEZONE` (IANA timezone, e.g. `Asia/Karachi`)
  - `GOOGLE_SHEET_LOGS_TAB` (optional, default `Logs`) — tab for ElevenLabs webhook rows
  - `ELEVENLABS_WEBHOOK_SECRET` — required; signing secret from ElevenLabs webhook settings (HMAC verification)
  - `SUPABASE_URL` — project URL (Settings → API)
  - `SUPABASE_SERVICE_ROLE_KEY` — **service_role** secret (server-side only; never expose to clients)
  - `SUPABASE_ORDERS_TABLE` / `SUPABASE_LOGS_TABLE` — optional; default table names `orders` and `call_logs`

### ElevenLabs post-call webhook
1. Create the **Logs** tab (run `setupLogsSheet` in Apps Script, or let the first append create the header row automatically).
2. In ElevenLabs, set the post-call webhook URL to your public base URL plus **`/webhooks/elevenlabs/post-call`** (HTTPS). Example: `https://your-app.herokuapp.com/webhooks/elevenlabs/post-call`
3. Paste the same signing secret into `ELEVENLABS_WEBHOOK_SECRET` in your deployment environment.

### 4) Run locally

```bash
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open docs at `http://localhost:8000/docs`.

### Phone numbers
- Stored value: leading spaces trimmed and a leading `+` removed (digits and any other characters you pass are kept as-is).
- Lookup and “same caller” checks compare the **last 10 digits** only, so `+46701234567`, `46701234567`, and `0701234567` match when their digit tails align.

### API usage (examples)

#### Submit order

```bash
curl -X POST "http://localhost:8000/submit-order" ^
  -H "Content-Type: application/json" ^
  -d "{\"customer_name\":\"Ali\",\"customer_phone\":\"+923001234567\",\"order_type\":\"takeaway\",\"order_items\":[{\"name\":\"Burger\",\"quantity\":2}],\"pickup_time\":\"2026-05-07T23:30:00+05:00\"}"
```

#### Check order status (lists orders in the lookahead window)

Returns full order details per match (`customer_name`, `order_items`, times, `notes`, etc.), not status alone.

```bash
curl "http://localhost:8000/check-order-status?param_caller_number=%2B923001234567"
```

#### Update order

Send only the fields that changed. If changing `order_items`, send the complete revised item list.

```bash
curl -X POST "http://localhost:8000/update-order" ^
  -H "Content-Type: application/json" ^
  -d "{\"caller_number\":\"+923001234567\",\"order_id\":\"01HX...\",\"order_items\":[{\"name\":\"Burger\",\"quantity\":3}],\"notes\":\"Customer added one extra burger\"}"
```

#### Cancel order (by caller, cancels most recent active)

```bash
curl -X POST "http://localhost:8000/cancel-order" ^
  -H "Content-Type: application/json" ^
  -d "{\"caller_number\":\"+923001234567\",\"reason\":\"Customer changed mind\"}"
```


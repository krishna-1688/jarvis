/**
 * server.js — Jarvis WhatsApp background service.
 */

require("dotenv").config({ path: require("path").join(__dirname, "..", "backend", ".env") });

const express = require("express");
const qrcode = require("qrcode-terminal");
const { Client, LocalAuth } = require("whatsapp-web.js");

const PORT = 4500;
const HOST = "127.0.0.1";
const app = express();

// This service can read contacts and send messages as the user, and only
// the Jarvis backend on this laptop should ever call it. So it listens on
// 127.0.0.1 only (not the Wi-Fi network), and turns away requests made
// from a browser (they carry Origin / Sec-Fetch-Site, which web pages
// can't remove) or addressed to another host name (DNS rebinding).
app.use((req, res, next) => {
  const host = (req.headers.host || "").replace(/:\d+$/, "").toLowerCase();
  if (!["127.0.0.1", "localhost"].includes(host) || req.headers.origin || req.headers["sec-fetch-site"]) {
    return res.status(403).json({ error: "forbidden" });
  }
  next();
});
app.use(express.json());

// ── UPI/bank auto-parse (Phase 5.2) ───────
// Only messages from a sender whose WhatsApp display name matches one of
// these patterns get forwarded to the Python backend — never arbitrary
// messages. These are placeholder patterns (no real UPI notifications were
// available to build from) — replace with whatever your bank/UPI app's
// actual WhatsApp display name is for better accuracy.
const JARVIS_BACKEND_URL = process.env.JARVIS_BACKEND_URL || "http://127.0.0.1:8000";
const UPI_SENDER_PATTERNS = (
  process.env.UPI_SENDER_PATTERNS || "HDFC Bank,SBI,ICICI Bank,Axis Bank,Paytm,PhonePe,Google Pay"
)
  .split(",")
  .map((s) => s.trim().toLowerCase())
  .filter(Boolean);

let isReady = false;
let lastQR = null;
let contactsCache = [];

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: "./.wwebjs_auth" }),
  puppeteer: {
    headless: true,
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-dev-shm-usage",
      "--disable-accelerated-2d-canvas",
      "--no-first-run",
      "--no-zygote",
      "--disable-gpu",
    ],
  },
});

client.on("qr", (qr) => {
  lastQR = qr;
  console.log("\n📱 Scan this QR code with WhatsApp (Linked Devices):\n");
  qrcode.generate(qr, { small: true });
});

client.on("ready", async () => {
  isReady = true;
  lastQR = null;
  console.log("✅ WhatsApp connected and ready.");
  await refreshContacts();
});

client.on("disconnected", (reason) => {
  isReady = false;
  console.log("⚠️  WhatsApp disconnected:", reason);
  // Auto-reinitialize after disconnect
  setTimeout(() => {
    console.log("🔄 Reinitializing WhatsApp client...");
    client.initialize().catch(e => console.log("Reinit error:", e.message));
  }, 3000);
});

client.on("auth_failure", (msg) => {
  isReady = false;
  console.log("❌ Auth failure:", msg);
});

client.on("message", async (msg) => {
  if (!UPI_SENDER_PATTERNS.length) return;
  try {
    const contact = await msg.getContact();
    const displayName = (contact.name || contact.pushname || contact.shortName || "").toLowerCase();
    if (!displayName || !UPI_SENDER_PATTERNS.some((p) => displayName.includes(p))) return;

    const resp = await fetch(`${JARVIS_BACKEND_URL}/expense/ingest_upi`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sender: contact.name || contact.pushname || contact.shortName,
        message: msg.body,
        received_at: new Date((msg.timestamp || Date.now() / 1000) * 1000).toISOString(),
      }),
    });
    if (resp.ok) {
      const data = await resp.json();
      if (data.logged) console.log(`💸 Auto-logged expense: Rs.${data.amount} (${data.category})`);
    }
  } catch (e) {
    console.log("UPI ingest check error:", e.message);
  }
});

// Re-cache contacts whenever WhatsApp is reloaded internally
client.on("ready", async () => {
  await refreshContacts();
});

async function refreshContacts() {
  try {
    const contacts = await client.getContacts();
    contactsCache = contacts
      .filter((c) => c.isMyContact && c.name)
      .map((c) => ({
        name: c.name || c.pushname || c.number,
        number: c.number,
        id: c.id._serialized,
      }));
    console.log(`📇 Cached ${contactsCache.length} contacts.`);
  } catch (e) {
    console.log("Contact refresh error:", e.message);
  }
}

client.initialize();

// ══════════════════════════════════════════
//   SAFE SEND — retries on detached frame
// ══════════════════════════════════════════

/**
 * Wraps client.sendMessage with retry logic.
 * "Detached Frame" errors happen when WhatsApp Web's internal Puppeteer
 * page reloads (session refresh/reconnect) between API calls.
 * We wait briefly and retry once — the page is usually ready again by then.
 */
async function safeSendMessage(chatId, message, retries = 2) {
  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      await client.sendMessage(chatId, message);
      return { ok: true };
    } catch (e) {
      const isFrameError =
        e.message && (
          e.message.includes("detached Frame") ||
          e.message.includes("Execution context was destroyed") ||
          e.message.includes("Session closed") ||
          e.message.includes("Target closed")
        );

      if (isFrameError && attempt < retries) {
        console.log(`⚠️  Frame detached (attempt ${attempt}), retrying in 2s...`);
        await new Promise(r => setTimeout(r, 2000));

        // Force WhatsApp to reinitialize its page context
        try {
          await client.pupPage.reload({ waitUntil: "networkidle2", timeout: 10000 });
          await new Promise(r => setTimeout(r, 1500));
        } catch (reloadErr) {
          console.log("Page reload failed, continuing with retry:", reloadErr.message);
        }
        continue;
      }

      // Non-frame error or out of retries
      return { ok: false, error: e.message };
    }
  }
  return { ok: false, error: "Max retries exceeded" };
}

// ══════════════════════════════════════════
//   FUZZY MATCHING
// ══════════════════════════════════════════

function cleanForMatching(str) {
  return str
    .replace(/[\u{1F300}-\u{1FAFF}]/gu, "")
    .replace(/[\u{2600}-\u{27BF}]/gu, "")
    .replace(/[\u{2190}-\u{21FF}]/gu, "")
    .replace(/[\u{2B00}-\u{2BFF}]/gu, "")
    .replace(/[\u{FE00}-\u{FE0F}]/gu, "")
    .replace(/[\u200d]/gu, "")
    .replace(/[^a-zA-Z\s']/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function levenshtein(a, b) {
  const m = a.length, n = b.length;
  const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = 0; i <= m; i++) dp[i][0] = i;
  for (let j = 0; j <= n; j++) dp[0][j] = j;
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (a[i - 1] === b[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1];
      } else {
        dp[i][j] = 1 + Math.min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1]);
      }
    }
  }
  return dp[m][n];
}

function similarity(a, b) {
  a = a.toLowerCase().trim();
  b = b.toLowerCase().trim();
  if (a === b) return 1;
  const maxLen = Math.max(a.length, b.length);
  if (maxLen === 0) return 1;
  return 1 - levenshtein(a, b) / maxLen;
}

function fuzzyFindContacts(query, topN = 3) {
  const q = cleanForMatching(query).toLowerCase().trim();

  const seen = new Set();
  const uniqueContacts = contactsCache.filter((c) => {
    const key = c.id || `${c.name}|${c.number}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  const scored = uniqueContacts.map((c) => {
    const cleanName = cleanForMatching(c.name).toLowerCase();
    const words = cleanName.split(/\s+/).filter(Boolean);

    const fullNameScore = similarity(q, cleanName);
    const bestWordScore = words.length
      ? Math.max(...words.map((w) => similarity(q, w)))
      : 0;

    const isExactFullMatch = fullNameScore >= 0.95;

    let score;
    if (isExactFullMatch) {
      score = 1.0;
    } else {
      const coverage = words.length ? 1 / words.length : 1;
      const discountedWordScore = bestWordScore * (0.55 + 0.45 * coverage);
      const substringBoost = cleanName.includes(q) && cleanName !== q ? 0.1 : 0;
      score = Math.min(0.94, Math.max(fullNameScore, discountedWordScore) + substringBoost);
    }

    return { ...c, score };
  });

  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, topN).filter((c) => c.score >= 0.45);
}

// ══════════════════════════════════════════
//   HTTP API
// ══════════════════════════════════════════

app.get("/status", (req, res) => {
  res.json({ ready: isReady, qr: lastQR, contacts_loaded: contactsCache.length });
});

app.get("/contacts", (req, res) => {
  res.json({ contacts: contactsCache });
});

app.post("/refresh-contacts", async (req, res) => {
  if (!isReady) return res.status(503).json({ error: "WhatsApp not ready yet" });
  await refreshContacts();
  res.json({ ok: true, count: contactsCache.length });
});

app.post("/find-contact", (req, res) => {
  const { query } = req.body;
  if (!query) return res.status(400).json({ error: "'query' is required" });
  const matches = fuzzyFindContacts(query, 3);
  res.json({ matches });
});

/**
 * POST /send-confirmed
 * Uses safeSendMessage — retries on detached frame errors.
 */
app.post("/send-confirmed", async (req, res) => {
  if (!isReady) {
    return res.status(503).json({ error: "WhatsApp not ready. Scan QR first." });
  }
  const { contact_id, message } = req.body;
  if (!contact_id || !message) {
    return res.status(400).json({ error: "Both 'contact_id' and 'message' are required." });
  }

  const result = await safeSendMessage(contact_id, message);
  if (result.ok) {
    res.json({ ok: true });
  } else {
    console.log("Send error:", result.error);
    res.status(500).json({ error: result.error });
  }
});

/**
 * POST /send
 * Legacy direct-send — also uses safeSendMessage now.
 */
app.post("/send", async (req, res) => {
  if (!isReady) {
    return res.status(503).json({ error: "WhatsApp not ready. Scan QR first." });
  }

  const { to, message } = req.body;
  if (!to || !message) {
    return res.status(400).json({ error: "Both 'to' and 'message' are required." });
  }

  try {
    let chatId;
    let resolvedName = to;

    const cleanedNumber = to.replace(/[^\d+]/g, "");
    const isPhoneNumber = /^\+?\d{8,15}$/.test(cleanedNumber);

    if (isPhoneNumber) {
      const digitsOnly = cleanedNumber.replace("+", "");
      chatId = `${digitsOnly}@c.us`;
    } else {
      const exact = contactsCache.find(
        (c) => cleanForMatching(c.name).toLowerCase() === cleanForMatching(to).toLowerCase()
      );
      if (!exact) {
        return res.status(404).json({
          error: `No exact contact match for '${to}'. Use /find-contact for fuzzy search.`,
        });
      }
      chatId = exact.id;
      resolvedName = exact.name;
    }

    const result = await safeSendMessage(chatId, message);
    if (result.ok) {
      res.json({ ok: true, sent_to: resolvedName, message });
    } else {
      res.status(500).json({ error: result.error });
    }
  } catch (e) {
    console.log("Send error:", e.message);
    res.status(500).json({ error: e.message });
  }
});

app.listen(PORT, HOST, () => {
  console.log(`\n🚀 Jarvis WhatsApp service running on http://${HOST}:${PORT}`);
  console.log("   Waiting for WhatsApp client to initialize...\n");
});
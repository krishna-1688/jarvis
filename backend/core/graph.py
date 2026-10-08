"""
core/graph.py — Jarvis's associative memory.

Every turn becomes an EVENT linked to the ENTITIES it mentions (courses,
exams, people, tasks, topics, remembered facts). Entities that come up
together get a weighted link that strengthens each time they co-occur
(Hebbian: w += rate * (1 - w)) and fades with time since it was last
reinforced (half-life decay, computed at read time — nothing runs in the
background to age it). Recall spreads activation outward from what the
question mentions, two hops through the strongest fresh links, and
returns the few memories most connected to it.

Design constraints (all measured in tests, see the commit that added this):
  - no AI calls anywhere in here: entity detection reuses the router's
    entities, the course resolver and the feature result data;
  - writes happen on one background thread, so a reply never waits on
    memory, and SQLite never sees two writers;
  - its own database file, so backups/repair can't touch jarvis.db;
  - any failure here degrades to "no extra memory context", never to a
    broken reply (callers catch everything).
"""

import json
import math
import os
import queue
import re
import shutil
import sqlite3
import threading
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# JARVIS_MEMORY_DB lets tests point at a throwaway file instead of real memory.
DB_PATH = os.environ.get("JARVIS_MEMORY_DB") or os.path.join(BASE_DIR, "data", "database", "memory_graph.db")
BACKUP_DIR = os.environ.get("JARVIS_MEMORY_BACKUP_DIR") or os.path.join(BASE_DIR, "data", "backups")
BACKUPS_KEPT = 7
SCHEMA_VERSION = 1

# ── Learning dynamics ─────────────────────────────────────────
LEARN_RATE = 0.25            # co-occurrence strengthening per turn
CONTEXT_LEARN_RATE = 0.12    # weaker link for "carried over from last turn"
EDGE_HALF_LIFE_DAYS = 21     # an unused link loses half its strength in 3 weeks
EVENT_HALF_LIFE_DAYS = 10    # recency weighting when picking past events
CONTEXT_WINDOW_S = 180       # turns this close together are one conversation
PRUNE_BELOW = 0.02           # effective weight under this is forgotten
MAX_DEGREE = 150             # strongest links kept per node

# ── Recall budget ─────────────────────────────────────────────
HOP1_FANOUT = 12
HOP2_SOURCES = 6
HOP2_DECAY = 0.5
MAX_EVENTS_IN_CONTEXT = 4
MAX_FACTS_IN_CONTEXT = 12
MAX_CONTEXT_CHARS = 850    # sent on every chat call (~210 tokens max) — keep it lean

_STOPWORDS = {
    "the", "and", "for", "are", "was", "were", "what", "whats", "how", "why", "who", "when", "where",
    "which", "you", "your", "yours", "me", "my", "mine", "can", "could", "would", "should", "will",
    "just", "about", "this", "that", "these", "those", "with", "from", "have", "has", "had", "does",
    "did", "not", "any", "tell", "give", "please", "jarvis", "boss", "its", "is", "am", "be", "been",
    "there", "then", "some", "into", "also", "like", "get", "got", "want", "need", "know", "make",
    "let", "one", "all", "explain", "example", "examples", "difference", "between", "simple", "terms",
    "briefly", "quick", "quickly", "really", "thing", "things", "something", "anything", "much",
    "many", "more", "most", "very", "okay", "yeah", "yes", "sure", "thanks", "thank", "today",
    "tomorrow", "yesterday", "now", "again", "still", "even", "only", "use", "using", "used", "way",
    "ways", "time", "times", "good", "best", "better", "help", "idea", "ideas", "tips", "tip",
    "mean", "means", "work", "works", "doing", "done", "going", "gonna", "wanna", "they", "them",
    "their", "our", "his", "her", "him", "she", "him", "it's", "i'm", "don't", "i've", "i'll",
    "said", "say", "says", "tell", "told", "show", "shows", "find", "look", "see", "remember",
    "prepare", "preparing", "preparation", "keep", "keeping", "teach", "teaches", "teaching", "learn",
    "learning", "study", "studying", "understand", "start", "started", "try", "trying", "feel",
    "feeling", "think", "thinking", "lower", "higher", "other", "others", "first", "last", "next",
    "struggle", "struggling", "wrong", "right", "should", "shall", "must", "might", "able", "than",
    # everyday filler that small talk kept turning into topics
    "back", "down", "little", "name", "looking", "glad", "love", "whole", "well", "here", "come",
    "coming", "call", "went", "take", "took", "put", "keep", "said", "sound", "sounds", "maybe",
    "actually", "basically", "literally", "always", "never", "every", "each", "same", "such",
    "while", "after", "before", "around", "through", "over", "under", "didn", "doesn", "isn",
}


# ══════════════════════════════════════════
#   STORAGE
# ══════════════════════════════════════════

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS nodes (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    label TEXT NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0,
    mentions INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    last_seen REAL NOT NULL,
    UNIQUE (kind, key)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    text TEXT NOT NULL,
    reply TEXT NOT NULL DEFAULT '',
    intent TEXT NOT NULL DEFAULT '',
    via TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS event_links (
    node_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL,
    PRIMARY KEY (node_id, event_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS event_links_event ON event_links (event_id);
CREATE TABLE IF NOT EXISTS edges (
    a INTEGER NOT NULL,
    b INTEGER NOT NULL,
    weight REAL NOT NULL,
    updated REAL NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (a, b)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS edges_b ON edges (b);
CREATE INDEX IF NOT EXISTS edges_a_weight ON edges (a, weight DESC);
CREATE INDEX IF NOT EXISTS edges_b_weight ON edges (b, weight DESC);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    node_id INTEGER NOT NULL,
    attribute TEXT,
    text TEXT NOT NULL,
    created_at REAL NOT NULL,
    superseded_at REAL
);
CREATE INDEX IF NOT EXISTS facts_attr ON facts (attribute) WHERE superseded_at IS NULL;
CREATE TABLE IF NOT EXISTS habits (key TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0);
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(label, content='nodes', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
    INSERT INTO nodes_fts(rowid, label) VALUES (new.id, new.label);
END;
CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, label) VALUES ('delete', old.id, old.label);
END;
"""


def _connect(path: str = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")   # safe with WAL: survives crashes, not power cuts mid-fsync
    conn.execute("PRAGMA foreign_keys=OFF")
    return conn


def _now() -> float:
    return time.time()


def _decayed(weight: float, updated: float, now: float, half_life_days: float = EDGE_HALF_LIFE_DAYS) -> float:
    age_days = max(0.0, now - updated) / 86400.0
    return weight * math.pow(0.5, age_days / half_life_days)


def _content_words(text: str) -> list:
    words = []
    for w in re.findall(r"[a-z][a-z0-9+#'’]{3,}", (text or "").lower()):
        # Contractions ("that's", "didn't", "you're") are never topics —
        # plural-stripping used to turn "that's" into a node called "that'".
        if "'" in w or "’" in w or w in _STOPWORDS or len(w) < 4:
            continue
        if len(w) > 5 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 5 and w.endswith(("ches", "shes", "xes", "sses")):
            w = w[:-2]
        elif len(w) > 4 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
            w = w[:-1]
        if w not in words:
            words.append(w)
    return words


# ══════════════════════════════════════════
#   ENTITY EXTRACTION (no AI calls)
# ══════════════════════════════════════════

_FOLLOWUP_RE = re.compile(
    r"\b(?:it|its|it's|that|this|those|these|them|there|he|she|they|his|her|their|why|how come|"
    r"what about|and the|same|again|more|another)\b"
)

_FACT_RE = re.compile(r"^(?:my\s+)?(.{2,40}?)\s+(?:is|are|was|=)\s+(.+)$", re.IGNORECASE)


def _fact_attribute(fact_text: str) -> str | None:
    """'My birthday is 5 May' -> 'birthday', used to supersede old values."""
    m = _FACT_RE.match(fact_text.strip().rstrip("."))
    if not m:
        return None
    attr = re.sub(r"\s+", " ", m.group(1).lower()).strip()
    return attr if len(attr.split()) <= 5 else None


def extract_entities(text: str, intent: str = "", payload: dict | None = None,
                     data: dict | None = None) -> list:
    """[(kind, key, label), ...] for everything this turn is about."""
    payload = payload or {}
    data = data or {}
    found = []

    def add(kind, key, label):
        key = (key or "").strip().lower()
        if key and (kind, key) not in [(k, kk) for k, kk, _ in found]:
            found.append((kind, key, (label or key).strip()))

    try:
        from core.course_resolver import resolve_course_best
        course_hits = []
        if payload.get("course"):
            course_hits.append(resolve_course_best(str(payload["course"])))
        course_hits.append(resolve_course_best(text or ""))
        for hit in course_hits:
            if hit:
                add("course", hit["course_code"] or hit["course_name"], hit["course_name"])
                # A lab belongs with its theory course ("daa lab record" is
                # about DAA too) — link both so either one recalls it.
                name = hit["course_name"] or ""
                if re.search(r"\blab\b", name, re.IGNORECASE):
                    parent = resolve_course_best(re.sub(r"\s*\blab\b", "", name, flags=re.IGNORECASE).strip())
                    if parent and parent["course_code"] != hit["course_code"]:
                        add("course", parent["course_code"] or parent["course_name"], parent["course_name"])
    except Exception:
        pass

    exams = data.get("exams") if isinstance(data.get("exams"), list) else None
    if intent == "exams" and exams:
        e = exams[0]
        add("exam", f"{e.get('exam_type')}:{e.get('course_code')}", f"{e.get('exam_type')} {e.get('course_name')}")
        add("course", e.get("course_code"), e.get("course_name"))

    for key in ("sent_to", "candidate"):
        if isinstance(data.get(key), str):
            add("person", data[key], data[key])

    task = data.get("task")
    if isinstance(task, dict) and task.get("title"):
        add("task", task["title"], task["title"])
    elif intent in ("task_complete", "task_drop") and payload.get("query"):
        add("task", payload["query"], payload["query"])

    if intent in ("brain", "chat", "remember_fact") or not found:
        course_words = {w for _, _, label in found for w in label.lower().split()}
        for w in _content_words(text)[:4]:
            if w not in course_words:
                add("topic", w, w)
    return found


# ══════════════════════════════════════════
#   THE GRAPH
# ══════════════════════════════════════════

class MemoryGraph:
    def __init__(self, path: str = None, start_writer: bool = True):
        self.path = path or DB_PATH
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._ensure_healthy()
        conn = _connect(self.path)
        conn.executescript(_SCHEMA)
        conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        conn.commit()
        conn.close()
        self._read_local = threading.local()
        self._readers = []                  # every reader connection, so close() can release them all
        self._readers_lock = threading.Lock()
        self._queue = queue.Queue()
        self._last_turn = {"ts": 0.0, "node_ids": []}
        self._writer = None
        if start_writer:
            self._writer = threading.Thread(target=self._writer_loop, daemon=True, name="memory-graph-writer")
            self._writer.start()

    # ── health ────────────────────────────────────────────────
    def _ensure_healthy(self):
        """integrity_check at startup; a corrupt file is moved aside and the
        newest backup restored, so a bad disk write costs at most a day."""
        if not os.path.exists(self.path):
            return
        try:
            conn = _connect(self.path)
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            conn.close()
        except sqlite3.DatabaseError:
            ok = False
        if ok:
            return
        bad = f"{self.path}.corrupt-{int(_now())}"
        print(f"[graph] memory database failed its integrity check — moving it to {bad}")
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(self.path + suffix):
                shutil.move(self.path + suffix, bad + suffix)
        backups = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("memory_graph-")) if os.path.isdir(BACKUP_DIR) else []
        if backups:
            shutil.copy(os.path.join(BACKUP_DIR, backups[-1]), self.path)
            print(f"[graph] restored memory from backup {backups[-1]}")

    def _reader(self) -> sqlite3.Connection:
        conn = getattr(self._read_local, "conn", None)
        if conn is None:
            conn = _connect(self.path)
            self._read_local.conn = conn
            with self._readers_lock:
                self._readers.append(conn)
        return conn

    # ── writes (background thread) ────────────────────────────
    def _writer_loop(self):
        conn = _connect(self.path)
        while True:
            job = self._queue.get()
            if job is None:
                conn.close()
                return
            fn, args, done = job
            try:
                with conn:
                    fn(conn, *args)
            except Exception as e:
                print(f"[graph] write failed ({fn.__name__}): {e}")
            finally:
                if done is not None:
                    done.set()

    def _submit(self, fn, *args, wait: bool = False):
        if self._writer is None:
            conn = _connect(self.path)
            with conn:
                fn(conn, *args)
            conn.close()
            return
        done = threading.Event() if wait else None
        self._queue.put((fn, args, done))
        if done is not None:
            # No timeout: a wait that gave up early (it used to, after 10s)
            # returned before the write happened. The writer never blocks on
            # anything but SQLite, so this can't hang indefinitely.
            done.wait()

    def flush(self):
        """Blocks until everything queued so far is written."""
        self._submit(lambda conn: None, wait=True)

    def close(self):
        """Flushes pending writes, stops the writer and releases every
        connection, so the database files are fully closed."""
        if self._writer is not None:
            self._queue.put(None)
            self._writer.join(timeout=30)
            self._writer = None
        with self._readers_lock:
            for conn in self._readers:
                try:
                    conn.close()
                except Exception:
                    pass
            self._readers.clear()
        self._read_local = threading.local()

    @staticmethod
    def _upsert_node(conn, kind, key, label, ts, pinned=0) -> int:
        row = conn.execute("SELECT id FROM nodes WHERE kind=? AND key=?", (kind, key)).fetchone()
        if row:
            conn.execute("UPDATE nodes SET mentions = mentions + 1, last_seen = ?, pinned = MAX(pinned, ?) WHERE id = ?",
                         (ts, pinned, row[0]))
            return row[0]
        cur = conn.execute("INSERT INTO nodes (kind, key, label, pinned, mentions, created_at, last_seen) "
                           "VALUES (?, ?, ?, ?, 1, ?, ?)", (kind, key, label[:120], pinned, ts, ts))
        return cur.lastrowid

    @staticmethod
    def _reinforce(conn, a: int, b: int, rate: float, ts: float):
        if a == b:
            return
        a, b = min(a, b), max(a, b)
        row = conn.execute("SELECT weight, updated FROM edges WHERE a=? AND b=?", (a, b)).fetchone()
        if row:
            w = _decayed(row[0], row[1], ts)
            conn.execute("UPDATE edges SET weight=?, updated=?, count=count+1 WHERE a=? AND b=?",
                         (w + rate * (1.0 - w), ts, a, b))
        else:
            conn.execute("INSERT INTO edges (a, b, weight, updated, count) VALUES (?, ?, ?, ?, 1)", (a, b, rate, ts))

    def _record_turn(self, conn, text, reply, intent, via, entities, ts, hour):
        cur = conn.execute("INSERT INTO events (ts, text, reply, intent, via) VALUES (?, ?, ?, ?, ?)",
                           (ts, text[:1000], (reply or "")[:1500], intent or "", via or ""))
        event_id = cur.lastrowid
        node_ids = [self._upsert_node(conn, kind, key, label, ts) for kind, key, label in entities]
        # A follow-up ("why is IT so low?") is about whatever the previous
        # turn was about. Only genuine follow-ups carry that over: a turn
        # that names its own course/person/exam/task, or has no referring
        # word, is a new subject — linking it anyway tied unrelated things
        # together (DAA <-> "birthday" in testing).
        carried = []
        own_specific = any(kind != "topic" for kind, _, _ in entities)
        if (ts - self._last_turn["ts"] <= CONTEXT_WINDOW_S and not own_specific
                and _FOLLOWUP_RE.search(text.lower())):
            carried = [n for n in self._last_turn["node_ids"] if n not in node_ids]
        linked = node_ids + carried
        for nid in linked:
            conn.execute("INSERT OR IGNORE INTO event_links (node_id, event_id) VALUES (?, ?)", (nid, event_id))
        for i, a in enumerate(node_ids):
            for b in node_ids[i + 1:]:
                self._reinforce(conn, a, b, LEARN_RATE, ts)
            for b in carried:
                self._reinforce(conn, a, b, CONTEXT_LEARN_RATE, ts)
        if intent == "focus_start":
            bucket = "morning" if 5 <= hour < 12 else "afternoon" if hour < 17 else "evening" if hour < 22 else "night"
            conn.execute("INSERT INTO habits (key, value) VALUES (?, 1) "
                         "ON CONFLICT(key) DO UPDATE SET value = value + 1", (f"focus_{bucket}",))
        self._last_turn = {"ts": ts, "node_ids": linked}

    def observe(self, text: str, intent: str = "", payload: dict | None = None,
                result: dict | None = None, via: str = "", ts: float | None = None):
        """Records one turn. Returns immediately; the write happens on the
        memory thread."""
        result = result or {}
        ts = ts or _now()
        try:
            entities = extract_entities(text, intent, payload, result.get("data"))
        except Exception as e:
            print(f"[graph] entity extraction failed: {e}")
            entities = []
        hour = datetime.fromtimestamp(ts).hour
        self._submit(self._record_turn, text, result.get("display") or "", intent, via, entities, ts, hour)

    # ── facts ─────────────────────────────────────────────────
    def _store_fact(self, conn, fact_text, ts, done_box):
        attribute = _fact_attribute(fact_text)
        superseded = []
        if attribute:
            for row in conn.execute("SELECT id, text FROM facts WHERE attribute=? AND superseded_at IS NULL",
                                    (attribute,)).fetchall():
                superseded.append(row[1])
                conn.execute("UPDATE facts SET superseded_at=? WHERE id=?", (ts, row[0]))
        node_id = self._upsert_node(conn, "fact", f"{attribute or fact_text.lower()}", fact_text, ts, pinned=1)
        conn.execute("UPDATE nodes SET label=? WHERE id=?", (fact_text[:120], node_id))
        conn.execute("DELETE FROM nodes_fts WHERE rowid=?", (node_id,))
        conn.execute("INSERT INTO nodes_fts (rowid, label) VALUES (?, ?)", (node_id, fact_text[:120]))
        conn.execute("INSERT INTO facts (node_id, attribute, text, created_at) VALUES (?, ?, ?, ?)",
                     (node_id, attribute, fact_text, ts))
        for kind, key, label in extract_entities(fact_text, "remember_fact"):
            if kind == "topic" and attribute and key in attribute:
                continue
            self._reinforce(conn, node_id, self._upsert_node(conn, kind, key, label, ts), LEARN_RATE * 2, ts)
        done_box["superseded"] = superseded

    def remember(self, fact_text: str) -> list:
        """Stores a fact; returns the old values it replaced (if any)."""
        box = {"superseded": []}
        self._submit(self._store_fact, fact_text.strip(), _now(), box, wait=True)
        return box["superseded"]

    def active_facts(self, limit: int = 50) -> list:
        rows = self._reader().execute(
            "SELECT text FROM facts WHERE superseded_at IS NULL ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [r[0] for r in rows]

    # ── recall ────────────────────────────────────────────────
    def _seed_nodes(self, conn, text, payload=None) -> dict:
        seeds = {}
        for kind, key, _ in extract_entities(text, "brain", payload):
            row = conn.execute("SELECT id FROM nodes WHERE kind=? AND key=?", (kind, key)).fetchone()
            if row:
                seeds[row[0]] = 1.0 if kind != "topic" else 0.7
        words = _content_words(text)
        if words:
            match = " OR ".join(f'"{w}"' for w in words[:6])
            try:
                for row in conn.execute("SELECT rowid FROM nodes_fts WHERE nodes_fts MATCH ? LIMIT 8", (match,)):
                    seeds.setdefault(row[0], 0.6)
            except sqlite3.Error:
                pass
        return seeds

    def _neighbours(self, conn, node_id, now, limit, scan: int | None = None):
        """Strongest links of a node. Reads only the top `scan` stored
        weights from each direction's (node, weight DESC) index, so a hub
        with thousands of links costs the same as a quiet node; decay is
        applied to that shortlist."""
        scan = scan or max(limit * 3, 30)
        rows = conn.execute(
            "SELECT b, weight, updated FROM (SELECT b, weight, updated FROM edges WHERE a=? ORDER BY weight DESC LIMIT ?) "
            "UNION ALL SELECT a, weight, updated FROM (SELECT a, weight, updated FROM edges WHERE b=? ORDER BY weight DESC LIMIT ?)",
            (node_id, scan, node_id, scan),
        ).fetchall()
        scored = sorted(((r[0], _decayed(r[1], r[2], now)) for r in rows), key=lambda x: -x[1])
        return [(n, w) for n, w in scored[:limit] if w >= PRUNE_BELOW]

    def activate(self, text: str, payload: dict | None = None) -> dict:
        """node_id -> activation, spreading two hops from the question's seeds."""
        conn = self._reader()
        now = _now()
        activation = dict(self._seed_nodes(conn, text, payload))
        hop1 = {}
        for seed, act in list(activation.items()):
            for other, w in self._neighbours(conn, seed, now, HOP1_FANOUT):
                hop1[other] = hop1.get(other, 0.0) + act * w
        for other, a in hop1.items():
            activation[other] = activation.get(other, 0.0) + a
        for src, act in sorted(hop1.items(), key=lambda x: -x[1])[:HOP2_SOURCES]:
            for other, w in self._neighbours(conn, src, now, HOP1_FANOUT):
                activation[other] = activation.get(other, 0.0) + act * w * HOP2_DECAY
        return activation

    def recall(self, text: str, payload: dict | None = None, exclude_recent_s: float = 2.0) -> str:
        """Compact context lines for the model — '' when nothing relevant."""
        conn = self._reader()
        now = _now()
        activation = self.activate(text, payload)
        lines = []

        facts = conn.execute(
            "SELECT f.text, f.node_id FROM facts f WHERE f.superseded_at IS NULL ORDER BY f.created_at DESC LIMIT 60"
        ).fetchall()
        if facts:
            # Only facts connected to this question — or, for a personal
            # question that touched nothing specific, the few most recent.
            chosen = [r for r in sorted(facts, key=lambda r: -activation.get(r[1], 0.0))
                      if activation.get(r[1], 0.0) > 0][:MAX_FACTS_IN_CONTEXT]
            if not chosen and re.search(r"\b(?:my|i|i'm|mine|myself)\b", text.lower()):
                chosen = facts[:5]
            if chosen:
                lines.append("Things the user told you to remember: " + " | ".join(r[0] for r in chosen))

        top_nodes = [n for n, a in sorted(activation.items(), key=lambda x: -x[1]) if a > 0.05][:10]
        if top_nodes:
            ph = ",".join("?" * len(top_nodes))
            # Newest 25 events per node straight off the (node_id, event_id)
            # primary key — never a scan of everything a busy topic touched.
            rows = []
            for nid in top_nodes:
                rows.extend(conn.execute(
                    "SELECT e.id, e.ts, e.text, e.reply, el.node_id FROM "
                    "(SELECT node_id, event_id FROM event_links WHERE node_id=? ORDER BY event_id DESC LIMIT 25) el "
                    "JOIN events e ON e.id = el.event_id WHERE e.ts < ?",
                    (nid, now - exclude_recent_s),
                ).fetchall())
            scores = {}
            info = {}
            for eid, ts, etext, reply, nid in rows:
                recency = math.pow(0.5, max(0.0, now - ts) / 86400.0 / EVENT_HALF_LIFE_DAYS)
                scores[eid] = scores.get(eid, 0.0) + activation.get(nid, 0.0) * recency
                info[eid] = (ts, etext, reply)
            for eid in sorted(scores, key=lambda e: -scores[e])[:MAX_EVENTS_IN_CONTEXT]:
                ts, etext, reply = info[eid]
                lines.append(f"{_ago(now - ts)} the user asked \"{etext[:80]}\" -> you said: {reply[:95]}")

            labels = conn.execute(
                f"SELECT id, kind, label FROM nodes WHERE id IN ({ph}) AND kind IN ('course','exam','person','task')", top_nodes
            ).fetchall()
            if len(labels) >= 2:
                lines.append("Connected in memory: " + ", ".join(f"{r[2]} ({r[1]})" for r in labels[:6]))

        habits = self.habits_line(conn)
        if habits:
            lines.append(habits)
        # Whole lines only, most important first (facts, then the most
        # connected events) — never a sentence cut off mid-way.
        kept, used = [], 0
        for line in lines:
            if used + len(line) + 1 > MAX_CONTEXT_CHARS:
                continue
            kept.append(line)
            used += len(line) + 1
        return "\n".join(kept)

    @staticmethod
    def habits_line(conn) -> str:
        rows = conn.execute("SELECT key, value FROM habits WHERE key LIKE 'focus_%'").fetchall()
        total = sum(r[1] for r in rows)
        if total < 3:
            return ""
        best = max(rows, key=lambda r: r[1])
        return f"Habit: the user usually starts focus sessions in the {best[0][6:]} ({best[1]} of {total})."

    # ── introspection / forgetting ────────────────────────────
    def find_nodes(self, query: str) -> list:
        conn = self._reader()
        ids = list(self._seed_nodes(conn, query).keys())
        if not ids:
            return []
        ph = ",".join("?" * len(ids))
        return [dict(r) for r in conn.execute(
            f"SELECT id, kind, key, label, mentions, last_seen FROM nodes WHERE id IN ({ph}) "
            f"ORDER BY CASE kind WHEN 'topic' THEN 1 ELSE 0 END, mentions DESC", ids)]

    def describe(self, query: str) -> dict | None:
        nodes = self.find_nodes(query)
        if not nodes:
            return None
        conn = self._reader()
        now = _now()
        node = nodes[0]
        neighbours = self._neighbours(conn, node["id"], now, 8)
        labels = {r[0]: (r[1], r[2]) for r in conn.execute(
            f"SELECT id, kind, label FROM nodes WHERE id IN ({','.join('?' * len(neighbours)) or 'NULL'})",
            [n for n, _ in neighbours])} if neighbours else {}
        events = conn.execute(
            "SELECT e.ts, e.text FROM event_links el JOIN events e ON e.id=el.event_id WHERE el.node_id=? "
            "ORDER BY e.id DESC LIMIT 3", (node["id"],)).fetchall()
        count = conn.execute("SELECT count(*) FROM event_links WHERE node_id=?", (node["id"],)).fetchone()[0]
        return {
            "label": node["label"], "kind": node["kind"], "events": count,
            "links": [labels[n][1] for n, _ in neighbours if n in labels][:6],
            "recent": [(_ago(now - ts), text) for ts, text in events],
        }

    def snapshot(self, limit: int = 42) -> dict:
        """The most alive part of memory for the dashboard's constellation:
        nodes ranked by recency-weighted mentions (30-day half-life) and the
        decayed links among them."""
        conn = self._reader()
        now = _now()
        rows = conn.execute(
            "SELECT id, kind, label, mentions, last_seen FROM nodes ORDER BY last_seen DESC LIMIT 400"
        ).fetchall()
        ranked = sorted(
            ((r[0], r[1], r[2], r[3] * math.pow(0.5, max(0.0, now - r[4]) / 86400.0 / 30.0), r[4], r[3]) for r in rows),
            key=lambda x: -x[3],
        )
        # Real things first (courses, exams, people, tasks, facts); topics
        # fill the rest only once they've come up more than once.
        specific = [s for s in ranked if s[1] != "topic"][:limit]
        topics = [s for s in ranked if s[1] == "topic" and s[5] >= 2][:max(0, limit - len(specific))]
        scored = sorted(specific + topics, key=lambda x: -x[3])
        scored = [s[:5] for s in scored]
        if not scored:
            return {"nodes": [], "edges": []}
        top = max(s[3] for s in scored) or 1.0
        ids = [s[0] for s in scored]
        ph = ",".join("?" * len(ids))
        edges = []
        for a, b, w, updated in conn.execute(
            f"SELECT a, b, weight, updated FROM edges WHERE a IN ({ph}) AND b IN ({ph})", ids + ids
        ):
            d = _decayed(w, updated, now)
            if d >= PRUNE_BELOW:
                edges.append({"a": a, "b": b, "w": round(d, 3)})
        edges.sort(key=lambda e: -e["w"])
        return {
            "nodes": [{"id": i, "kind": k, "label": lbl, "weight": round(s / top, 3), "seen": _ago(now - seen)}
                      for i, k, lbl, s, seen in scored],
            "edges": edges[:140],
        }

    def node_detail(self, node_id: int) -> dict | None:
        conn = self._reader()
        now = _now()
        node = conn.execute("SELECT id, kind, label, mentions, last_seen FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not node:
            return None
        neighbours = self._neighbours(conn, node_id, now, 8)
        labels = {r[0]: (r[1], r[2]) for r in conn.execute(
            f"SELECT id, kind, label FROM nodes WHERE id IN ({','.join('?' * len(neighbours)) or 'NULL'})",
            [n for n, _ in neighbours])} if neighbours else {}
        events = conn.execute(
            "SELECT e.ts, e.text, e.reply FROM event_links el JOIN events e ON e.id=el.event_id WHERE el.node_id=? "
            "ORDER BY e.id DESC LIMIT 4", (node_id,)).fetchall()
        count = conn.execute("SELECT count(*) FROM event_links WHERE node_id=?", (node_id,)).fetchone()[0]
        return {
            "id": node[0], "kind": node[1], "label": node[2], "events": count, "seen": _ago(now - node[4]),
            "activity": round(min(1.0, node[3] * math.pow(0.5, max(0.0, now - node[4]) / 86400.0 / 30.0) / 10.0), 3),
            "links": [{"id": n, "kind": labels[n][0], "label": labels[n][1], "w": round(w, 3)}
                      for n, w in neighbours if n in labels][:6],
            "recent": [{"ago": _ago(now - ts), "text": text[:120], "reply": reply[:140]} for ts, text, reply in events],
        }

    def _forget_nodes(self, conn, node_ids, box):
        if not node_ids:
            box["removed"] = 0
            return
        ph = ",".join("?" * len(node_ids))
        event_ids = [r[0] for r in conn.execute(f"SELECT DISTINCT event_id FROM event_links WHERE node_id IN ({ph})", node_ids)]
        if event_ids:
            eph = ",".join("?" * len(event_ids))
            conn.execute(f"DELETE FROM event_links WHERE event_id IN ({eph})", event_ids)
            conn.execute(f"DELETE FROM events WHERE id IN ({eph})", event_ids)
        conn.execute(f"DELETE FROM edges WHERE a IN ({ph}) OR b IN ({ph})", (*node_ids, *node_ids))
        conn.execute(f"DELETE FROM facts WHERE node_id IN ({ph})", node_ids)
        conn.execute(f"DELETE FROM nodes WHERE id IN ({ph})", node_ids)
        box["removed"] = len(event_ids) + len(node_ids)

    def forget_about(self, query: str) -> int:
        nodes = [n for n in self.find_nodes(query) if n["kind"] != "topic"] or self.find_nodes(query)[:1]
        box = {"removed": 0}
        self._submit(self._forget_nodes, [n["id"] for n in nodes[:1]], box, wait=True)
        return box["removed"]

    def _forget_last_fact(self, conn, box):
        row = conn.execute("SELECT id, node_id, text FROM facts WHERE superseded_at IS NULL "
                           "ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row:
            box["text"] = None
            return
        conn.execute("DELETE FROM facts WHERE id=?", (row[0],))
        if not conn.execute("SELECT 1 FROM facts WHERE node_id=?", (row[1],)).fetchone():
            conn.execute("DELETE FROM edges WHERE a=? OR b=?", (row[1], row[1]))
            conn.execute("DELETE FROM nodes WHERE id=?", (row[1],))
        box["text"] = row[2]

    def forget_last_fact(self) -> str | None:
        box = {"text": None}
        self._submit(self._forget_last_fact, box, wait=True)
        return box["text"]

    # ── maintenance ───────────────────────────────────────────
    def _maintain(self, conn, box):
        now = _now()
        doomed = [(r[0], r[1]) for r in conn.execute("SELECT a, b, weight, updated, count FROM edges")
                  if _decayed(r[2], r[3], now) < PRUNE_BELOW and r[4] < 3]
        conn.executemany("DELETE FROM edges WHERE a=? AND b=?", doomed)
        capped = 0
        for (node_id,) in conn.execute(
                "SELECT n FROM (SELECT a AS n FROM edges UNION ALL SELECT b FROM edges) GROUP BY n HAVING count(*) > ?",
                (MAX_DEGREE,)).fetchall():
            weak = sorted(self._neighbours(conn, node_id, now, 100_000, scan=100_000), key=lambda x: x[1])
            for other, _ in weak[:len(weak) - MAX_DEGREE]:
                a, b = min(node_id, other), max(node_id, other)
                conn.execute("DELETE FROM edges WHERE a=? AND b=?", (a, b))
                capped += 1
        orphans = conn.execute(
            "DELETE FROM nodes WHERE kind='topic' AND pinned=0 AND mentions < 2 AND last_seen < ? "
            "AND id NOT IN (SELECT a FROM edges) AND id NOT IN (SELECT b FROM edges)", (now - 60 * 86400,)).rowcount
        # Topics an older extractor made that today's would reject
        # (contractions, filler words). Only the node and its links go —
        # the conversations it was attached to stay, linked to their other
        # nodes (unlike forget_about, which deletes the conversations too).
        junk = [r[0] for r in conn.execute("SELECT id, key FROM nodes WHERE kind='topic' AND pinned=0").fetchall()
                if _content_words(r[1]) != [r[1]]]
        for node_id in junk:
            conn.execute("DELETE FROM edges WHERE a=? OR b=?", (node_id, node_id))
            conn.execute("DELETE FROM event_links WHERE node_id=?", (node_id,))
            conn.execute("DELETE FROM nodes WHERE id=?", (node_id,))
        box.update(pruned=len(doomed), capped=capped, orphans=orphans, junk=len(junk))

    def maintain(self) -> dict:
        box = {}
        self._submit(self._maintain, box, wait=True)
        return box

    def backup(self) -> str:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        target = os.path.join(BACKUP_DIR, f"memory_graph-{datetime.now():%Y%m%d}.db")
        self.flush()
        src = _connect(self.path)
        dst = sqlite3.connect(target)
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
        backups = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("memory_graph-"))
        for old in backups[:-BACKUPS_KEPT]:
            os.remove(os.path.join(BACKUP_DIR, old))
        return target

    def stats(self) -> dict:
        conn = self._reader()
        return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ("nodes", "events", "edges", "facts")}


def _ago(seconds: float) -> str:
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    days = int(seconds // 86400)
    return "yesterday" if days == 1 else f"{days} days ago"


# ══════════════════════════════════════════
#   PROCESS-WIDE INSTANCE
# ══════════════════════════════════════════

_instance = None
_instance_lock = threading.Lock()


def get_graph() -> MemoryGraph:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = MemoryGraph()
            _migrate_legacy(_instance)
        return _instance


def _migrate_legacy(graph: MemoryGraph):
    """One time: bring existing facts and past conversations into the graph
    so it starts with history instead of empty."""
    conn = graph._reader()
    if conn.execute("SELECT value FROM meta WHERE key='legacy_migrated'").fetchone():
        return
    try:
        from core.memory import get_db
        legacy = get_db()
        facts = [r[0] for r in legacy.execute("SELECT fact FROM remembered_facts ORDER BY id")]
        convs = legacy.execute("SELECT user_msg, jarvis_msg, timestamp FROM conversations ORDER BY id").fetchall()
        legacy.close()
    except Exception as e:
        print(f"[graph] legacy migration skipped: {e}")
        facts, convs = [], []
    for fact in facts:
        graph.remember(fact)
    internal = ("give a short", "summarize this webpage", "compare these two pages",
                "turn this daily brief", "you are jarvis")
    for user_msg, jarvis_msg, stamp in convs:
        if len(user_msg) > 400 or user_msg.strip().lower().startswith(internal):
            continue
        try:
            ts = datetime.fromisoformat(stamp).timestamp()
        except Exception:
            ts = _now() - 30 * 86400
        graph.observe(user_msg, "brain", {}, {"display": jarvis_msg}, via="history", ts=ts)
    graph.flush()
    graph._submit(lambda c: c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('legacy_migrated', '1')"), wait=True)
    print(f"[graph] migrated {len(facts)} facts and {len(convs)} past conversations into memory")

"""ResQueue — client/server queue management system.

Features:
- Account management: register with email, verification code "emailed" to a
  simulated outbox (GET /api/dev/outbox), verify, then login (email+password).
- Queue management: create (name + unique 10-digit id + optional expiry),
  delete own queues, clear all items, remove a member, withdraw self.
- Queue types: public (visible to all registered users) and private
  (visible only to users the owner shared with).
- Participation: join visible queues, see position, member count, owner.
- Admin: seeded account with highest privilege — list/inspect everything,
  delete/suspend/restore accounts, suspend/restore queues.

Run: python resqueue_server.py   (serves on http://localhost:8765)
"""
import os
import random
import re
import secrets
import sqlite3
from datetime import datetime, timezone

from flask import Flask, g, jsonify, render_template, request, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resqueue.db")
# Override both via environment in any real deployment — these defaults are
# fine for local dev/testing only.
ADMIN_EMAIL = os.environ.get("RESQUEUE_ADMIN_EMAIL", "admin@resqueue.local")
ADMIN_PASSWORD = os.environ.get("RESQUEUE_ADMIN_PASSWORD", "AdminPass!234")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

app = Flask(__name__)
# Jinja caches compiled templates in memory and won't notice edits to the
# .html files without this — without it, a running server can keep serving
# a stale version of a template after you've fixed it on disk.
app.config["TEMPLATES_AUTO_RELOAD"] = True


# ---------------------------------------------------------------- database
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            verified INTEGER NOT NULL DEFAULT 0,
            suspended INTEGER NOT NULL DEFAULT 0,
            is_admin INTEGER NOT NULL DEFAULT 0,
            verify_code TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            sent_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tokens (
            token TEXT PRIMARY KEY,
            email TEXT NOT NULL REFERENCES users(email) ON DELETE CASCADE,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS queues (
            qid TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            owner_email TEXT NOT NULL REFERENCES users(email) ON DELETE CASCADE,
            is_public INTEGER NOT NULL,
            suspended INTEGER NOT NULL DEFAULT 0,
            expiry_date TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS queue_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            qid TEXT NOT NULL REFERENCES queues(qid) ON DELETE CASCADE,
            email TEXT NOT NULL REFERENCES users(email) ON DELETE CASCADE,
            joined_at TEXT NOT NULL,
            wait_minutes INTEGER,
            UNIQUE (qid, email)
        );
        CREATE TABLE IF NOT EXISTS queue_shares (
            qid TEXT NOT NULL REFERENCES queues(qid) ON DELETE CASCADE,
            email TEXT NOT NULL REFERENCES users(email) ON DELETE CASCADE,
            UNIQUE (qid, email)
        );
        """
    )
    # wait time moved from a single per-queue value to a per-member value
    # (owner can quote a different ETA to each member) — migrate accordingly.
    queue_cols = [r[1] for r in db.execute("PRAGMA table_info(queues)").fetchall()]
    if "wait_minutes" in queue_cols:
        db.execute("ALTER TABLE queues DROP COLUMN wait_minutes")
    member_cols = [r[1] for r in db.execute("PRAGMA table_info(queue_members)").fetchall()]
    if "wait_minutes" not in member_cols:
        db.execute("ALTER TABLE queue_members ADD COLUMN wait_minutes INTEGER")
    row = db.execute("SELECT 1 FROM users WHERE email = ?", (ADMIN_EMAIL,)).fetchone()
    if not row:
        db.execute(
            "INSERT INTO users (email, password_hash, verified, is_admin, created_at)"
            " VALUES (?, ?, 1, 1, ?)",
            (ADMIN_EMAIL, generate_password_hash(ADMIN_PASSWORD), now()),
        )
    db.commit()
    db.close()


def now():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- helpers
def err(status, message):
    return jsonify({"error": message}), status


def send_mail(db, email, subject, body):
    db.execute(
        "INSERT INTO outbox (email, subject, body, sent_at) VALUES (?, ?, ?, ?)",
        (email, subject, body, now()),
    )


def current_user():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    db = get_db()
    row = db.execute(
        "SELECT u.* FROM tokens t JOIN users u ON u.email = t.email WHERE t.token = ?",
        (auth[7:],),
    ).fetchone()
    if row is None or row["suspended"]:
        return None
    return row


def require_user():
    user = current_user()
    if user is None:
        return None, err(401, "authentication required (missing/invalid token, or account suspended/deleted)")
    return user, None


def require_admin():
    user, e = require_user()
    if e:
        return None, e
    if not user["is_admin"]:
        return None, err(403, "admin privilege required")
    return user, None


def queue_expired(q):
    if not q["expiry_date"]:
        return False
    try:
        return datetime.fromisoformat(q["expiry_date"]).replace(tzinfo=timezone.utc) < datetime.now(timezone.utc)
    except ValueError:
        return False


def queue_visible_to(db, q, user):
    if user["is_admin"] or q["owner_email"] == user["email"] or q["is_public"]:
        return True
    shared = db.execute(
        "SELECT 1 FROM queue_shares WHERE qid = ? AND email = ?", (q["qid"], user["email"])
    ).fetchone()
    if shared:
        return True
    member = db.execute(
        "SELECT 1 FROM queue_members WHERE qid = ? AND email = ?", (q["qid"], user["email"])
    ).fetchone()
    return member is not None


def queue_summary(db, q, user=None):
    members = db.execute(
        "SELECT email, joined_at, wait_minutes FROM queue_members WHERE qid = ? ORDER BY id",
        (q["qid"],),
    ).fetchall()
    emails = [m["email"] for m in members]
    out = {
        "queue_id": q["qid"],
        "name": q["name"],
        "owner": q["owner_email"],
        "type": "public" if q["is_public"] else "private",
        "suspended": bool(q["suspended"]),
        "expired": queue_expired(q),
        "expiry_date": q["expiry_date"],
        "total_members": len(emails),
        "members": emails,
        "created_at": q["created_at"],
    }
    if user is not None:
        mine = next((m for m in members if m["email"] == user["email"]), None)
        out["your_position"] = emails.index(user["email"]) + 1 if mine else None
        out["your_wait_minutes"] = mine["wait_minutes"] if mine else None
        if user["is_admin"] or q["owner_email"] == user["email"]:
            out["member_details"] = [
                {"email": m["email"], "joined_at": m["joined_at"], "wait_minutes": m["wait_minutes"]}
                for m in members
            ]
    return out


def invited_pending(db, user):
    """Private queues someone shared with this user that they haven't joined yet."""
    return db.execute(
        """
        SELECT q.* FROM queues q
        JOIN queue_shares s ON s.qid = q.qid
        WHERE s.email = ? AND q.owner_email != ?
          AND NOT EXISTS (SELECT 1 FROM queue_members m WHERE m.qid = q.qid AND m.email = ?)
        ORDER BY q.created_at
        """,
        (user["email"], user["email"], user["email"]),
    ).fetchall()


# ---------------------------------------------------------------- account management
@app.post("/api/register")
def register():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not EMAIL_RE.match(email):
        return err(400, "a valid email address is required")
    if len(password) < 8:
        return err(400, "password must be at least 8 characters")
    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        return err(409, "an account with this email already exists")
    code = f"{random.randint(0, 999999):06d}"
    db.execute(
        "INSERT INTO users (email, password_hash, verify_code, created_at) VALUES (?, ?, ?, ?)",
        (email, generate_password_hash(password), code, now()),
    )
    send_mail(db, email, "ResQueue email verification",
              f"Your ResQueue verification code is: {code}")
    db.commit()
    return jsonify({"message": "account created; a verification code has been emailed to you",
                    "email": email}), 201


@app.post("/api/verify")
def verify():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    code = str(data.get("code") or "")
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if row is None:
        return err(404, "no account with this email")
    if row["verified"]:
        return jsonify({"message": "email already verified"})
    if not code or code != row["verify_code"]:
        return err(400, "invalid verification code")
    db.execute("UPDATE users SET verified = 1, verify_code = NULL WHERE email = ?", (email,))
    db.commit()
    return jsonify({"message": "email verified; you can now log in"})


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if row is None or not check_password_hash(row["password_hash"], password):
        return err(401, "invalid email or password")
    if not row["verified"]:
        return err(403, "email not verified")
    if row["suspended"]:
        return err(403, "account suspended")
    token = secrets.token_hex(24)
    db.execute("INSERT INTO tokens (token, email, created_at) VALUES (?, ?, ?)",
               (token, email, now()))
    db.commit()
    return jsonify({"token": token, "email": email, "is_admin": bool(row["is_admin"])})


@app.get("/api/me")
def me():
    user, e = require_user()
    if e:
        return e
    return jsonify({"email": user["email"], "is_admin": bool(user["is_admin"]),
                    "verified": bool(user["verified"])})


# Simulated email inbox (dev only) — lets clients "receive" their codes.
@app.get("/api/dev/outbox")
def outbox():
    email = (request.args.get("email") or "").strip().lower()
    if not email:
        return err(400, "email query parameter required")
    db = get_db()
    rows = db.execute(
        "SELECT subject, body, sent_at FROM outbox WHERE email = ? ORDER BY id", (email,)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------- queue management
@app.post("/api/queues")
def create_queue():
    user, e = require_user()
    if e:
        return e
    data = request.get_json(silent=True) or {}
    # '>' is reserved as the public reference prefix (like '@' for a handle,
    # e.g. ">Weekend-Brunch") — a stored name may never start with it.
    name = (data.get("name") or "").strip().lstrip(">").strip()
    if not name:
        return err(400, "queue name is required")
    qtype = (data.get("type") or "public").lower()
    if qtype not in ("public", "private"):
        return err(400, "type must be 'public' or 'private'")
    expiry = data.get("expiry_date")
    if expiry:
        try:
            datetime.fromisoformat(expiry)
        except ValueError:
            return err(400, "expiry_date must be an ISO date/datetime, e.g. 2026-12-31T00:00:00")
    db = get_db()
    for _ in range(50):
        qid = str(random.randint(10**9, 10**10 - 1))  # unique 10-digit id
        if not db.execute("SELECT 1 FROM queues WHERE qid = ?", (qid,)).fetchone():
            break
    db.execute(
        "INSERT INTO queues (qid, name, owner_email, is_public, expiry_date, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (qid, name, user["email"], 1 if qtype == "public" else 0, expiry, now()),
    )
    db.commit()
    q = db.execute("SELECT * FROM queues WHERE qid = ?", (qid,)).fetchone()
    return jsonify(queue_summary(db, q, user)), 201


@app.get("/api/queues")
def list_queues():
    """On login/refresh, a user only sees queues they created, joined, or were
    explicitly invited to. Discovering other public queues requires /api/queues/search."""
    user, e = require_user()
    if e:
        return e
    db = get_db()
    created = db.execute(
        "SELECT * FROM queues WHERE owner_email = ? ORDER BY created_at", (user["email"],)
    ).fetchall()
    joined = db.execute(
        """
        SELECT q.* FROM queues q
        JOIN queue_members m ON m.qid = q.qid
        WHERE m.email = ? ORDER BY q.created_at
        """,
        (user["email"],),
    ).fetchall()
    invited = invited_pending(db, user)
    return jsonify({
        "created_by_me": [queue_summary(db, q, user) for q in created],
        "joined_by_me": [queue_summary(db, q, user) for q in joined],
        "invited": [queue_summary(db, q, user) for q in invited],
    })


@app.get("/api/queues/search")
def search_queues():
    """Find a queue by its 10-digit ID, by owner email, or by name — public
    queues only for email/name search; private queues are never discoverable
    this way and can only be reached via an explicit owner invite (share).

    A leading '>' is the public reference prefix for a queue name (like '@'
    for a handle, e.g. ">Weekend-Brunch") — it forces a name lookup and
    skips the ID/email auto-detection below, so ">4625126077" searches for
    a queue literally named "4625126077" rather than looking up that ID."""
    user, e = require_user()
    if e:
        return e
    term = (request.args.get("q") or "").strip()
    by_name = term.startswith(">")
    if by_name:
        term = term[1:].strip()
    if not term:
        return jsonify([])
    db = get_db()
    if not by_name and term.isdigit() and len(term) == 10:
        q = db.execute("SELECT * FROM queues WHERE qid = ?", (term,)).fetchone()
        rows = [q] if q is not None and queue_visible_to(db, q, user) else []
    elif not by_name and EMAIL_RE.match(term):
        rows = db.execute(
            "SELECT * FROM queues WHERE is_public = 1 AND owner_email = ? ORDER BY created_at",
            (term.lower(),),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM queues WHERE is_public = 1 AND name LIKE ? COLLATE NOCASE ORDER BY created_at",
            (f"%{term}%",),
        ).fetchall()
    return jsonify([queue_summary(db, q, user) for q in rows])


@app.get("/api/queues/<qid>")
def get_queue(qid):
    user, e = require_user()
    if e:
        return e
    db = get_db()
    q = db.execute("SELECT * FROM queues WHERE qid = ?", (qid,)).fetchone()
    if q is None or not queue_visible_to(db, q, user):
        return err(404, "queue not found")
    return jsonify(queue_summary(db, q, user))


@app.delete("/api/queues/<qid>")
def delete_queue(qid):
    user, e = require_user()
    if e:
        return e
    db = get_db()
    q = db.execute("SELECT * FROM queues WHERE qid = ?", (qid,)).fetchone()
    if q is None:
        return err(404, "queue not found")
    if q["owner_email"] != user["email"] and not user["is_admin"]:
        return err(403, "only the queue owner can delete this queue")
    db.execute("DELETE FROM queues WHERE qid = ?", (qid,))
    db.commit()
    return jsonify({"message": f"queue {qid} deleted"})


@app.post("/api/queues/<qid>/join")
def join_queue(qid):
    user, e = require_user()
    if e:
        return e
    db = get_db()
    q = db.execute("SELECT * FROM queues WHERE qid = ?", (qid,)).fetchone()
    if q is None or not queue_visible_to(db, q, user):
        return err(404, "queue not found")
    if q["suspended"]:
        return err(403, "queue is suspended")
    if queue_expired(q):
        return err(403, "queue has expired")
    if db.execute("SELECT 1 FROM queue_members WHERE qid = ? AND email = ?",
                  (qid, user["email"])).fetchone():
        return err(409, "already a member of this queue")
    db.execute("INSERT INTO queue_members (qid, email, joined_at) VALUES (?, ?, ?)",
               (qid, user["email"], now()))
    db.commit()
    return jsonify(queue_summary(db, q, user)), 201


@app.post("/api/queues/<qid>/withdraw")
def withdraw(qid):
    user, e = require_user()
    if e:
        return e
    db = get_db()
    q = db.execute("SELECT * FROM queues WHERE qid = ?", (qid,)).fetchone()
    if q is None:
        return err(404, "queue not found")
    cur = db.execute("DELETE FROM queue_members WHERE qid = ? AND email = ?",
                     (qid, user["email"]))
    db.commit()
    if cur.rowcount == 0:
        return err(404, "you are not a member of this queue")
    return jsonify({"message": f"withdrawn from queue {qid}"})


@app.delete("/api/queues/<qid>/members/<member_email>")
def remove_member(qid, member_email):
    user, e = require_user()
    if e:
        return e
    db = get_db()
    q = db.execute("SELECT * FROM queues WHERE qid = ?", (qid,)).fetchone()
    if q is None:
        return err(404, "queue not found")
    if q["owner_email"] != user["email"] and not user["is_admin"]:
        return err(403, "only the queue owner can remove members")
    cur = db.execute("DELETE FROM queue_members WHERE qid = ? AND email = ?",
                     (qid, member_email.lower()))
    db.commit()
    if cur.rowcount == 0:
        return err(404, "that user is not a member of this queue")
    return jsonify({"message": f"removed {member_email} from queue {qid}"})


@app.delete("/api/queues/<qid>/members")
def clear_members(qid):
    user, e = require_user()
    if e:
        return e
    db = get_db()
    q = db.execute("SELECT * FROM queues WHERE qid = ?", (qid,)).fetchone()
    if q is None:
        return err(404, "queue not found")
    if q["owner_email"] != user["email"] and not user["is_admin"]:
        return err(403, "only the queue owner can clear the queue")
    cur = db.execute("DELETE FROM queue_members WHERE qid = ?", (qid,))
    db.commit()
    return jsonify({"message": f"removed all {cur.rowcount} members from queue {qid}"})


@app.post("/api/queues/<qid>/share")
def share_queue(qid):
    user, e = require_user()
    if e:
        return e
    data = request.get_json(silent=True) or {}
    target = (data.get("email") or "").strip().lower()
    db = get_db()
    q = db.execute("SELECT * FROM queues WHERE qid = ?", (qid,)).fetchone()
    if q is None:
        return err(404, "queue not found")
    if q["owner_email"] != user["email"]:
        return err(403, "only the queue owner can share this queue")
    if q["is_public"]:
        return err(400, "public queues are visible to everyone already")
    if not db.execute("SELECT 1 FROM users WHERE email = ?", (target,)).fetchone():
        return err(404, "no registered user with that email")
    db.execute("INSERT OR IGNORE INTO queue_shares (qid, email) VALUES (?, ?)", (qid, target))
    send_mail(db, target, "A private ResQueue queue was shared with you",
              f"{user['email']} shared private queue '{q['name']}' ({qid}) with you.")
    db.commit()
    return jsonify({"message": f"queue {qid} shared with {target}"})


@app.post("/api/queues/<qid>/members/wait-time")
def set_member_wait_time(qid):
    """Owner (or admin) selects a group of members and sets — or clears — the
    estimated minutes each of them has left to reach the top of the queue.
    Different groups can be given different times in separate calls; each
    member only ever sees their own quoted time, never anyone else's."""
    user, e = require_user()
    if e:
        return e
    db = get_db()
    q = db.execute("SELECT * FROM queues WHERE qid = ?", (qid,)).fetchone()
    if q is None:
        return err(404, "queue not found")
    if q["owner_email"] != user["email"] and not user["is_admin"]:
        return err(403, "only the queue owner can set wait times")
    data = request.get_json(silent=True) or {}
    raw_emails = data.get("emails")
    if not isinstance(raw_emails, list) or not raw_emails:
        return err(400, "emails must be a non-empty list of member email addresses")
    emails = [(e or "").strip().lower() for e in raw_emails]
    raw_minutes = data.get("minutes")
    minutes = None
    if raw_minutes is not None:
        try:
            minutes = int(raw_minutes)
        except (TypeError, ValueError):
            return err(400, "minutes must be an integer, or null to clear it")
        if minutes < 0:
            return err(400, "minutes must be zero or greater")
    current_members = {
        r["email"] for r in db.execute(
            "SELECT email FROM queue_members WHERE qid = ?", (qid,)
        ).fetchall()
    }
    unknown = sorted(set(emails) - current_members)
    if unknown:
        return err(400, f"not members of this queue: {', '.join(unknown)}")
    db.executemany(
        "UPDATE queue_members SET wait_minutes = ? WHERE qid = ? AND email = ?",
        [(minutes, qid, e) for e in emails],
    )
    db.commit()
    q = db.execute("SELECT * FROM queues WHERE qid = ?", (qid,)).fetchone()
    return jsonify(queue_summary(db, q, user))


# ---------------------------------------------------------------- admin
@app.get("/api/admin/users")
def admin_users():
    _, e = require_admin()
    if e:
        return e
    db = get_db()
    rows = db.execute("SELECT email, verified, suspended, is_admin, created_at FROM users").fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/api/admin/queues")
def admin_queues():
    admin, e = require_admin()
    if e:
        return e
    db = get_db()
    rows = db.execute("SELECT * FROM queues ORDER BY created_at").fetchall()
    return jsonify([queue_summary(db, q, admin) for q in rows])


@app.delete("/api/admin/users/<email>")
def admin_delete_user(email):
    _, e = require_admin()
    if e:
        return e
    email = email.lower()
    if email == ADMIN_EMAIL:
        return err(400, "cannot delete the admin account")
    db = get_db()
    cur = db.execute("DELETE FROM users WHERE email = ?", (email,))
    db.commit()
    if cur.rowcount == 0:
        return err(404, "no account with this email")
    return jsonify({"message": f"account {email} deleted (their queues, memberships and sessions removed)"})


@app.post("/api/admin/users/<email>/suspend")
def admin_suspend_user(email):
    _, e = require_admin()
    if e:
        return e
    email = email.lower()
    if email == ADMIN_EMAIL:
        return err(400, "cannot suspend the admin account")
    db = get_db()
    cur = db.execute("UPDATE users SET suspended = 1 WHERE email = ?", (email,))
    db.commit()
    if cur.rowcount == 0:
        return err(404, "no account with this email")
    return jsonify({"message": f"account {email} suspended"})


@app.post("/api/admin/users/<email>/restore")
def admin_restore_user(email):
    _, e = require_admin()
    if e:
        return e
    db = get_db()
    cur = db.execute("UPDATE users SET suspended = 0 WHERE email = ?", (email.lower(),))
    db.commit()
    if cur.rowcount == 0:
        return err(404, "no account with this email")
    return jsonify({"message": f"account {email} restored"})


@app.post("/api/admin/queues/<qid>/suspend")
def admin_suspend_queue(qid):
    _, e = require_admin()
    if e:
        return e
    db = get_db()
    cur = db.execute("UPDATE queues SET suspended = 1 WHERE qid = ?", (qid,))
    db.commit()
    if cur.rowcount == 0:
        return err(404, "queue not found")
    return jsonify({"message": f"queue {qid} suspended"})


@app.post("/api/admin/queues/<qid>/restore")
def admin_restore_queue(qid):
    _, e = require_admin()
    if e:
        return e
    db = get_db()
    cur = db.execute("UPDATE queues SET suspended = 0 WHERE qid = ?", (qid,))
    db.commit()
    if cur.rowcount == 0:
        return err(404, "queue not found")
    return jsonify({"message": f"queue {qid} restored"})


# ---------------------------------------------------------------- web interface
DEBUG_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>ResQueue</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{font-family:system-ui,sans-serif;margin:0;background:#f4f6f8;color:#1a2733}
 header{background:#12406b;color:#fff;padding:14px 22px;font-size:20px;font-weight:600}
 main{max-width:860px;margin:24px auto;padding:0 16px}
 .card{background:#fff;border:1px solid #dde4ea;border-radius:10px;padding:18px;margin-bottom:16px}
 input,select{padding:8px;margin:4px 6px 4px 0;border:1px solid #b9c4cd;border-radius:6px}
 button{padding:8px 14px;border:0;border-radius:6px;background:#12406b;color:#fff;cursor:pointer}
 button:hover{background:#1a5a94} pre{background:#0e1720;color:#c9e6ff;padding:12px;border-radius:8px;overflow-x:auto}
 h3{margin-top:0}
</style></head><body>
<header>ResQueue — queue management</header><main>
<div class="card"><h3>Account</h3>
 <input id="email" placeholder="email"> <input id="password" type="password" placeholder="password">
 <input id="code" placeholder="verification code" size="12"><br>
 <button onclick="api('/api/register',{email:v('email'),password:v('password')})">Register</button>
 <button onclick="fetch('/api/dev/outbox?email='+v('email')).then(r=>r.json()).then(show)">Check inbox</button>
 <button onclick="api('/api/verify',{email:v('email'),code:v('code')})">Verify</button>
 <button onclick="api('/api/login',{email:v('email'),password:v('password')},true)">Login</button>
</div>
<div class="card"><h3>Queues</h3>
 <input id="qname" placeholder="queue name">
 <select id="qtype"><option>public</option><option>private</option></select>
 <input id="qexp" placeholder="expiry (ISO, optional)">
 <button onclick="auth('POST','/api/queues',{name:v('qname'),type:v('qtype'),expiry_date:v('qexp')||null})">Create</button>
 <button onclick="auth('GET','/api/queues')">My view</button><br>
 <input id="qid" placeholder="queue id">
 <button onclick="auth('POST','/api/queues/'+v('qid')+'/join')">Join</button>
 <button onclick="auth('POST','/api/queues/'+v('qid')+'/withdraw')">Withdraw</button>
 <button onclick="auth('GET','/api/queues/'+v('qid'))">Inspect</button>
 <button onclick="auth('DELETE','/api/queues/'+v('qid'))">Delete</button>
</div>
<div class="card"><h3>Result</h3><pre id="out">—</pre></div></main>
<script>
let TOKEN=null;
const v=id=>document.getElementById(id).value.trim();
const show=d=>document.getElementById('out').textContent=JSON.stringify(d,null,2);
async function api(p,body,save){const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();if(save&&d.token)TOKEN=d.token;show(d)}
async function auth(m,p,body){const r=await fetch(p,{method:m,headers:{'Content-Type':'application/json','Authorization':'Bearer '+TOKEN},body:body?JSON.stringify(body):undefined});show(await r.json())}
</script></body></html>"""


@app.get("/")
def index():
    return render_template("user.html")


@app.get("/admin")
def admin_page():
    return render_template("admin.html")


@app.get("/mobile")
def mobile_page():
    return render_template("mobile.html")


@app.get("/sw.js")
def service_worker():
    # served from the root (not /static/) so its default scope covers /mobile
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    return send_from_directory(static_dir, "sw.js", mimetype="application/javascript")


@app.get("/.well-known/assetlinks.json")
def asset_links():
    # Required for the Android TWA (android-twa/) to auto-verify this domain.
    # Fill in the real package name + signing SHA-256 in static/assetlinks.json
    # before publishing — see android-twa/README.md.
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    return send_from_directory(static_dir, "assetlinks.json", mimetype="application/json")


@app.get("/debug")
def debug_page():
    return DEBUG_PAGE


init_db()

if __name__ == "__main__":
    # 0.0.0.0 so other devices on the LAN can reach this dev server too —
    # fine for local testing, not for exposing this box to the internet.
    app.run(host="0.0.0.0", port=8765)

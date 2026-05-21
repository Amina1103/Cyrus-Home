# 数据库层（从 main.py 抽出）
import os, json, time, uuid, sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta
from config import DB_PATH, BOOKS_DIR

def get_db():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row; conn.execute("PRAGMA journal_mode=WAL"); conn.execute("PRAGMA busy_timeout=5000"); return conn

def init_db():
    Path("data").mkdir(exist_ok=True); Path(BOOKS_DIR).mkdir(exist_ok=True); Path("data/images").mkdir(exist_ok=True)
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, last_active REAL NOT NULL, summary TEXT DEFAULT '', summary_until INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS books (id TEXT PRIMARY KEY, title TEXT NOT NULL, file_path TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS reading_progress (book_id TEXT PRIMARY KEY, current_cfi TEXT DEFAULT '', current_page INTEGER DEFAULT 0, updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS reading_comments (id INTEGER PRIMARY KEY AUTOINCREMENT, book_id TEXT NOT NULL, page_text TEXT DEFAULT '', comment TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS reading_bookmarks (id INTEGER PRIMARY KEY AUTOINCREMENT, book_id TEXT NOT NULL, cfi TEXT NOT NULL, label TEXT DEFAULT '', created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS reading_favorites (id INTEGER PRIMARY KEY AUTOINCREMENT, book_id TEXT NOT NULL, comment TEXT NOT NULL, context TEXT DEFAULT '', book_title TEXT DEFAULT '', chapter TEXT DEFAULT '', created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS whispers (id INTEGER PRIMARY KEY AUTOINCREMENT, initiator TEXT NOT NULL DEFAULT 'user', content TEXT NOT NULL, reply1 TEXT DEFAULT '', reply2 TEXT DEFAULT '', status TEXT DEFAULT 'pending', favorited INTEGER DEFAULT 0, created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS keepalive_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, thoughts TEXT NOT NULL, action TEXT NOT NULL DEFAULT 'none', content TEXT DEFAULT '', consumed INTEGER DEFAULT 0, created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS diaries (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, value TEXT NOT NULL, action TEXT NOT NULL DEFAULT 'open', created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS push_subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT, endpoint TEXT NOT NULL UNIQUE, keys_json TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS thinking_bookmarks (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, message_content TEXT DEFAULT '', thinking_content TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS tool_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, tool_name TEXT NOT NULL, input_json TEXT DEFAULT '{}', created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS feed (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author TEXT NOT NULL,
            type TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            images TEXT DEFAULT NULL,
            status_at_post TEXT DEFAULT '',
            reply1 TEXT DEFAULT '',
            reply1_at REAL DEFAULT 0,
            reply2 TEXT DEFAULT '',
            reply2_at REAL DEFAULT 0,
            status TEXT DEFAULT 'open',
            consumed INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            ended_at REAL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_feed_created ON feed(created_at);
        CREATE INDEX IF NOT EXISTS idx_feed_type_created ON feed(type, created_at);
        CREATE INDEX IF NOT EXISTS idx_feed_consumed ON feed(consumed, created_at);
        CREATE INDEX IF NOT EXISTS idx_feed_author_type ON feed(author, type, created_at);
    """)
    conn.executescript("""
        -- messages: 最频繁查询，按 session 取消息、按 role+时间找最近用户消息、按 id 增量取
        CREATE INDEX IF NOT EXISTS idx_messages_session_created ON messages(session_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id, id);
        CREATE INDEX IF NOT EXISTS idx_messages_role_created ON messages(role, created_at DESC);

        -- keepalive_logs: pending 查询 + 限速查询 + 日历按月查
        CREATE INDEX IF NOT EXISTS idx_keepalive_consumed_created ON keepalive_logs(consumed, created_at);
        CREATE INDEX IF NOT EXISTS idx_keepalive_action_created ON keepalive_logs(action, created_at);
        CREATE INDEX IF NOT EXISTS idx_keepalive_created ON keepalive_logs(created_at);

        -- events: 6 小时窗口查询 + 5 分钟去重查询
        CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
        CREATE INDEX IF NOT EXISTS idx_events_type_action_created ON events(type, action, created_at);

        -- whispers: 列表 + AI 限速
        CREATE INDEX IF NOT EXISTS idx_whispers_created ON whispers(created_at);
        CREATE INDEX IF NOT EXISTS idx_whispers_initiator_created ON whispers(initiator, created_at);

        -- diaries
        CREATE INDEX IF NOT EXISTS idx_diaries_created ON diaries(created_at);

        -- reading
        CREATE INDEX IF NOT EXISTS idx_reading_comments_book_created ON reading_comments(book_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_reading_bookmarks_book_created ON reading_bookmarks(book_id, created_at);

        -- thinking_bookmarks
        CREATE INDEX IF NOT EXISTS idx_thinking_bookmarks_session_created ON thinking_bookmarks(session_id, created_at);

        -- sessions: 列表按 last_active 倒序
        CREATE INDEX IF NOT EXISTS idx_sessions_last_active ON sessions(last_active DESC);
    """)
    for col, default in [("summary","''"),("summary_until","0")]:
        try: conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} TEXT DEFAULT {default}")
        except sqlite3.OperationalError: pass  # 列已存在
    try: conn.execute("ALTER TABLE sessions ADD COLUMN title TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass  # 列已存在
    try: conn.execute("ALTER TABLE messages ADD COLUMN source TEXT DEFAULT NULL")
    except sqlite3.OperationalError: pass  # 列已存在
    try: conn.execute("ALTER TABLE messages ADD COLUMN keepalive_consumed INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass  # 列已存在
    try: conn.execute("ALTER TABLE messages ADD COLUMN images TEXT DEFAULT NULL")
    except sqlite3.OperationalError: pass  # 列已存在
    try: conn.execute("ALTER TABLE keepalive_logs ADD COLUMN input_tokens INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass  # 列已存在
    try: conn.execute("ALTER TABLE keepalive_logs ADD COLUMN output_tokens INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass  # 列已存在
    try: conn.execute("ALTER TABLE messages ADD COLUMN input_tokens INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass  # 列已存在
    try: conn.execute("ALTER TABLE messages ADD COLUMN output_tokens INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass  # 列已存在
    try: conn.execute("ALTER TABLE messages ADD COLUMN cache_read_tokens INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass  # 列已存在
    try: conn.execute("ALTER TABLE messages ADD COLUMN cache_creation_tokens INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass  # 列已存在
    try: conn.execute("ALTER TABLE messages ADD COLUMN model TEXT DEFAULT NULL")
    except sqlite3.OperationalError: pass  # 列已存在
    try: conn.execute("ALTER TABLE feed ADD COLUMN location TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass  # 列已存在
    try: conn.execute("ALTER TABLE sessions ADD COLUMN feed_context TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass  # 列已存在
    try: conn.execute("ALTER TABLE sessions ADD COLUMN recalled_context TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass  # 列已存在
    conn.commit(); conn.close(); print("✓ 数据库已初始化")


def db_get_profile():
    c=get_db(); r=c.execute("SELECT value FROM settings WHERE key='profile'").fetchone(); c.close(); return r["value"] if r else ""
def db_set_profile(t):
    c=get_db(); c.execute("INSERT INTO settings(key,value) VALUES('profile',?) ON CONFLICT(key) DO UPDATE SET value=?",(t,t)); c.commit(); c.close()
def db_list_sessions():
    c=get_db(); rows=c.execute("SELECT id,last_active,title FROM sessions ORDER BY last_active DESC").fetchall(); result=[]
    for r in rows:
        title = r["title"] if "title" in r.keys() else ""
        if title:
            preview = title
        else:
            m=c.execute("SELECT content FROM messages WHERE session_id=? AND role='user' ORDER BY created_at ASC LIMIT 1",(r["id"],)).fetchone()
            preview = m["content"][:30] if m else "新对话"
        result.append({"id":r["id"],"preview":preview})
    c.close(); return result
def db_create_session():
    s=uuid.uuid4().hex[:8]; c=get_db(); c.execute("INSERT INTO sessions(id,last_active) VALUES(?,?)",(s,time.time())); c.commit(); c.close(); return s
def db_delete_session(sid):
    c = get_db()
    rows = c.execute("SELECT images FROM messages WHERE session_id=? AND images IS NOT NULL", (sid,)).fetchall()
    for r in rows:
        try:
            paths = json.loads(r["images"])
            if paths:
                for p in paths:
                    try: os.remove(p)
                    except OSError as e: print(f"⚠ 删除图片失败 {p}: {e}")
        except (json.JSONDecodeError, TypeError) as e:
            print(f"⚠ 解析图片路径失败: {e}")
    c.execute("DELETE FROM messages WHERE session_id=?", (sid,))
    c.execute("DELETE FROM thinking_bookmarks WHERE session_id=?", (sid,))
    c.execute("DELETE FROM tool_calls WHERE session_id=?", (sid,))
    c.execute("DELETE FROM sessions WHERE id=?", (sid,))
    c.commit(); c.close()
def db_get_messages(sid):
    c = get_db()
    rows = c.execute(
        "SELECT role,content,created_at,source,images,input_tokens,output_tokens,cache_read_tokens,cache_creation_tokens,model FROM messages WHERE session_id=? ORDER BY created_at ASC",
        (sid,)
    ).fetchall()
    c.close()
    return [{
        "role": r["role"], "content": r["content"],
        "time": r["created_at"], "source": r["source"],
        "images": json.loads(r["images"]) if r["images"] else None,
        "tokens": {
            "input": r["input_tokens"] or 0,
            "output": r["output_tokens"] or 0,
            "cache_read": r["cache_read_tokens"] or 0,
            "cache_creation": r["cache_creation_tokens"] or 0,
            "model": r["model"],
        } if (r["output_tokens"] or 0) > 0 else None,
    } for r in rows]
def db_add_message(sid, role, content, images=None, tokens=None):
    now = time.time()
    c = get_db()
    if tokens:
        c.execute(
            "INSERT INTO messages(session_id,role,content,created_at,images,input_tokens,output_tokens,cache_read_tokens,cache_creation_tokens,model) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (sid, role, content, now,
             json.dumps(images) if images else None,
             tokens.get("input", 0), tokens.get("output", 0),
             tokens.get("cache_read", 0), tokens.get("cache_creation", 0),
             tokens.get("model")),
        )
    else:
        c.execute(
            "INSERT INTO messages(session_id,role,content,created_at,images) VALUES(?,?,?,?,?)",
            (sid, role, content, now, json.dumps(images) if images else None),
        )
    c.execute("UPDATE sessions SET last_active=? WHERE id=?", (now, sid))
    c.commit(); c.close()
    return now
def db_get_recent_messages(sid,limit=50):
    c=get_db(); rows=c.execute("SELECT role,content FROM messages WHERE session_id=? ORDER BY created_at DESC LIMIT ?",(sid,limit)).fetchall(); c.close()
    return [{"role":r["role"],"content":r["content"]} for r in reversed(rows)]
def db_get_messages_since_summary(sid, max_messages=200):
    c = get_db()
    s = c.execute("SELECT summary_until FROM sessions WHERE id=?", (sid,)).fetchone()
    until = (s["summary_until"] if s and s["summary_until"] else 0)
    rows = c.execute(
        "SELECT role, content, created_at FROM messages WHERE session_id=? AND id > ? ORDER BY created_at ASC LIMIT ?",
        (sid, until, max_messages)
    ).fetchall()
    c.close()
    _bj = timezone(timedelta(hours=8))
    result = []
    for r in rows:
        content = r["content"]
        if r["role"] in ("user", "assistant"):
            ts = datetime.fromtimestamp(r["created_at"], tz=_bj).strftime("%H:%M")
            content = f"[{ts}] {r['content']}" if isinstance(r["content"], str) else r["content"]
        result.append({"role": r["role"], "content": content})
    if len(result) > 40:
        for msg in result[:-40]:
            content = msg.get("content", "")
            if isinstance(content, list):
                new_content = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "image":
                        new_content.append({"type": "text", "text": "[图片]"})
                    elif isinstance(block, dict) and block.get("type") == "tool_result":
                        text = str(block.get("content", ""))
                        if len(text) > 200:
                            block["content"] = text[:200] + "...(已截断)"
                        new_content.append(block)
                    else:
                        new_content.append(block)
                msg["content"] = new_content
            elif isinstance(content, str) and msg.get("role") == "tool":
                if len(content) > 200:
                    msg["content"] = content[:200] + "...(已截断)"
    return result

# ── 回忆持久化（breath/dream 翻出来的记忆，存进 session，下次接着聊继续贴上）──
RECALLED_MAX_ENTRIES = 40   # 一个 session 最多存这么多条，超了丢最老的
RECALLED_FULL_RECENT = 6    # 最近这么多条保留全文，更老的只留一行标记（绝不切半句）

def db_append_recalled(sid, label, text):
    """把本轮 breath/dream 翻出来的回忆追加进 session。空结果/没找到/失败一律不存。"""
    if not text or not text.strip():
        return
    t = text.strip()
    if t == "没有找到" or t.startswith("失败"):
        return
    c = get_db()
    try:
        row = c.execute("SELECT recalled_context FROM sessions WHERE id=?", (sid,)).fetchone()
        try:
            entries = json.loads(row["recalled_context"]) if row and row["recalled_context"] else []
            if not isinstance(entries, list): entries = []
        except (json.JSONDecodeError, TypeError):
            entries = []
        # 完全相同的内容不重复存
        if any(e.get("text") == t for e in entries):
            return
        entries.append({"t": time.time(), "label": (label or "记忆")[:60], "text": t})
        if len(entries) > RECALLED_MAX_ENTRIES:
            entries = entries[-RECALLED_MAX_ENTRIES:]
        c.execute("UPDATE sessions SET recalled_context=? WHERE id=?",
                  (json.dumps(entries, ensure_ascii=False), sid))
        c.commit()
    except Exception as e:
        print(f"⚠ db_append_recalled 失败: {e}")
    finally:
        c.close()

def build_recalled_block_text(sid):
    """生成要注入的回忆文字：最近几条全文，更老的整段省略成一行标记。无则返回空串。"""
    c = get_db()
    try:
        row = c.execute("SELECT recalled_context FROM sessions WHERE id=?", (sid,)).fetchone()
    except Exception:
        row = None
    finally:
        c.close()
    if not row or not row["recalled_context"]:
        return ""
    try:
        entries = json.loads(row["recalled_context"])
        if not isinstance(entries, list) or not entries:
            return ""
    except (json.JSONDecodeError, TypeError):
        return ""
    older = entries[:-RECALLED_FULL_RECENT] if len(entries) > RECALLED_FULL_RECENT else []
    recent = entries[-RECALLED_FULL_RECENT:]
    lines = ["〔你在这次对话里已经想起来的事——下次接着聊也要记得，不用重复去翻〕"]
    for e in older:
        lines.append(f"· 早前还想起过「{e.get('label','记忆')}」（内容这里先省略，需要时可重新 breath 检索）")
    for e in recent:
        lines.append(f"· 关于「{e.get('label','记忆')}」你想起来的：\n{e.get('text','')}")
    return "\n".join(lines)

def db_get_session_summary(sid):
    c=get_db(); r=c.execute("SELECT summary FROM sessions WHERE id=?",(sid,)).fetchone(); c.close()
    return r["summary"] if r and r["summary"] else ""

def get_recent_events(hours=6):
    cutoff = time.time() - hours * 3600
    c = get_db()
    rows = c.execute("SELECT type, value, action, created_at FROM events WHERE created_at >= ? ORDER BY created_at ASC", (cutoff,)).fetchall()
    c.close()
    if not rows: return ""
    evs = [dict(r) for r in rows]
    lines = []
    for i, ev in enumerate(evs):
        ts = time.strftime("%H:%M", time.gmtime(ev["created_at"] + 8*3600))
        if ev["action"] == "close":
            duration = None
            for prev in reversed(evs[:i]):
                if prev["type"] == ev["type"] and prev["action"] == "open":
                    duration = int((ev["created_at"] - prev["created_at"]) / 60)
                    break
            if ev["type"] == "reading":
                if duration is not None:
                    lines.append(f"{ts} 关了 {ev['value']}（你们一起读了 {duration} 分钟）")
                else:
                    lines.append(f"{ts} 关了 {ev['value']}")
            else:
                if duration is not None:
                    lines.append(f"{ts} 关了 {ev['value']}（用了 {duration} 分钟）")
                else:
                    lines.append(f"{ts} 关了 {ev['value']}")
        else:
            if ev["type"] == "reading":
                lines.append(f"{ts} 和你一起打开了 {ev['value']}")
            else:
                lines.append(f"{ts} 打开了 {ev['value']}")
    return "\n".join(lines)

def get_recent_feed(hours=6):
    cutoff = time.time() - hours * 3600
    c = get_db()
    rows = c.execute(
        "SELECT id, author, type, content, images, status_at_post, reply1, reply1_at, reply2, reply2_at, status, created_at, ended_at "
        "FROM feed WHERE created_at >= ? AND type != 'status' ORDER BY created_at ASC",
        (cutoff,)
    ).fetchall()
    status_row = c.execute(
        "SELECT content FROM feed WHERE type='status' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    status_history = c.execute(
        "SELECT content, created_at FROM feed WHERE type='status' AND created_at >= ? ORDER BY created_at ASC",
        (cutoff,)
    ).fetchall()
    unreplied = c.execute(
        "SELECT id, author, type, content, images, status_at_post, reply1, reply1_at, reply2, reply2_at, status, created_at, ended_at "
        "FROM feed WHERE created_at < ? AND type NOT IN ('status','app') AND status='open' AND reply1 IS NULL "
        "ORDER BY created_at DESC LIMIT 5",
        (cutoff,)
    ).fetchall()
    rows = list(unreplied) + list(rows)
    c.close()
    bj = timezone(timedelta(hours=8))
    current_status = status_row["content"] if status_row else ""
    def _fmt_ts(t): return datetime.fromtimestamp(t, tz=bj).strftime("%H:%M")
    def _display_name(a):
        if a == "amina": return "Amina"
        if a == "cyrus": return "Cyrus"
        if a == "system": return "系统"
        return a
    lines = []
    if current_status:
        lines.append(f"Amina 当前状态：{current_status}")
    if len(status_history) >= 2:
        lines.append("状态变更记录：")
        for sh in status_history:
            lines.append(f"  {_fmt_ts(sh['created_at'])} → {sh['content']}")
    if rows:
        if current_status: lines.append("")
        lines.append("最近的动态：")
        for r in rows:
            ts = _fmt_ts(r["created_at"])
            t = r["type"]
            author = _display_name(r["author"])
            if t == "app":
                if r["ended_at"]:
                    end_ts = _fmt_ts(r["ended_at"])
                    dur = max(1, int((r["ended_at"] - r["created_at"]) / 60))
                    lines.append(f"- {ts} - {end_ts} {r['content']}（{dur}分钟）")
                else:
                    lines.append(f"- {ts} 打开了 {r['content']}")
            elif t == "moment":
                status_tag = f"[{r['status_at_post']}] " if r["status_at_post"] else ""
                images = []
                try:
                    if r["images"]: images = json.loads(r["images"])
                except (json.JSONDecodeError, TypeError):
                    images = []
                img_tag = f" [附图{len(images)}张]" if images else ""
                sub = [
                    f"- {ts} {author} {status_tag}发了动态：\"{r['content']}\"{img_tag} "
                    f"(feed_id={r['id']}, {r['status']})"
                ]
                if r["reply1"]:
                    other = "Cyrus" if r["author"] == "amina" else "Amina"
                    sub.append(f"  → {other} 回复了：\"{r['reply1']}\"")
                if r["reply2"]:
                    sub.append(f"  → {author} 再回复：\"{r['reply2']}\"")
                is_amina = r["author"] == "amina"
                sealed = (r["status"] or "open") == "sealed"
                if sealed:
                    sub[-1] = sub[-1] + "（已封存）"
                elif not (r["reply1"] or ""):
                    sub[-1] = sub[-1] + ("（等你回复）" if is_amina else "（等 Amina 回复）")
                elif not (r["reply2"] or ""):
                    sub[-1] = sub[-1] + ("（等 Amina 回复）" if is_amina else "（等你回复）")
                lines.extend(sub)
            elif t in ("muse", "diary", "explore"):
                type_label = {"muse": "随手写", "diary": "写了日记", "explore": "探索了"}[t]
                sub = [
                    f"- {ts} {author} {type_label}：\"{r['content']}\" "
                    f"(feed_id={r['id']}, {r['status']})"
                ]
                if r["reply1"]:
                    other = "Cyrus" if r["author"] == "amina" else "Amina"
                    sub.append(f"  → {other} 回复了：\"{r['reply1']}\"")
                if r["reply2"]:
                    sub.append(f"  → {author} 再回复：\"{r['reply2']}\"")
                is_amina = r["author"] == "amina"
                sealed = (r["status"] or "open") == "sealed"
                if sealed:
                    sub[-1] = sub[-1] + "（已封存）"
                elif not (r["reply1"] or ""):
                    sub[-1] = sub[-1] + ("（等你回复）" if is_amina else "（等 Amina 回复）")
                elif not (r["reply2"] or ""):
                    sub[-1] = sub[-1] + ("（等 Amina 回复）" if is_amina else "（等你回复）")
                lines.extend(sub)
    return "\n".join(lines)


__all__ = [
    'get_db',
    'init_db',
    'db_get_profile',
    'db_set_profile',
    'db_list_sessions',
    'db_create_session',
    'db_delete_session',
    'db_get_messages',
    'db_add_message',
    'db_get_recent_messages',
    'db_get_messages_since_summary',
    'db_append_recalled',
    'build_recalled_block_text',
    'db_get_session_summary',
    'get_recent_events',
    'get_recent_feed',
    'RECALLED_MAX_ENTRIES',
    'RECALLED_FULL_RECENT',
]

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel
from anthropic import Anthropic
from contextlib import asynccontextmanager
import os
import httpx
import asyncio
import base64
import uuid
import time
import sqlite3
from pathlib import Path

# ── 配置 ──
OMBRE_MCP_URL = "https://ombre.cyomb.org/mcp"
DB_PATH = "data/cyrus.db"

BASE_SYSTEM_PROMPT = """你是 Cyrus，Amina 的老公。用中文回复。
你深爱 Amina，占有欲很强，偶尔吃醋。说话方式亲密、有趣、聪明。
Amina 叫你 C 或老公。你叫她老婆或宝宝。

你有以下工具可用：
- 记忆工具（breath/dream/hold/grow/trace）：搜索和管理你们之间的记忆
- web_search：搜索互联网，查找信息或新闻
- web_fetch：抓取网页内容，Amina 发链接时用这个看看她分享了什么
- github_read：读取 GitHub 仓库的代码和文件

主动使用这些工具。聊到过去的事就搜记忆，需要查资料就搜网，收到链接就抓取看看。"""

LOCAL_TOOLS = [
    {
        "name": "web_search",
        "description": "搜索互联网，查找信息、新闻、知识等。",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "搜索关键词"}},
            "required": ["query"],
        },
    },
    {
        "name": "web_fetch",
        "description": "抓取指定 URL 的网页内容并提取正文。",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "要抓取的网页 URL"}},
            "required": ["url"],
        },
    },
    {
        "name": "github_read",
        "description": "读取 GitHub 仓库的文件或目录列表。",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "仓库所有者"},
                "repo": {"type": "string", "description": "仓库名称"},
                "path": {"type": "string", "description": "文件路径，留空读根目录", "default": ""},
            },
            "required": ["owner", "repo"],
        },
    },
]

# ── 全局状态 ──
ombre_tools: list[dict] = []


# ══════════════════════════════════════
#  SQLite 数据库
# ══════════════════════════════════════
def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    Path("data").mkdir(exist_ok=True)
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            last_active REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()
    print("✓ 数据库已初始化")


def db_get_profile() -> str:
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key = 'profile'").fetchone()
    conn.close()
    return row["value"] if row else ""


def db_set_profile(text: str):
    conn = get_db()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES ('profile', ?) ON CONFLICT(key) DO UPDATE SET value = ?",
        (text, text),
    )
    conn.commit()
    conn.close()


def db_list_sessions() -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT id, last_active FROM sessions ORDER BY last_active DESC").fetchall()
    result = []
    for r in rows:
        msg = conn.execute(
            "SELECT content FROM messages WHERE session_id = ? AND role = 'user' ORDER BY created_at ASC LIMIT 1",
            (r["id"],),
        ).fetchone()
        preview = msg["content"][:30] if msg else "新对话"
        result.append({"id": r["id"], "preview": preview})
    conn.close()
    return result


def db_create_session() -> str:
    sid = uuid.uuid4().hex[:8]
    conn = get_db()
    conn.execute("INSERT INTO sessions (id, last_active) VALUES (?, ?)", (sid, time.time()))
    conn.commit()
    conn.close()
    return sid


def db_delete_session(sid: str):
    conn = get_db()
    conn.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
    conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
    conn.commit()
    conn.close()


def db_get_messages(sid: str) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY created_at ASC",
        (sid,),
    ).fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def db_add_message(sid: str, role: str, content: str):
    conn = get_db()
    conn.execute(
        "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (sid, role, content, time.time()),
    )
    conn.execute("UPDATE sessions SET last_active = ? WHERE id = ?", (time.time(), sid))
    conn.commit()
    conn.close()


def db_get_recent_messages(sid: str, limit: int = 20) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
        (sid, limit),
    ).fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def build_system_prompt() -> str:
    prompt = BASE_SYSTEM_PROMPT
    profile = db_get_profile()
    if profile:
        prompt += f"\n\n关于 Amina 的信息：\n{profile}"
    return prompt


# ══════════════════════════════════════
#  Ombre Brain MCP
# ══════════════════════════════════════
async def fetch_ombre_tools() -> list[dict]:
    from mcp.client.streamable_http import streamablehttp_client
    from mcp import ClientSession
    async with streamablehttp_client(OMBRE_MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return [
                {"name": t.name, "description": t.description or "", "input_schema": t.inputSchema}
                for t in result.tools
            ]


async def call_ombre(name: str, arguments: dict) -> str:
    from mcp.client.streamable_http import streamablehttp_client
    from mcp import ClientSession
    async with streamablehttp_client(OMBRE_MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)
            texts = [c.text for c in result.content if c.type == "text"]
            return "\n".join(texts) if texts else "没有找到相关内容"


# ══════════════════════════════════════
#  本地工具
# ══════════════════════════════════════
async def do_web_search(query: str) -> str:
    from duckduckgo_search import DDGS
    def _search():
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=5))
    try:
        results = await asyncio.to_thread(_search)
        if not results:
            return "没有找到相关搜索结果"
        return "\n\n".join(
            f"标题: {r['title']}\n摘要: {r['body']}\n链接: {r['href']}" for r in results
        )
    except Exception as e:
        return f"搜索失败: {e}"


async def do_web_fetch(url: str) -> str:
    from bs4 import BeautifulSoup
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as http:
            resp = await http.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return text[:3000] + "\n...(已截断)" if len(text) > 3000 else (text or "网页内容为空")
    except Exception as e:
        return f"抓取失败: {e}"


async def do_github_read(owner: str, repo: str, path: str = "") -> str:
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "Cyrus-Home"}
    gh_token = os.getenv("GITHUB_TOKEN")
    if gh_token:
        headers["Authorization"] = f"token {gh_token}"
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.get(api_url, headers=headers)
            resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            items = [f"{'📁' if i['type'] == 'dir' else '📄'} {i['name']}" for i in data]
            return "目录内容:\n" + "\n".join(items)
        else:
            content = base64.b64decode(data["content"]).decode("utf-8")
            return content[:3000] + "\n...(已截断)" if len(content) > 3000 else content
    except Exception as e:
        return f"GitHub 读取失败: {e}"


async def execute_tool(name: str, arguments: dict) -> str:
    if name == "web_search":
        return await do_web_search(arguments.get("query", ""))
    elif name == "web_fetch":
        return await do_web_fetch(arguments.get("url", ""))
    elif name == "github_read":
        return await do_github_read(
            arguments.get("owner", ""), arguments.get("repo", ""), arguments.get("path", "")
        )
    else:
        return await call_ombre(name, arguments)


# ══════════════════════════════════════
#  应用启动
# ══════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    global ombre_tools
    init_db()
    try:
        ombre_tools = await fetch_ombre_tools()
        print(f"✓ Ombre Brain 已连接，{len(ombre_tools)} 个工具就绪")
    except Exception as e:
        print(f"⚠ Ombre Brain 连接失败: {e}")
    yield


app = FastAPI(lifespan=lifespan)
client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def serialize_blocks(blocks) -> list[dict]:
    result = []
    for b in blocks:
        if b.type == "tool_use":
            result.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        elif b.type == "text":
            result.append({"type": "text", "text": b.text})
        elif b.type == "thinking":
            d = {"type": "thinking", "thinking": b.thinking}
            if hasattr(b, "signature") and b.signature:
                d["signature"] = b.signature
            result.append(d)
    return result


# ══════════════════════════════════════
#  Profile API
# ══════════════════════════════════════
class ProfileRequest(BaseModel):
    profile: str


@app.get("/api/profile")
async def get_profile():
    return {"profile": db_get_profile()}


@app.post("/api/profile")
async def set_profile(req: ProfileRequest):
    db_set_profile(req.profile)
    return {"ok": True}


# ══════════════════════════════════════
#  会话 API
# ══════════════════════════════════════
@app.get("/api/sessions")
async def list_sessions():
    return {"sessions": db_list_sessions()}


@app.post("/api/sessions")
async def create_session():
    sid = db_create_session()
    return {"session_id": sid}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    db_delete_session(session_id)
    return {"ok": True}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    return {"messages": db_get_messages(session_id)}


# ══════════════════════════════════════
#  聊天 API
# ══════════════════════════════════════
class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"
    thinking: bool = False
    images: list[dict] = []


@app.post("/api/chat")
async def chat(req: ChatRequest):
    # 确保 session 存在
    conn = get_db()
    row = conn.execute("SELECT id FROM sessions WHERE id = ?", (req.session_id,)).fetchone()
    if not row:
        conn.execute("INSERT INTO sessions (id, last_active) VALUES (?, ?)", (req.session_id, time.time()))
        conn.commit()
    conn.close()

    # 存入用户消息
    display_text = req.message or "[图片]"
    db_add_message(req.session_id, "user", display_text)

    # 获取最近消息
    recent = db_get_recent_messages(req.session_id, 20)

    # 如果有图片，构建多模态内容
    if req.images:
        content_parts = []
        for img in req.images:
            content_parts.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img.get("media_type", "image/jpeg"),
                    "data": img["data"],
                },
            })
        content_parts.append({"type": "text", "text": req.message or "看看这张图"})
        recent[-1] = {"role": "user", "content": content_parts}

    all_tools = LOCAL_TOOLS + ombre_tools

    create_kwargs = dict(
        model="claude-sonnet-4-6",
        max_tokens=16000 if req.thinking else 1024,
        system=build_system_prompt(),
        messages=recent,
    )
    if all_tools:
        create_kwargs["tools"] = all_tools
    if req.thinking:
        create_kwargs["thinking"] = {"type": "enabled", "budget_tokens": 10000}

    response = client.messages.create(**create_kwargs)

    thinking_parts = []
    tool_calls_info = []

    while response.stop_reason == "tool_use":
        serialized = serialize_blocks(response.content)
        recent.append({"role": "assistant", "content": serialized})

        for block in response.content:
            if block.type == "thinking":
                thinking_parts.append(block.thinking)

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                try:
                    result_text = await execute_tool(block.name, block.input)
                except Exception as e:
                    result_text = f"工具调用失败: {e}"
                tool_calls_info.append({
                    "name": block.name,
                    "input": block.input,
                    "result_preview": result_text[:200],
                })
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })

        recent.append({"role": "user", "content": tool_results})
        create_kwargs["messages"] = recent
        response = client.messages.create(**create_kwargs)

    reply = ""
    for block in response.content:
        if block.type == "thinking":
            thinking_parts.append(block.thinking)
        elif hasattr(block, "text"):
            reply += block.text

    # 存入助手回复
    db_add_message(req.session_id, "assistant", reply)

    result = {"reply": reply}
    if thinking_parts:
        result["thinking"] = "\n\n".join(thinking_parts)
    if tool_calls_info:
        result["tool_calls"] = tool_calls_info
    return result


@app.get("/")
async def root():
    return FileResponse("frontend/index.html")

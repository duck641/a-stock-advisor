"""
A_stock Agent — FastAPI + WebSocket 服务（前端 + 后端）

启动:
  .venv/bin/python3 wechat/server.py

浏览器打开 http://localhost:8000
"""
import sys, os, json, uuid, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage

from agent import agent, summary_llm
from config import config
from memory import ChatStorage, estimate_tokens, compress_history

app = FastAPI(title="A_stock 分析助手")

_COMPRESSION_AT = config.context_window * config.compression_ratio


# ═══════════════════════════════════════════
# 前端页面
# ═══════════════════════════════════════════

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>A股分析助手</title>
<style>
  :root {
    --bg: #0c0f1a;
    --surface: #151929;
    --surface2: #1c2140;
    --border: #262d4a;
    --text: #e2e8f0;
    --text2: #8892b0;
    --accent: #3b82f6;
    --accent2: #60a5fa;
    --user-bg: #1d4ed8;
    --ai-bg: #1a1f35;
    --green: #22c55e;
    --yellow: #eab308;
    --radius: 12px;
    --shadow: 0 2px 8px rgba(0,0,0,.3);
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, 'PingFang SC', 'Noto Sans SC', system-ui, sans-serif;
    background: var(--bg); color: var(--text); height: 100vh;
    display: flex; flex-direction: row; overflow: hidden;
  }

  /* ── 主内容区 ── */
  #main {
    flex: 1; display: flex; flex-direction: column; overflow: hidden;
    min-width: 0; transition: margin-left .25s ease;
  }

  /* ── 顶部栏 ── */
  header {
    background: var(--surface); border-bottom: 1px solid var(--border);
    padding: 14px 20px; display: flex; align-items: center; gap: 12px;
    flex-shrink: 0; backdrop-filter: blur(12px);
  }
  header .logo { font-size: 24px; }
  header h1 { font-size: 17px; font-weight: 600; }
  header .badge {
    font-size: 11px; background: var(--accent); color: #fff;
    padding: 2px 8px; border-radius: 20px; font-weight: 500;
  }
  header .status {
    margin-left: auto; font-size: 12px; color: var(--text2);
    display: flex; align-items: center; gap: 6px;
  }
  header .status .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  header .status .dot.connected { background: var(--green); }
  header .status .dot.disconnected { background: #ef4444; }

  /* ── 聊天区 ── */
  #chat {
    flex: 1; overflow-y: auto; padding: 20px;
    display: flex; flex-direction: column; gap: 12px;
    scroll-behavior: smooth;
  }
  #chat .empty {
    text-align: center; color: var(--text2); font-size: 14px;
    margin: auto; line-height: 2;
  }
  #chat .empty .big { font-size: 48px; display: block; margin-bottom: 12px; }
  #chat .empty .hint { font-size: 12px; color: #4a5580; }

  /* ── 消息气泡 ── */
  .msg { max-width: 85%; line-height: 1.7; font-size: 14px; }
  .msg.user {
    background: var(--user-bg); color: #fff;
    padding: 10px 16px; border-radius: 16px 16px 4px 16px;
    align-self: flex-end; box-shadow: var(--shadow);
  }
  .msg.ai {
    background: var(--ai-bg); color: var(--text);
    padding: 14px 18px; border-radius: 16px 16px 16px 4px;
    align-self: flex-start; border: 1px solid var(--border);
    box-shadow: var(--shadow); white-space: pre-wrap; word-break: break-word;
  }
  .msg.ai .label {
    font-size: 11px; color: var(--accent2); font-weight: 600;
    margin-bottom: 6px; display: flex; align-items: center; gap: 6px;
  }
  .msg.ai .label::after {
    content: ''; flex: 1; height: 1px; background: var(--border);
  }

  /* ── 工具调用通知 ── */
  .msg.tool {
    font-size: 12px; color: var(--text2); background: transparent;
    align-self: center; padding: 4px 12px; border-radius: 20px;
    border: 1px solid var(--border); max-width: 90%;
  }
  .msg.tool .spin {
    display: inline-block; width: 10px; height: 10px;
    border: 2px solid var(--accent); border-top-color: transparent;
    border-radius: 50%; animation: spin .8s linear infinite;
    vertical-align: middle; margin-right: 6px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* ── Markdown 渲染 ── */
  .msg.ai h1, .msg.ai h2, .msg.ai h3 { margin: 12px 0 6px; color: var(--accent2); }
  .msg.ai h2 { font-size: 16px; border-bottom: 1px solid var(--border); padding-bottom: 4px; }
  .msg.ai h3 { font-size: 14px; }
  .msg.ai p { margin: 6px 0; }
  .msg.ai ul, .msg.ai ol { padding-left: 20px; margin: 6px 0; }
  .msg.ai li { margin: 2px 0; }
  .msg.ai table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 12px; }
  .msg.ai th, .msg.ai td { border: 1px solid var(--border); padding: 6px 8px; text-align: left; }
  .msg.ai th { background: var(--surface2); font-weight: 600; }
  .msg.ai code { background: var(--surface2); padding: 2px 6px; border-radius: 4px; font-size: 12px; }
  .msg.ai pre { background: #0a0d1a; padding: 12px; border-radius: 8px; overflow-x: auto; margin: 8px 0; }
  .msg.ai pre code { background: none; padding: 0; }
  .msg.ai blockquote { border-left: 3px solid var(--accent); padding-left: 12px; margin: 8px 0; color: var(--text2); }
  .msg.ai hr { border: none; border-top: 1px solid var(--border); margin: 12px 0; }
  .msg.ai strong { color: var(--accent2); }
  .msg.ai em { color: var(--yellow); }

  /* ── 光标闪烁 ── */
  .cursor::after {
    content: '▌'; animation: blink 1s step-end infinite; color: var(--accent2);
  }
  @keyframes blink { 50% { opacity: 0; } }

  /* ── 输入区 ── */
  #input-area {
    background: var(--surface); border-top: 1px solid var(--border);
    padding: 12px 16px; display: flex; gap: 10px; align-items: flex-end;
    flex-shrink: 0;
  }
  #input {
    flex: 1; padding: 10px 14px; border-radius: 10px;
    border: 1px solid var(--border); background: var(--bg); color: var(--text);
    font-size: 14px; outline: none; font-family: inherit; resize: none;
    max-height: 120px; line-height: 1.5;
  }
  #input:focus { border-color: var(--accent); }
  #input::placeholder { color: #4a5580; }
  #send {
    width: 44px; height: 44px; border-radius: 10px; border: none;
    background: var(--accent); color: white; font-size: 20px;
    cursor: pointer; display: flex; align-items: center; justify-content: center;
    flex-shrink: 0; transition: background .2s;
  }
  #send:hover { background: var(--accent2); }
  #send:disabled { background: var(--border); cursor: not-allowed; opacity: .5; }

  /* ── 历史对话列表 ── */
  #sidebar {
    width: 0; overflow: hidden; background: var(--surface);
    border-right: 1px solid transparent; flex-shrink: 0;
    display: flex; flex-direction: column; height: 100%;
    transition: width .25s ease, border-color .25s ease;
  }
  #sidebar.open {
    width: 280px; border-right-color: var(--border); overflow: visible;
  }
  #sidebar .head { padding: 14px 16px; border-bottom: 1px solid var(--border); font-weight: 600; font-size: 14px; white-space: nowrap; flex-shrink: 0; }
  #sidebar .list { flex: 1 1 0; min-height: 0; overflow-y: auto; padding: 6px; }
  #sidebar .item {
    padding: 8px 10px; border-radius: 8px; cursor: pointer; font-size: 13px;
    margin-bottom: 4px; display: flex; align-items: center; gap: 6px;
    transition: background .15s;
  }
  #sidebar .item:hover { background: var(--bg); }
  #sidebar .item.active { background: var(--accent); color: #fff; }
  #sidebar .item .title {
    flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  #sidebar .item .count {
    font-size: 11px; color: var(--text2); flex-shrink: 0;
  }
  #sidebar .item.active .count { color: rgba(255,255,255,.6); }
  #sidebar .item .acts { display: none; gap: 2px; flex-shrink: 0; }
  #sidebar .item:hover .acts { display: flex; }
  #sidebar .item .acts button {
    background: none; border: none; color: var(--text2); cursor: pointer;
    font-size: 13px; padding: 2px 4px; border-radius: 4px; line-height: 1;
  }
  #sidebar .item .acts button:hover { background: var(--surface2); color: var(--text); }
  #sidebar .item .acts button.danger:hover { background: #7f1d1d; color: #fca5a5; }

  @media (max-width: 640px) {
    .msg { max-width: 92%; }
    #sidebar.open { width: 80%; position: absolute; z-index: 10; height: 100%; }
  }
</style>
</head>
<body>

<!-- 侧栏 -->
<div id="sidebar">
  <div class="head">📋 历史对话 <span style="float:right;font-size:12px;color:var(--text2);cursor:pointer" id="btnNewConv">＋ 新建</span></div>
  <div class="list" id="convList"></div>
</div>

<div id="main">
<header>
  <span style="cursor:pointer;font-size:20px" onclick="toggleSidebar()">☰</span>
  <span class="logo">📊</span>
  <h1>A股分析助手</h1>
  <span class="badge">WebSocket</span>
  <div class="status">
    <span class="dot" id="statusDot"></span>
    <span id="statusText">连接中...</span>
  </div>
</header>

<div id="chat">
  <div class="empty">
    <span class="big">📊</span>
    输入股票名称开始分析<br>
    <span class="hint">支持：赛力斯、茅台、宁德时代、板块排行、概念板块...</span>
  </div>
</div>

<!-- 输入 -->
<div id="input-area">
  <textarea id="input" rows="1" placeholder="输入消息..." autofocus></textarea>
  <button id="send" onclick="send()">➤</button>
</div>

</div>
<script>
// ═══════════════════════════════════════════
// 状态
// ═══════════════════════════════════════════

let ws = null;
let sessionId = localStorage.getItem('astock_session') || Date.now().toString(36) + Math.random().toString(36).slice(2,8);
localStorage.setItem('astock_session', sessionId);
let busy = false;

const chat = document.getElementById('chat');
const input = document.getElementById('input');
const sendBtn = document.getElementById('send');
const statusDot = document.getElementById('statusDot');
const statusText = document.getElementById('statusText');
const convList = document.getElementById('convList');

// ═══════════════════════════════════════════
// WebSocket
// ═══════════════════════════════════════════

function connect() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws/${sessionId}`);

  ws.onopen = () => { setStatus(true); };
  ws.onclose = () => { setStatus(false); if (!busy) setTimeout(connect, 3000); };
  ws.onmessage = e => {
    try {
      const data = JSON.parse(e.data);
      switch (data.type) {
        case 'tool': addToolBubble(data.content); break;
        case 'token': streamToken(data.content); break;
        case 'done': finishStream(); break;
        case 'error': showError(data.content); break;
      }
    } catch(_) {}
  };
}

function setStatus(connected) {
  statusDot.className = 'dot ' + (connected ? 'connected' : 'disconnected');
  statusText.textContent = connected ? '已连接' : '断开';
}

// ═══════════════════════════════════════════
// 消息渲染
// ═══════════════════════════════════════════

let currentBubble = null;
let emptyHint = null;

function scrollBottom() { chat.scrollTop = chat.scrollHeight; }

function addUserBubble(text) {
  removeEmpty();
  const div = document.createElement('div');
  div.className = 'msg user';
  div.textContent = text;
  chat.appendChild(div);
  scrollBottom();
}

function addToolBubble(name) {
  let text = `🔧 ${name}`;
  // 如果最后一个气泡已经是 tool 且正在旋转，追加
  const last = chat.lastElementChild;
  if (last && last.classList.contains('tool') && last.dataset.tool) {
    last.textContent += ' → ' + name.split('(')[0];
    scrollBottom();
    return;
  }
  const div = document.createElement('div');
  div.className = 'msg tool';
  div.dataset.tool = '1';
  div.innerHTML = `<span class="spin"></span> ${text}`;
  chat.appendChild(div);
  scrollBottom();
}

function startAssistantBubble() {
  removeEmpty();
  if (currentBubble && currentBubble.classList.contains('cursor')) return currentBubble;
  const div = document.createElement('div');
  div.className = 'msg ai cursor';
  div.innerHTML = '<div class="label">🤖 分析结果</div>';
  chat.appendChild(div);
  currentBubble = div;
  scrollBottom();
  return div;
}

function streamToken(text) {
  const div = startAssistantBubble();
  // 取出当前文本（不含光标）
  let html = div.innerHTML;
  // 移除末尾的 ▌ 光标
  if (div.classList.contains('cursor')) {
    html = html.replace(/▌$/, '');
  }
  // 追加新内容，做简易 markdown 分行渲染
  const newText = text.replace(/</g, '&lt;').replace(/>/g, '&gt;');
  // 保留已有的 .label，追加内容
  const labelEnd = html.indexOf('</div>') + 6;
  const existingContent = html.slice(labelEnd);
  html = html.slice(0, labelEnd) + existingContent + newText;
  div.innerHTML = html + '▌';
  scrollBottom();
}

function finishStream() {
  if (currentBubble) {
    let html = currentBubble.innerHTML;
    html = html.replace(/▌$/, '');
    // 渲染 markdown
    currentBubble.innerHTML = '<div class="label">🤖 分析结果</div>' + renderMarkdown(extractContent(html));
    currentBubble.classList.remove('cursor');
    currentBubble = null;
  }
  // 清除所有 tool 气泡
  document.querySelectorAll('.msg.tool').forEach(el => el.remove());
  busy = false;
  sendBtn.disabled = false;
  input.disabled = false;
  input.focus();
  loadConversations();  // 刷新侧栏（标题可能已更新）
}

function extractContent(html) {
  const idx = html.indexOf('</div>');
  return idx >= 0 ? html.slice(idx + 6) : html;
}

function renderMarkdown(text) {
  // 简易 markdown 渲染
  let t = text
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/### (.+)/g, '<h3>$1</h3>')
    .replace(/## (.+)/g, '<h2>$1</h2>')
    .replace(/# (.+)/g, '<h1>$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
    .replace(/`(.+?)`/g, '<code>$1</code>')
    .replace(/^\|(.+)\|$/gm, (m) => { return m; })  // 表格行保留原样
    .replace(/\n---+\n/g, '\n<hr>\n')
    .replace(/^> (.+)/gm, '<blockquote>$1</blockquote>')
    .replace(/^- (.+)/gm, '<li>$1</li>')
    .replace(/\n\n/g, '</p><p>')
    .replace(/\n/g, '<br>');
  
  // 简化表格渲染：先识别表格块
  const lines = text.split('\n');
  let inTable = false, tableHtml = '', result = [];
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (l.startsWith('|') && l.endsWith('|') && !l.includes('---')) {
      if (!inTable) { inTable = true; tableHtml = '<table>'; }
      const cells = l.split('|').filter(c => c.trim()).map(c => c.trim());
      if (i > 0 && lines[i-1].includes('---')) {
        // 跳过表头分隔行
        continue;
      }
      // 判断是否是表头（上一条是分隔行或者第一行）
      const isHeader = i === 0 || (i > 0 && lines[i-1].includes('---'));
      const tag = isHeader ? 'th' : 'td';
      tableHtml += '<tr>' + cells.map(c => `<${tag}>${c}</${tag}>`).join('') + '</tr>';
      if (i+1 >= lines.length || !lines[i+1].startsWith('|')) {
        inTable = false; tableHtml += '</table>';
        result.push(tableHtml);
      }
    } else if (!inTable) {
      result.push(l);
    }
  }
  return result.join('\n');
}

function removeEmpty() {
  const empty = chat.querySelector('.empty');
  if (empty) empty.remove();
}

function showError(msg) {
  finishStream();
  const div = document.createElement('div');
  div.className = 'msg ai';
  div.style.borderColor = '#ef4444';
  div.innerHTML = `<div class="label" style="color:#ef4444">❌ 错误</div>${msg}`;
  chat.appendChild(div);
  scrollBottom();
}

// ═══════════════════════════════════════════
// 发送
// ═══════════════════════════════════════════

function send() {
  const text = input.value.trim();
  if (!text || busy || !ws || ws.readyState !== WebSocket.OPEN) return;
  input.value = '';
  input.style.height = 'auto';
  busy = true;
  sendBtn.disabled = true;
  input.disabled = true;
  addUserBubble(text);
  ws.send(JSON.stringify({type: 'message', content: text}));
}

// 自动调整输入框高度
input.addEventListener('input', () => {
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 120) + 'px';
});
input.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
});

// ═══════════════════════════════════════════
// 侧栏
// ═══════════════════════════════════════════

function toggleSidebar() {
  const sb = document.getElementById('sidebar');
  sb.classList.toggle('open');
  if (sb.classList.contains('open')) {
    loadConversations();
  }
}

async function loadConversations() {
  try {
    const resp = await fetch('/api/conversations');
    const convs = await resp.json();
    convList.innerHTML = '';
    convs.forEach(c => {
      const div = document.createElement('div');
      div.className = 'item' + (c.id === sessionId ? ' active' : '');
      div.innerHTML =
        '<span class="title" onclick="switchConversation(\'' + c.id + '\')">' + esc(c.title) + '</span>' +
        '<span class="count">' + c.msg_count + '</span>' +
        '<span class="acts">' +
          '<button title="重命名" onclick="event.stopPropagation();renameConversation(\'' + c.id + '\',\'' + escJs(c.title) + '\')">✏️</button>' +
          '<button title="删除" class="danger" onclick="event.stopPropagation();deleteConversation(\'' + c.id + '\')">🗑</button>' +
        '</span>';
      convList.appendChild(div);
    });
  } catch(e) { console.error('加载对话列表失败', e); }
}

function esc(s) { const d=document.createElement('div');d.textContent=s;return d.innerHTML; }
function escJs(s) { return s.replace(/\\/g,'\\\\').replace(/'/g,"\\'").replace(/"/g,'\\"'); }

async function switchConversation(id) {
  if (id === sessionId) { toggleSidebar(); return; }
  sessionId = id;
  localStorage.setItem('astock_session', sessionId);
  chat.innerHTML = '';
  try {
    const resp = await fetch('/api/conversations/' + id + '/messages');
    const msgs = await resp.json();
    if (msgs.length === 0) {
      chat.innerHTML = '<div class="empty"><span class="big">📊</span>输入股票名称开始分析<br><span class="hint">支持：赛力斯、茅台、宁德时代、板块排行、概念板块...</span></div>';
    } else {
      msgs.forEach(m => {
        if (m.role === 'user') addUserBubble(m.content);
        else if (m.role === 'ai' || m.role === 'system') {
          const div = document.createElement('div');
          div.className = 'msg ai';
          div.innerHTML = '<div class="label">🤖 分析结果</div>' + renderMarkdown(m.content.replace(/</g,'&lt;').replace(/>/g,'&gt;'));
          chat.appendChild(div);
        }
      });
      scrollBottom();
    }
  } catch(e) { console.error('加载消息失败', e); }
  ws.close();
  connect();
}

async function deleteConversation(id) {
  if (!confirm('确定删除这个对话？')) return;
  try {
    await fetch('/api/conversations/' + id, { method: 'DELETE' });
    if (id === sessionId) {
      const resp = await fetch('/api/conversations');
      const convs = await resp.json();
      if (convs.length > 0) {
        switchConversation(convs[0].id);
      } else {
        newConversation();
      }
    } else {
      loadConversations();
    }
  } catch(e) { console.error('删除失败', e); }
}

async function renameConversation(id, oldTitle) {
  const name = prompt('对话名称：', oldTitle);
  if (!name || name === oldTitle) return;
  try {
    await fetch('/api/conversations/' + id + '/title', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: name }),
    });
    loadConversations();
  } catch(e) { console.error('重命名失败', e); }
}

async function newConversation() {
  try {
    const resp = await fetch('/api/conversations', { method: 'POST' });
    const data = await resp.json();
    switchConversation(data.id);
  } catch(e) { console.error('创建对话失败', e); }
}

document.getElementById('btnNewConv').addEventListener('click', newConversation);

// ═══════════════════════════════════════════
// 启动
// ═══════════════════════════════════════════

connect();
</script>
</body>
</html>"""


# ═══════════════════════════════════════════
# WebSocket 端点
# ═══════════════════════════════════════════

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(ws: WebSocket, session_id: str):
    await ws.accept()
    storage = ChatStorage()

    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") != "message":
                continue
            text = data["content"]

            # 确保对话存在（用 session_id 作为对话 ID）
            conv = storage.get_conversation(session_id)
            if not conv:
                with storage._connect() as conn:
                    conn.execute(
                        "INSERT INTO conversations (id, title) VALUES (?, ?)",
                        (session_id, "新对话"),
                    )
                # 首次创建：写入 system prompt
                from prompts import build_react_system_prompt
                sp = build_react_system_prompt(
                    role="analyst",
                    include_rules=["base", "risk", "stock_resolve"],
                    include_skills=["trend", "indicator", "volume"],
                )
                storage.save_message(session_id, SystemMessage(content=sp))

            history = storage.get_messages(session_id)

            user_msg = HumanMessage(content=text)
            storage.save_message(session_id, user_msg)
            history.append(user_msg)

            # 第一条用户消息 → 自动设为对话标题
            if conv and conv.get("title") == "新对话":
                title = text[:30] + ("..." if len(text) > 30 else "")
                storage.update_title(session_id, title)

            new_msgs = []
            full_reply = ""

            for chunk in agent.stream({"messages": history}, stream_mode="updates"):
                for node_name, value in chunk.items():
                    if not isinstance(value, dict) or "messages" not in value:
                        continue
                    for msg in value["messages"]:
                        new_msgs.append(msg)
                        if isinstance(msg, AIMessage) and msg.tool_calls:
                            for tc in msg.tool_calls:
                                await ws.send_json({"type": "tool", "content": f"{tc['name']}(...)"})
                        elif isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
                            await ws.send_json({"type": "token", "content": msg.content})
                            full_reply += msg.content

            if new_msgs:
                storage.save_messages(session_id, new_msgs)
                history.extend(new_msgs)

            # 历史压缩：整替换 DB，避免旧消息残留
            if estimate_tokens(history) > _COMPRESSION_AT:
                history = compress_history(history, summary_llm)
                storage.replace_all_messages(session_id, history)

            await ws.send_json({"type": "done"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "content": str(e)})
        except Exception:
            pass


# ═══════════════════════════════════════════
# REST API — 对话管理
# ═══════════════════════════════════════════

from pydantic import BaseModel


class RenameRequest(BaseModel):
    title: str


@app.get("/api/conversations")
async def list_conversations():
    """列出所有对话"""
    storage = ChatStorage()
    convs = storage.list_conversations(limit=200)
    return [
        {"id": c["id"], "title": c["title"], "msg_count": c["msg_count"],
         "updated_at": c["updated_at"]}
        for c in convs
    ]


@app.post("/api/conversations")
async def create_conversation():
    """创建新对话"""
    storage = ChatStorage()
    conv_id = storage.create_conversation()
    from prompts import build_react_system_prompt
    sp = build_react_system_prompt(
        role="analyst",
        include_rules=["base", "risk", "stock_resolve"],
        include_skills=["trend", "indicator", "volume"],
    )
    storage.save_message(conv_id, SystemMessage(content=sp))
    return {"id": conv_id, "title": "新对话"}


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    """删除对话"""
    storage = ChatStorage()
    storage.delete_conversation(conv_id)
    return {"ok": True}


@app.put("/api/conversations/{conv_id}/title")
async def rename_conversation(conv_id: str, body: RenameRequest):
    """重命名对话"""
    storage = ChatStorage()
    storage.update_title(conv_id, body.title)
    return {"ok": True}


@app.get("/api/conversations/{conv_id}/messages")
async def get_messages(conv_id: str):
    """加载对话的全部消息（用于切换对话时恢复历史）"""
    storage = ChatStorage()
    msgs = storage.get_messages(conv_id)
    result = []
    for m in msgs:
        if isinstance(m, SystemMessage) and "前情摘要" not in (m.content or ""):
            continue  # 前端不需要显示 system prompt
        role = "system" if isinstance(m, SystemMessage) else \
               "user" if isinstance(m, HumanMessage) else \
               "ai" if isinstance(m, AIMessage) else "tool"
        content = m.content or ""
        if isinstance(m, AIMessage) and not content:
            continue  # 跳过纯 tool_calls 的 AI 消息
        result.append({"role": role, "content": content})
    return result


@app.get("/")
async def index():
    return HTMLResponse(HTML)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

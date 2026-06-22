# Session Memory — Design Spec

> **Date:** 2026-06-21
> **Status:** Approved, ready for implementation
> **Build order:** First of 4 student features (session memory → session travel → quiz generation → chapter summaries)

## Purpose

Save every chat message (user + assistant) to the database so the conversation survives browser refresh, closing the tab, clearing browser data, and backend restarts. Today the chat lives only in the browser's Pinia store (RAM) — a refresh wipes it. This makes it persistent.

This is the foundation for session travel (multiple named threads): when that feature lands, the `chat_messages` table gains a `session_id` column and a `chat_sessions` table, and this single thread becomes "the default session."

## Scope

**In scope:**
- New `chat_messages` table (migration + ORM model)
- `GET /api/chat/history` (load all messages, oldest-first)
- `POST /api/chat/history` (save a batch: user message + assistant message + citations)
- `DELETE /api/chat/history` (clear all)
- Frontend: store loads history on mount, saves after each Q&A turn, "Clear chat" button with confirm

**Out of scope (deferred to session travel):**
- Multiple sessions / `session_id`
- Session list / switching sidebar
- Session titles / auto-naming
- Message editing or deletion of individual messages

## Database

### New table: `chat_messages`

```sql
CREATE TABLE chat_messages (
    id          SERIAL PRIMARY KEY,
    role        VARCHAR(20) NOT NULL,          -- 'user' | 'assistant'
    content     TEXT NOT NULL,                 -- the message text
    citations   JSONB,                         -- [{filename, page_number, excerpt}, ...]; NULL for user messages
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX chat_messages_created_at_idx ON chat_messages(created_at);
```

`citations` is JSONB so the full citation objects (excerpt + page + filename) are stored per assistant message. On reload, the frontend restores hover popups and citation chips without re-fetching — the data round-trips intact.

### Migration

One Alembic migration (`alembic revision -m "add chat_messages table"`). The table is created fresh — no data migration needed (existing chat is in RAM only, nothing to port).

## Backend

### ORM model (`app/db/models.py`)

```python
class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

### Pydantic schemas (`app/models/chat.py`)

```python
class ChatMessageRead(BaseModel):
    """One message as returned by GET /api/chat/history."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    role: str
    content: str
    citations: list[dict] | None = None
    created_at: datetime

class ChatMessageBatch(BaseModel):
    """Batch save: POST /api/chat/history accepts a list of these."""
    messages: list[ChatMessageCreate]

class ChatMessageCreate(BaseModel):
    role: str  # 'user' | 'assistant'
    content: str
    citations: list[dict] | None = None
```

### Router (`app/api/chat_history.py`)

Three endpoints, all thin (call service, shape response):

- `GET /api/chat/history` → `SELECT * FROM chat_messages ORDER BY created_at ASC` → return `{"messages": [ChatMessageRead, ...]}`
- `POST /api/chat/history` → accept `ChatMessageBatch`, INSERT all rows, return `{"saved": N}`
- `DELETE /api/chat/history` → `DELETE FROM chat_messages` → 204

No service layer needed — these are direct CRUD on one table. The handlers are 5 lines each.

### What stays unchanged

`POST /api/chat` (streaming) is **not touched**. It streams exactly as before. Saving happens in the frontend store AFTER the stream completes — the store calls `POST /api/chat/history` with the finished user + assistant messages. This keeps the streaming path clean and avoids coupling SSE to DB writes.

## Frontend

### `api/client.js` — three new functions

```js
export async function loadChatHistory() {
  const { data } = await http.get('/api/chat/history')
  return data.messages
}
export async function saveChatMessages(messages) {
  await http.post('/api/chat/history', { messages })
}
export async function clearChatHistory() {
  await http.delete('/api/chat/history')
}
```

### `stores/chat.js` — three changes

1. **On store init:** call `loadChatHistory()` → populate `messages`. If empty, the empty state shows.
2. **After stream completes** (in `onCitations` / `onFinally`): call `saveChatMessages([userMsg, assistantMsg])` to persist both messages with citations. Fire-and-forget (don't block the UI on the save).
3. **New action `clearHistory()`:** calls `clearChatHistory()` API + empties `messages` array.

### `ChatInterface.vue` — UI changes

1. **On mount:** the store already loads history (change #1 above). No template change needed — if `messages` is populated, they render.
2. **"Clear chat" button:** a trash icon above the composer. On click → confirm dialog → `chat.clearHistory()`. Hidden when there are no messages.

## Data flow

```
User asks question
  → POST /api/chat (SSE streaming, UNCHANGED)
  → tokens stream into assistant message
  → done event fires (citations arrive)
  → store calls POST /api/chat/history with [{role:'user', content:q}, {role:'assistant', content:answer, citations}]
  → DB saves both rows

User refreshes browser
  → store init → GET /api/chat/history
  → messages restored in order with citations
  → hover popups + chips work on restored messages

User clicks "Clear chat"
  → confirm → DELETE /api/chat/history → store empties
```

## Error handling

- `GET /api/chat/history` fails → store starts empty (chat still works, just no history). Log the error, don't crash.
- `POST /api/chat/history` fails → the message was already shown to the user (streaming completed); only persistence failed. Show a subtle "⚠ not saved" indicator but don't block. The message is in the Pinia store for the current session.
- `DELETE /api/chat/history` fails → show the error, don't clear the UI.

## Testing

- Migration: `alembic upgrade head` creates `chat_messages` cleanly
- `GET /api/chat/history` returns messages in created_at order
- `POST /api/chat/history` saves a batch with JSONB citations
- `DELETE /api/chat/history` wipes all rows
- Frontend: ask a question → refresh → chat restored with citations working
- Frontend: clear chat → empty state shows → new questions work

## Future: session travel upgrade path

When session travel lands:
1. Add `session_id` FK column to `chat_messages`
2. Add `chat_sessions` table (id, title, created_at, updated_at)
3. The existing single thread becomes `session_id = 1` (or the first created session)
4. `GET /api/chat/history?session_id=N` scopes to one thread
5. Frontend: session list sidebar + switching

No rewrite needed — the table grows a column, the endpoints gain a query param.

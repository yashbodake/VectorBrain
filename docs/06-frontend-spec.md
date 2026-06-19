# 06 — Frontend Spec (Vue.js 3)

## Project Setup

```bash
npm create vite@latest vectorbrain-frontend -- --template vue
cd vectorbrain-frontend
npm install pinia axios
```

Use `<script setup>` Composition API throughout. No Options API.

## Component Tree

```
App.vue
├── DocumentManager.vue
│   └── DocumentCard.vue        (one per document, status badge, delete button)
│   └── FileUploader.vue        (drag/drop + file picker, multi-file)
└── ChatInterface.vue
    └── MessageBubble.vue       (one per Q&A turn)
    └── CitationChip.vue        (one per citation under an answer)
```

### `App.vue` (Parent)

- Two-panel layout: `DocumentManager` on the left, `ChatInterface` on the right (matches the reference mockup in `docs/01-product-overview.md`).
- No business logic here beyond layout — state lives in Pinia stores, not in `App.vue`.

### `DocumentManager.vue`

- On mount, calls `documentsStore.fetchDocuments()`
- Renders the document count (e.g., "Uploaded Documents — 3")
- Renders a `DocumentCard` per document
- Renders `FileUploader` at the bottom for adding more
- While any document has `status: 'processing'`, poll `GET /api/documents` every ~2 seconds (clear the interval when nothing is processing anymore — don't poll forever)

### `DocumentCard.vue`

Props: `document` object (`id`, `filename`, `page_count`, `file_size_bytes`, `status`).

- Shows filename, page count + file size (e.g., "245 pages • 4.2 MB" — blank/placeholder while `page_count` is null and status is `uploaded`/`processing`)
- Status badge: visually distinct states for `uploaded`, `processing`, `ready`, `failed` (e.g., color-coded — exact styling is the implementer's call, see `frontend-design` conventions in `.claude/rules/coding-conventions.md`)
- If `status === 'failed'`, show the error message (truncated, with a way to see the full message — e.g., a tooltip) and allow delete
- Delete button calls `documentsStore.deleteDocument(id)`, with a confirm step (don't delete on a single misclick)

### `FileUploader.vue`

- Accepts multiple files at once (`<input type="file" multiple accept="application/pdf">` plus drag-and-drop)
- For each file: validate it's a PDF client-side (extension/MIME check) before upload — fast feedback, doesn't replace server-side validation
- Calls `documentsStore.uploadDocument(file)` per file; uploads can run concurrently, each tracked independently so one slow upload doesn't block the others in the UI
- Show per-file upload progress if feasible; at minimum show an "uploading..." state until the server responds with the created `documents` row

### `ChatInterface.vue`

- On mount, calls nothing special — chat starts empty (no persisted history in Lite scope, per `docs/01-product-overview.md`)
- Renders the message thread (`MessageBubble` per turn) in a scrollable area, auto-scrolls to bottom on new content
- Text input + send button at the bottom, placeholder text: "Ask a question across all documents..."
- Disable the send button (or show a clear empty/disabled state) if there are zero `ready` documents yet — don't let the user fire a question into a void with no helpful response
- On submit: appends a user `MessageBubble`, opens the SSE connection to `POST /api/chat`, appends an assistant `MessageBubble` that fills in as `token` events arrive, attaches `CitationChip`s when the `done` event arrives
- Handle the `error` SSE event by showing an inline error state on that message bubble (e.g., "Something went wrong — try again") rather than leaving it stuck mid-stream

### `MessageBubble.vue`

Props: `role` (`'user' | 'assistant'`), `content` (string, may update incrementally while streaming), `citations` (array, optional), `isStreaming` (boolean).

- Visually distinguish user vs. assistant turns
- While `isStreaming` is true, show a subtle typing/streaming indicator
- Renders `CitationChip` per citation below the message content once streaming completes

### `CitationChip.vue`

Props: `filename`, `pageNumber` (nullable).

- Renders something like "Source: Physics_Textbook.pdf, p. 45" (omit the page portion gracefully if `pageNumber` is null, per the schema note in `docs/03-database-schema.md`)

## Pinia Stores

### `stores/documents.js`

```js
export const useDocumentsStore = defineStore('documents', {
  state: () => ({
    documents: [],   // array of document objects from the API
    loading: false,
  }),
  actions: {
    async fetchDocuments() { /* GET /api/documents, set this.documents */ },
    async uploadDocument(file) { /* POST /api/documents (multipart), push result into this.documents */ },
    async deleteDocument(id) { /* DELETE /api/documents/{id}, remove from this.documents */ },
    hasProcessingDocuments() { /* getter-style helper: any doc with status processing/uploaded */ },
    readyDocumentCount() { /* count of status === 'ready' */ },
  },
})
```

### `stores/chat.js`

```js
export const useChatStore = defineStore('chat', {
  state: () => ({
    messages: [],   // [{ role, content, citations, isStreaming }]
  }),
  actions: {
    async sendMessage(question) {
      // 1. push a user message
      // 2. push an empty assistant message with isStreaming: true
      // 3. open SSE connection to POST /api/chat
      // 4. on each 'token' event, append text to the assistant message's content
      // 5. on 'done', set citations, isStreaming = false
      // 6. on 'error', set an error flag on the message, isStreaming = false
    },
  },
})
```

## API Client

Centralize API calls in `src/api/client.js` (base URL from `import.meta.env.VITE_API_BASE_URL`, default `http://localhost:8000/api`). Don't scatter raw `fetch`/`axios` calls across components — stores call the client, components call stores.

For the streaming chat call specifically, native `fetch` with a `ReadableStream` reader (or the `EventSource` API if the backend exposes a GET-based SSE variant) is simpler than pulling in an extra SSE library for this scope — implementer's choice, document whichever is used in a code comment since it affects how `POST /api/chat` needs to be called from the browser (note: native `EventSource` only supports GET, so if using it, the backend would need a GET variant with the question as a query param — otherwise stick with `fetch` + manual stream parsing for SSE-over-POST).

## Environment

`.env` (Vite, frontend-specific, separate from the backend's `.env`):
```
VITE_API_BASE_URL=http://localhost:8000/api
```

## States to Handle (don't skip these — this is most of what makes the UI feel solid)

- Zero documents uploaded yet (empty state in `DocumentManager`, chat disabled)
- Documents uploading
- Documents processing (parsing/embedding in progress)
- Documents ready
- Document failed (with visible reason)
- Chat: no ready documents yet → input disabled with an explanatory placeholder/tooltip
- Chat: question submitted, streaming in progress
- Chat: streaming finished, citations shown
- Chat: error mid-stream

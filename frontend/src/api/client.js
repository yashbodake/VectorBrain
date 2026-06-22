// Centralized API client. Components never call fetch/axios directly — they go
// through the Pinia stores, which use this module (coding-conventions.md).
//
// REST calls use axios (base URL from VITE_API_BASE_URL, default '' = same
// origin via the Vite dev proxy). The chat call is SSE-over-POST, which axios
// doesn't stream cleanly, so it uses native fetch + a ReadableStream reader
// instead — no extra SSE library needed (docs/06 implementer's choice).

import axios from 'axios'

// '' base -> relative URLs -> Vite dev proxy handles /api in dev; in prod the
// static assets are served alongside the API so same-origin still works.
const baseURL = import.meta.env.VITE_API_BASE_URL || ''

export const http = axios.create({ baseURL })

// --- Documents ---------------------------------------------------------------
export async function listDocuments() {
  const { data } = await http.get('/api/documents')
  return data.documents
}

export async function uploadDocument(file, { onUploadProgress } = {}) {
  const form = new FormData()
  form.append('file', file)
  const { data } = await http.post('/api/documents', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress,
  })
  return data
}

export async function deleteDocument(id) {
  await http.delete(`/api/documents/${id}`)
}

// --- Chat history (session memory) -----------------------------------------
export async function loadChatHistory(sessionId = 1) {
  const { data } = await http.get('/api/chat/history', { params: { session_id: sessionId } })
  return data.messages
}

export async function saveChatMessages(messages, sessionId = 1) {
  await http.post('/api/chat/history', { messages, session_id: sessionId })
}

export async function clearChatHistory(sessionId = 1) {
  await http.delete('/api/chat/history', { params: { session_id: sessionId } })
}

// --- Chat sessions (session travel) ----------------------------------------
export async function listSessions() {
  const { data } = await http.get('/api/chat/sessions')
  return data.sessions
}

export async function createSession(title = 'New session') {
  const { data } = await http.post('/api/chat/sessions', { title })
  return data
}

export async function renameSession(id, title) {
  const { data } = await http.patch(`/api/chat/sessions/${id}`, { title })
  return data
}

export async function deleteSession(id) {
  await http.delete(`/api/chat/sessions/${id}`)
}

// --- Quiz (active recall study tool) ---------------------------------------
export async function generateQuiz(documentId, n = 5) {
  const { data } = await http.post(`/api/documents/${documentId}/quiz`, null, { params: { n } })
  return data
}

export async function getQuiz(documentId) {
  const { data } = await http.get(`/api/documents/${documentId}/quiz`)
  return data
}

export async function answerQuizQuestion(questionId, selectedIndex) {
  const { data } = await http.post(`/api/quiz/${questionId}/answer`, { selected_index: selectedIndex })
  return data
}

export async function getQuizScore(documentId) {
  const { data } = await http.get('/api/quiz/score', { params: { document_id: documentId } })
  return data
}

// --- Chapter summaries (cached per-section overviews) ----------------------
export async function generateSummaries(documentId) {
  const { data } = await http.post(`/api/documents/${documentId}/summarize`)
  return data
}

export async function getSummaries(documentId) {
  const { data } = await http.get(`/api/documents/${documentId}/summaries`)
  return data
}

// --- Chat (SSE over POST) ----------------------------------------------------
// Parses the SSE stream from POST /api/chat manually. Why not EventSource?
// EventSource only supports GET; our endpoint is POST with a JSON body, so we
// use fetch + ReadableStream and parse the `event:`/`data:` frames ourselves.
//
// `handlers` get called as events arrive:
//   onToken(text)            — per `event: token`
//   onCitations(citations)   — per `event: done`
//   onError(message)         — per `event: error`
//   onFinally()              — always, after the stream ends (any reason)
//
// Returns an AbortController so the caller can cancel a mid-stream answer.
export function streamChat(question, handlers, documentIds = null) {
  const controller = new AbortController()

  ;(async () => {
    let resp
    try {
      // Build the body. When documentIds is a non-empty array, include it to
      // scope retrieval (Feature 3); omit it entirely otherwise so old
      // backends / backward-compatible curl still work.
      const body = { question }
      if (Array.isArray(documentIds) && documentIds.length) {
        body.document_ids = documentIds
      }
      resp = await fetch(`${baseURL}/api/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      })
    } catch (err) {
      // Network error / abort before any response.
      if (controller.signal.aborted) {
        handlers.onFinally?.()
        return
      }
      handlers.onError?.(
        "Couldn't reach the server. Is the backend running?",
      )
      handlers.onFinally?.()
      return
    }

    if (!resp.ok || !resp.body) {
      // Non-2xx: try to read the JSON error detail, else generic message.
      let detail = `Request failed (HTTP ${resp.status})`
      try {
        const body = await resp.json()
        if (body?.detail) detail = body.detail
      } catch {
        /* not JSON; keep generic detail */
      }
      handlers.onError?.(detail)
      handlers.onFinally?.()
      return
    }

    // Decode + buffer the byte stream into SSE frames separated by blank lines.
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    try {
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // Frames are separated by a blank line (\n\n). Process complete frames.
        let sep
        while ((sep = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, sep)
          buffer = buffer.slice(sep + 2)
          handleFrame(frame, handlers)
        }
      }
      // Flush any trailing frame without a terminal blank line.
      if (buffer.trim()) handleFrame(buffer, handlers)
    } catch (err) {
      if (controller.signal.aborted) {
        // User cancelled — not an error to surface.
      } else {
        handlers.onError?.('The connection was interrupted mid-stream.')
      }
    } finally {
      handlers.onFinally?.()
    }
  })()

  return controller
}

// Parse one SSE frame (may contain `event:` and `data:` lines) and dispatch.
function handleFrame(frame, handlers) {
  let event = 'message'
  let dataLine = ''
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLine = line.slice(5).trim()
  }
  if (!dataLine) return
  let payload
  try {
    payload = JSON.parse(dataLine)
  } catch {
    return // malformed frame; ignore rather than crash the stream
  }
  switch (event) {
    case 'token':
      handlers.onToken?.(payload.text ?? '')
      break
    case 'done':
      handlers.onCitations?.(payload.citations ?? [], payload.sources ?? [])
      break
    case 'error':
      handlers.onError?.(payload.message ?? 'Something went wrong.')
      break
    default:
      break
  }
}

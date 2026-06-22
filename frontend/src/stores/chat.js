// Chat store. Owns the message thread and the streaming chat call.
//
// A message object: { id, role: 'user'|'assistant', content, citations, isStreaming, error }
// The assistant message is created empty with isStreaming:true, then filled in
// as token events arrive — that's what makes the answer appear live.
//
// SESSION MEMORY: messages persist to the DB via /api/chat/history. On mount,
// loadHistory() restores the conversation. After each Q&A turn, both messages
// (user + assistant with citations) are saved. See docs/superpowers/specs/
// 2026-06-21-session-memory-design.md.

import { defineStore } from 'pinia'
import { streamChat, loadChatHistory, saveChatMessages, clearChatHistory } from '../api/client'

let nextId = 1
const makeId = () => nextId++

export const useChatStore = defineStore('chat', {
  state: () => ({
    messages: [], // [{ id, role, content, citations, sources, isStreaming, error }]
    currentController: null, // AbortController for the in-flight stream
    currentSessionId: 1, // the active chat session (session travel)
    historyLoaded: false, // true after the first loadHistory() for current session
  }),

  getters: {
    isStreaming: (state) =>
      state.messages.some((m) => m.role === 'assistant' && m.isStreaming),
  },

  actions: {
    // Switch to a different session: clear the UI, mark history as unloaded,
    // load the new session's messages.
    async switchSession(sessionId) {
      this.currentSessionId = sessionId
      this.messages = []
      this.historyLoaded = false
      await this.loadHistory()
    },

    // Load persisted chat from the DB for the current session.
    async loadHistory() {
      if (this.historyLoaded) return
      try {
        const rows = await loadChatHistory(this.currentSessionId)
        this.messages = rows.map((r) => ({
          id: makeId(),
          role: r.role,
          content: r.content,
          citations: r.citations || [],
          sources: r.citations || [],
          isStreaming: false,
          error: null,
        }))
      } catch (e) {
        // History load failed — start fresh (chat still works, just no history).
        console.error('[chat] loadHistory failed:', e)
      } finally {
        this.historyLoaded = true
      }
    },

    async sendMessage(question) {
      // 1. push the user message
      const userMsg = {
        id: makeId(),
        role: 'user',
        content: question,
        citations: [],
        isStreaming: false,
        error: null,
      }
      this.messages.push(userMsg)

      // 2. push an empty assistant message, streaming
      const assistant = {
        id: makeId(),
        role: 'assistant',
        content: '',
        citations: [],
        sources: [],
        isStreaming: true,
        error: null,
      }
      this.messages.push(assistant)

      // 3. scope the query to selected docs (Feature 3)
      const { useDocumentsStore } = await import('./documents')
      const documentsStore = useDocumentsStore()
      const scopeIds = documentsStore.selectedReadyIds

      let tokenCount = 0
      this.currentController = streamChat(
        question,
        {
          onToken: (text) => {
            tokenCount += 1
            assistant.content += text
            this.messages = [...this.messages]
          },
          onCitations: (citations, sources) => {
            assistant.citations = citations
            assistant.sources = sources || []
            assistant.isStreaming = false
            this.messages = [...this.messages]
            // Persist both messages now that the answer is complete.
            this._persistTurn(userMsg, assistant)
          },
          onError: (message) => {
            console.error('[chat] onError:', message)
            assistant.error = message
            assistant.isStreaming = false
            this.messages = [...this.messages]
            // Still persist — the user saw an error message, keep it in history.
            this._persistTurn(userMsg, assistant)
          },
          onFinally: () => {
            assistant.isStreaming = false
            this.currentController = null
            this.messages = [...this.messages]
          },
        },
        scopeIds,
      )
    },

    // Save a user+assistant pair to the DB. Fire-and-forget — if it fails, the
    // message was already shown; only persistence is lost (not the current session).
    async _persistTurn(userMsg, assistantMsg) {
      try {
        await saveChatMessages(
          [
            { role: userMsg.role, content: userMsg.content, citations: null },
            {
              role: assistantMsg.role,
              content: assistantMsg.content,
              citations: assistantMsg.citations || null,
            },
          ],
          this.currentSessionId,
        )
        // Auto-title the session if it's still the default "New session".
        // Uses the first user message as the title (truncated).
        await this._maybeAutoTitle(userMsg.content)
      } catch (e) {
        console.error('[chat] persist failed (non-blocking):', e)
      }
    },

    // If the current session's title is still the default, rename it to the
    // first question (truncated). Updates both the backend and the sessions
    // store so the sidebar reflects it immediately.
    async _maybeAutoTitle(firstQuestion) {
      try {
        const { useSessionsStore } = await import('./sessions')
        const sessions = useSessionsStore()
        const current = sessions.sessions.find((s) => s.id === this.currentSessionId)
        if (current && (current.title === 'New session' || !current.title)) {
          const newTitle = firstQuestion.slice(0, 60) + (firstQuestion.length > 60 ? '…' : '')
          await sessions.rename(this.currentSessionId, newTitle)
        }
      } catch (e) {
        // Non-blocking — title is cosmetic.
      }
    },

    cancel() {
      this.currentController?.abort()
      this.currentController = null
    },

    // Clear all history from DB + UI. Called by the "Clear chat" button.
    async clearHistory() {
      this.cancel()
      try {
        await clearChatHistory(this.currentSessionId)
      } catch (e) {
        console.error('[chat] clearHistory failed:', e)
      }
      this.messages = []
    },

    // Legacy alias (clear() is used by some old callers).
    clear() {
      return this.clearHistory()
    },
  },
})

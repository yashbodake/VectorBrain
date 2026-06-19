// Chat store. Owns the message thread and the streaming chat call.
//
// A message object: { id, role: 'user'|'assistant', content, citations, isStreaming, error }
// The assistant message is created empty with isStreaming:true, then filled in
// as token events arrive — that's what makes the answer appear live.

import { defineStore } from 'pinia'
import { streamChat } from '../api/client'

let nextId = 1
const makeId = () => nextId++

export const useChatStore = defineStore('chat', {
  state: () => ({
    messages: [], // [{ id, role, content, citations, isStreaming, error }]
    currentController: null, // AbortController for the in-flight stream
  }),

  getters: {
    isStreaming: (state) =>
      state.messages.some((m) => m.role === 'assistant' && m.isStreaming),
  },

  actions: {
    async sendMessage(question) {
      // 1. push the user message
      this.messages.push({
        id: makeId(),
        role: 'user',
        content: question,
        citations: [],
        isStreaming: false,
        error: null,
      })

      // 2. push an empty assistant message, streaming
      const assistant = {
        id: makeId(),
        role: 'assistant',
        content: '',
        citations: [],
        isStreaming: true,
        error: null,
      }
      this.messages.push(assistant)

      // 3-6. open SSE, handle token/done/error. Scope the query to the docs
      // the user has selected (Feature 3). The documents store holds the
      // current selection; an empty selection still sends (backend will reply
      // with the "no documents ready" decline).
      const { useDocumentsStore } = await import('./documents')
      const documentsStore = useDocumentsStore()
      const scopeIds = documentsStore.selectedReadyIds

      let tokenCount = 0
      this.currentController = streamChat(
        question,
        {
          onToken: (text) => {
            tokenCount += 1
            // DEBUG: prove tokens reach the store. Check browser console.
            console.log(`[chat] onToken #${tokenCount}:`, JSON.stringify(text))
            assistant.content += text
            // Force Pinia to notice the nested mutation on some Vue versions.
            this.messages = [...this.messages]
          },
          onCitations: (citations) => {
            console.log('[chat] onCitations:', citations)
            assistant.citations = citations
            assistant.isStreaming = false
            this.messages = [...this.messages]
          },
          onError: (message) => {
            console.error('[chat] onError:', message)
            assistant.error = message
            assistant.isStreaming = false
            this.messages = [...this.messages]
          },
          onFinally: () => {
            console.log('[chat] onFinally. total tokens:', tokenCount)
            // If we never got a done/error event (e.g. aborted or stream cut),
            // still clear isStreaming so the UI isn't stuck "typing" forever.
            assistant.isStreaming = false
            this.currentController = null
            this.messages = [...this.messages]
          },
        },
        scopeIds,
      )
    },

    cancel() {
      // User-initiated stop on the in-flight answer.
      this.currentController?.abort()
      this.currentController = null
    },

    clear() {
      this.cancel()
      this.messages = []
    },
  },
})

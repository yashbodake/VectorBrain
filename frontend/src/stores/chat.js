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

      // 3-6. open SSE, handle token/done/error
      this.currentController = streamChat(question, {
        onToken: (text) => {
          assistant.content += text
        },
        onCitations: (citations) => {
          assistant.citations = citations
          assistant.isStreaming = false
        },
        onError: (message) => {
          assistant.error = message
          assistant.isStreaming = false
        },
        onFinally: () => {
          // If we never got a done/error event (e.g. aborted or stream cut),
          // still clear isStreaming so the UI isn't stuck "typing" forever.
          assistant.isStreaming = false
          this.currentController = null
        },
      })
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

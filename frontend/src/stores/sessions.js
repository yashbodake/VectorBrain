// Sessions store — manages the list of chat sessions (session travel).
// The chat store owns the messages for the CURRENT session; this store owns
// the session list (sidebar) and CRUD operations.

import { defineStore } from 'pinia'
import * as api from '../api/client'

export const useSessionsStore = defineStore('sessions', {
  state: () => ({
    sessions: [], // [{ id, title, created_at, updated_at, message_count }]
    loading: false,
  }),

  getters: {
    hasMultiple: (state) => state.sessions.length > 1,
  },

  actions: {
    async fetch() {
      this.loading = true
      try {
        this.sessions = await api.listSessions()
      } finally {
        this.loading = false
      }
    },

    // Create a new session and return it. The chat store switches to it.
    async create(title = 'New session') {
      const sess = await api.createSession(title)
      this.sessions.unshift(sess)
      return sess
    },

    async rename(id, title) {
      const updated = await api.renameSession(id, title)
      const idx = this.sessions.findIndex((s) => s.id === id)
      if (idx !== -1) this.sessions.splice(idx, 1, { ...this.sessions[idx], title })
      return updated
    },

    async remove(id) {
      await api.deleteSession(id)
      this.sessions = this.sessions.filter((s) => s.id !== id)
    },
  },
})

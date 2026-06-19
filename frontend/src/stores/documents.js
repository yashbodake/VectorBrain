// Documents store. Single source of truth for the document list + upload/delete
// state. Components call these actions, never the API client directly
// (coding-conventions.md).

import { defineStore } from 'pinia'
import * as api from '../api/client'

export const useDocumentsStore = defineStore('documents', {
  state: () => ({
    documents: [], // array of document objects from the API
    loading: false, // true while the initial list is fetching
    error: null, // last fetch error (string | null)
    // Document scoping (Feature 3): the set of doc ids the user has selected
    // for the next chat query. All ready docs are selected by default; the user
    // can toggle individual docs to narrow scope. Stored as a plain object
    // (id -> true) for Pinia reactivity (Sets aren't deeply reactive in options
    // stores without extra setup).
    selected: {},
  }),

  getters: {
    // Any document still working its way to 'ready' (uploaded==queued,
    // processing==parsing/embedding). Drives the 2s polling in DocumentManager.
    hasProcessingDocuments: (state) =>
      state.documents.some(
        (d) => d.status === 'processing' || d.status === 'uploaded',
      ),

    readyDocumentCount: (state) =>
      state.documents.filter((d) => d.status === 'ready').length,

    // The ids actually in scope for the next query. Ready docs are selectable;
    // a selected id whose doc is no longer ready (e.g. it got deleted) is
    // ignored at query time too.
    selectedReadyIds(state) {
      const ready = new Set(
        state.documents.filter((d) => d.status === 'ready').map((d) => d.id),
      )
      return Object.keys(state.selected)
        .map((id) => Number(id))
        .filter((id) => ready.has(id))
    },
  },

  actions: {
    // Keep `selected` in sync with the ready docs: a newly-ready doc is auto-
    // selected (all-selected-by-default), a deleted doc is removed. Called after
    // every fetch/refresh so selection never drifts from reality.
    _syncSelection() {
      const readyIds = this.documents
        .filter((d) => d.status === 'ready')
        .map((d) => d.id)
      const next = {}
      for (const id of readyIds) {
        // Preserve existing choice if the doc was already known; otherwise
        // default to selected (all-selected-by-default for new ready docs).
        next[id] = this.selected[id] ?? true
      }
      this.selected = next
    },

    async fetchDocuments() {
      this.loading = true
      this.error = null
      try {
        this.documents = await api.listDocuments()
        this._syncSelection()
      } catch (e) {
        this.error = extractDetail(e) || 'Failed to load documents.'
      } finally {
        this.loading = false
      }
    },

    // Upload one file. onProgress is axios's onUploadProgress callback so the
    // uploader can show a per-file progress bar. Returns the created doc.
    async uploadDocument(file, { onProgress } = {}) {
      const created = await api.uploadDocument(file, {
        onUploadProgress: onProgress,
      })
      // Optimistic insert at the top so it appears immediately.
      this.documents.unshift(created)
      return created
    },

    async deleteDocument(id) {
      await api.deleteDocument(id)
      this.documents = this.documents.filter((d) => d.id !== id)
      this._syncSelection()
    },

    // Replaces a document in the list (used by polling to refresh statuses).
    upsertDocument(doc) {
      const idx = this.documents.findIndex((d) => d.id === doc.id)
      if (idx === -1) this.documents.unshift(doc)
      else this.documents.splice(idx, 1, doc)
    },

    // Refresh only the documents that are still pending (uploaded/processing).
    // Cheaper than refetching all, and enough to drive the status badges. The
    // caller (DocumentManager) decides cadence — every ~2s while any doc is
    // pending, stopped once everything is terminal (docs/06).
    async refreshPending() {
      const pendingIds = this.documents
        .filter((d) => d.status === 'uploaded' || d.status === 'processing')
        .map((d) => d.id)
      if (!pendingIds.length) return
      // One round-trip for the list; we re-derive pending status from it. For a
      // single-user Lite app this is simpler than N per-id GETs.
      const fresh = await api.listDocuments()
      for (const doc of fresh) {
        if (pendingIds.includes(doc.id)) this.upsertDocument(doc)
      }
      this._syncSelection()
    },

    // --- Document scoping controls (Feature 3) ---
    toggleSelected(id) {
      // Only ready docs are selectable.
      const doc = this.documents.find((d) => d.id === id)
      if (!doc || doc.status !== 'ready') return
      this.selected = { ...this.selected, [id]: !this.selected[id] }
    },
    selectAllReady() {
      const next = {}
      for (const d of this.documents) if (d.status === 'ready') next[d.id] = true
      this.selected = next
    },
    selectNoneReady() {
      const next = {}
      for (const d of this.documents) if (d.status === 'ready') next[d.id] = false
      this.selected = next
    },
  },
})

function extractDetail(e) {
  return e?.response?.data?.detail || e?.message || null
}

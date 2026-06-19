<script setup>
// Left panel: document list + uploader. Polls every 2s WHILE any document is
// pending (uploaded/processing) and stops once everything reaches a terminal
// state — never polls forever (docs/06).

import { onMounted, onUnmounted, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useDocumentsStore } from '../stores/documents'
import DocumentCard from './DocumentCard.vue'
import FileUploader from './FileUploader.vue'

const store = useDocumentsStore()
const { documents, loading, error, hasProcessingDocuments } = storeToRefs(store)

let pollTimer = null

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    try {
      await store.refreshPending()
    } catch {
      // Swallow transient poll errors — the next tick retries. A persistent
      // failure will surface on the next manual action.
    }
  }, 2000)
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}

// Poll exactly while something is pending; stop the moment nothing is.
watch(
  hasProcessingDocuments,
  (pending) => {
    if (pending) startPolling()
    else stopPolling()
  },
  { immediate: true },
)

onMounted(() => store.fetchDocuments())
onUnmounted(stopPolling)

async function onDelete(id) {
  try {
    await store.deleteDocument(id)
  } catch (e) {
    // Surface a lightweight alert; real error handling is out of Lite scope.
    alert(e?.response?.data?.detail || 'Could not delete document.')
  }
}
</script>

<template>
  <section class="doc-manager">
    <header class="panel-header">
      <h2>Uploaded Documents</h2>
      <span class="count">{{ documents.length }}</span>
    </header>

    <!-- Loading / error / empty states -->
    <p v-if="loading" class="state">Loading documents…</p>
    <p v-else-if="error" class="state error-text">⚠️ {{ error }}</p>
    <p v-else-if="!documents.length" class="state empty">
      No documents yet. Upload some PDFs to get started.
    </p>

    <div v-else class="doc-list">
      <DocumentCard
        v-for="doc in documents"
        :key="doc.id"
        :document="doc"
        @delete="onDelete"
      />
    </div>

    <FileUploader />
  </section>
</template>

<style scoped>
.doc-manager {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.panel-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
}
.panel-header h2 {
  font-size: 1rem;
  margin: 0;
}
.count {
  font-size: 0.75rem;
  font-weight: 700;
  color: var(--muted, #5b6472);
  background: var(--chip-bg, #eef1f6);
  padding: 0.1rem 0.45rem;
  border-radius: 999px;
}
.state {
  font-size: 0.85rem;
  color: var(--muted, #6b7280);
  padding: 1rem 0;
  text-align: center;
}
.state.empty {
  font-style: italic;
}
.error-text { color: #b42318; }
.doc-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  padding-right: 0.2rem;
}
</style>

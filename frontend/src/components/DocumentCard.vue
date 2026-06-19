<script setup>
// One document row. Shows filename, page count + size, a color-coded status
// badge, and a delete button (with confirm). When status is 'failed', the
// error message is shown (truncated; full text on hover via title).
//
// A scope checkbox (Feature 3) lets the user include/exclude this document
// from chat queries. Only ready docs are selectable — the checkbox is hidden
// while a doc is uploaded/processing/failed (can't search an un-ingested doc).

import { computed, ref } from 'vue'
import { useDocumentsStore } from '../stores/documents'

const props = defineProps({
  document: { type: Object, required: true },
})

const emit = defineEmits(['delete'])

const documents = useDocumentsStore()

// Confirm step so a single misclick can't delete a document.
const confirming = ref(false)

const doc = computed(() => props.document)
const isReady = computed(() => doc.value.status === 'ready')
const isSelected = computed(() => isReady.value && documents.selected[doc.value.id] !== false)

function onToggle() {
  documents.toggleSelected(doc.value.id)
}

// "245 pages • 4.2 MB" — blank while page_count is null and status is
// uploaded/processing (docs/06).
const meta = computed(() => {
  const parts = []
  if (doc.value.page_count != null) {
    parts.push(`${doc.value.page_count} page${doc.value.page_count === 1 ? '' : 's'}`)
  }
  if (doc.value.file_size_bytes != null) {
    parts.push(formatSize(doc.value.file_size_bytes))
  }
  return parts.join(' • ')
})

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB']
  let v = bytes / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`
}

function confirmDelete() {
  // Two-stage: first click reveals "Confirm?", second click fires delete.
  if (!confirming.value) {
    confirming.value = true
    return
  }
  emit('delete', doc.value.id)
}

function resetConfirm() {
  confirming.value = false
}
</script>

<template>
  <div class="doc-card" :class="[`status-${doc.status}`]" @mouseleave="resetConfirm">
    <!-- Scope checkbox (Feature 3). Only ready docs are selectable; the slot
         stays present (with visibility:hidden) so layout doesn't shift while a
         doc is still processing. -->
    <input
      type="checkbox"
      class="scope-checkbox"
      :checked="isSelected"
      :disabled="!isReady"
      :title="isReady ? 'Include this document in answers' : 'Document not ready'"
      :style="{ visibility: isReady ? 'visible' : 'hidden' }"
      @change="onToggle"
    />
    <div class="doc-main">
      <div class="filename" :title="doc.filename">{{ doc.filename }}</div>
      <div class="meta">
        <span v-if="meta">{{ meta }}</span>
        <span v-else class="meta-pending">processing…</span>
      </div>
      <!-- Failed: surface the reason. Truncated via CSS; full text on hover. -->
      <div
        v-if="doc.status === 'failed' && doc.error_message"
        class="error-msg"
        :title="doc.error_message"
      >
        ⚠️ {{ doc.error_message }}
      </div>
    </div>

    <div class="doc-side">
      <span class="badge" :class="`badge-${doc.status}`">
        {{ doc.status }}
      </span>
      <button
        class="delete-btn"
        :class="{ confirming }"
        :title="confirming ? 'Click again to confirm delete' : 'Delete document'"
        @click="confirmDelete"
      >
        {{ confirming ? 'Confirm?' : '✕' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.doc-card {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  padding: 0.6rem 0.7rem;
  border: 1px solid var(--border, #e2e6ee);
  border-left: 3px solid var(--status-color, #c4cad6);
  border-radius: 0.5rem;
  background: #fff;
  transition: border-color 0.2s;
}
.doc-card.status-ready { --status-color: #16a34a; }
.doc-card.status-processing { --status-color: #d97706; }
.doc-card.status-uploaded { --status-color: #d97706; }
.doc-card.status-failed { --status-color: #dc2626; }

.scope-checkbox {
  flex: 0 0 auto;
  margin-top: 0.2rem;
  width: 1.05rem;
  height: 1.05rem;
  cursor: pointer;
  accent-color: var(--accent, #2563eb);
}
.scope-checkbox:disabled {
  cursor: not-allowed;
  opacity: 0.4;
}

.doc-main {
  flex: 1;
  min-width: 0;
}
.filename {
  font-weight: 600;
  font-size: 0.9rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.meta {
  font-size: 0.78rem;
  color: var(--muted, #6b7280);
  margin-top: 0.15rem;
}
.meta-pending {
  font-style: italic;
  opacity: 0.7;
}
.error-msg {
  margin-top: 0.25rem;
  font-size: 0.75rem;
  color: #b42318;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.doc-side {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.35rem;
  flex: 0 0 auto;
}
.badge {
  font-size: 0.68rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  padding: 0.15rem 0.45rem;
  border-radius: 999px;
  background: #eef1f6;
  color: #5b6472;
}
.badge-ready { background: #dcfce7; color: #15803d; }
.badge-processing, .badge-uploaded { background: #fef3c7; color: #b45309; }
.badge-failed { background: #fee2e2; color: #b91c1c; }

.delete-btn {
  border: none;
  background: transparent;
  color: var(--muted, #9aa3b2);
  cursor: pointer;
  font-size: 0.8rem;
  padding: 0.1rem 0.35rem;
  border-radius: 0.3rem;
  transition: background 0.15s, color 0.15s;
}
.delete-btn:hover {
  background: #fee2e2;
  color: #b91c1c;
}
.delete-btn.confirming {
  background: #dc2626;
  color: #fff;
  font-weight: 600;
}
</style>

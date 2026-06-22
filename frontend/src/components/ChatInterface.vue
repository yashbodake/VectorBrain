<script setup>
// Right panel: chat thread + input. Auto-scrolls to bottom on new content.
// Send is disabled while no documents are ready OR while an answer is streaming.

import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useChatStore } from '../stores/chat'
import { useDocumentsStore } from '../stores/documents'
import MessageBubble from './MessageBubble.vue'

const chat = useChatStore()
const documents = useDocumentsStore()
const { messages, isStreaming } = storeToRefs(chat)
// Subscribe to the documents store reactively so any status change from the
// DocumentManager's polling instantly re-enables/disables this composer.
const { readyDocumentCount, selectedReadyIds } = storeToRefs(documents)

const input = ref('')
const threadEl = ref(null)

const readyCount = computed(() => readyDocumentCount.value)
const selectedCount = computed(() => selectedReadyIds.value.length)
// Allow typing only when at least one ready doc is selected (Feature 3 scope).
const canSend = computed(
  () => readyCount.value > 0
    && selectedCount.value > 0
    && !isStreaming.value
    && input.value.trim().length > 0,
)

// Make sure we have the latest document state on mount too — in case this
// panel mounts before the DocumentManager has fetched (or after an HMR reload
// that left the store empty). Belt-and-suspenders for the disabled-input case.
onMounted(() => {
  if (!documents.documents.length) documents.fetchDocuments()
})

const placeholder = computed(() => {
  if (readyCount.value === 0) {
    return 'Upload documents and wait for them to finish processing before asking…'
  }
  if (selectedCount.value === 0) {
    return 'Select at least one document (checkbox on the left) to ask…'
  }
  return 'Ask a question across the selected documents…'
})

async function send() {
  const q = input.value.trim()
  if (!canSend.value) return
  input.value = ''
  await chat.sendMessage(q)
}

function onEnter(e) {
  // Only intercept the Enter key. Shift+Enter = newline (let it through).
  // CRITICAL: without the key check this fires on EVERY keydown and
  // preventDefault() eats every character the user types.
  if (e.key !== 'Enter') return
  if (e.shiftKey) return
  e.preventDefault()
  send()
}

// Auto-scroll to the newest content as it streams in.
watch(
  () => messages.value.map((m) => (m.content?.length ?? 0) + (m.citations?.length ?? 0)),
  () => nextTick(scrollToBottom),
  { deep: true },
)
function scrollToBottom() {
  const el = threadEl.value
  if (el) el.scrollTop = el.scrollHeight
}

// DEBUG: surface the store state in the DOM so we can see WHY the input is
// disabled. Remove once the issue is resolved.
const debugDocs = computed(() => documents.documents)
// (kept for potential future debugging; currently unused in template)
void debugDocs
</script>

<template>
  <section class="chat">
    <div ref="threadEl" class="thread">
      <div v-if="!messages.length" class="empty-chat">
        <div class="empty-avatar">🧠</div>
        <p class="empty-title">VectorBrain</p>
        <p>Upload some PDFs and ask me anything — I'll answer with page-level citations.</p>
        <p class="hint" v-if="readyCount === 0">
          Waiting for at least one document to finish processing…
        </p>
      </div>

      <MessageBubble
        v-for="m in messages"
        :key="m.id"
        :role="m.role"
        :content="m.content"
        :citations="m.citations"
        :is-streaming="m.isStreaming"
        :error="m.error"
      />
    </div>

    <div class="composer">
      <!-- Scope hint (Feature 3): shows how many ready docs are in scope.
           Visible only when there are ready docs to scope. -->
      <div v-if="readyCount > 0" class="scope-hint">
        Searching {{ selectedCount }} of {{ readyCount }} document{{ readyCount === 1 ? '' : 's' }}
        <span v-if="selectedCount === 0" class="scope-warn">(select at least one to ask)</span>
      </div>
      <div class="composer-row">
        <textarea
          v-model="input"
          class="input"
          rows="1"
          :placeholder="placeholder"
          :disabled="selectedCount === 0"
          :title="selectedCount === 0 ? 'Select at least one ready document' : ''"
          @keydown="onEnter"
        />
        <button
          class="send-btn"
          :disabled="!canSend"
          :title="selectedCount === 0 ? 'Select at least one ready document' : 'Send'"
          @click="send"
        >
          {{ isStreaming ? '…' : 'Send' }}
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.chat {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.thread {
  flex: 1;
  overflow-y: auto;
  padding: 0.5rem 0;
}
.empty-chat {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  color: var(--muted, #6b7280);
  gap: 0.4rem;
}
.empty-avatar {
  font-size: 2.5rem;
  margin-bottom: 0.3rem;
}
.empty-title {
  font-size: 1.15rem;
  font-weight: 700;
  color: var(--text, #1f2533);
}
.empty-chat .hint { font-size: 0.8rem; opacity: 0.8; }

.composer {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  padding: 0.75rem 0 0;
  border-top: 1px solid var(--border, #e2e6ee);
}
.composer-row {
  display: flex;
  gap: 0.5rem;
  align-items: flex-end;
}
.scope-hint {
  font-size: 0.74rem;
  color: var(--muted, #6b7280);
}
.scope-warn {
  color: #b45309;
  font-weight: 600;
}
.input {
  flex: 1;
  resize: none;
  max-height: 8rem;
  padding: 0.6rem 0.75rem;
  border: 1px solid var(--border, #c4cad6);
  border-radius: 0.6rem;
  font: inherit;
  font-size: 0.9rem;
  outline: none;
  transition: border-color 0.15s;
}
.input:focus { border-color: var(--accent, #2563eb); }
.input:disabled { background: #f5f6f9; cursor: not-allowed; }

.send-btn {
  flex: 0 0 auto;
  padding: 0.6rem 1.1rem;
  border: none;
  border-radius: 0.6rem;
  background: var(--accent, #2563eb);
  color: #fff;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s, opacity 0.15s;
}
.send-btn:hover:not(:disabled) { background: #1d4ed8; }
.send-btn:disabled {
  background: var(--border, #c4cad6);
  cursor: not-allowed;
}
</style>

<script setup>
// Right panel: chat thread + input. Auto-scrolls to bottom on new content.
// Send is disabled while no documents are ready OR while an answer is streaming.

import { computed, nextTick, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useChatStore } from '../stores/chat'
import { useDocumentsStore } from '../stores/documents'
import MessageBubble from './MessageBubble.vue'

const chat = useChatStore()
const documents = useDocumentsStore()
const { messages, isStreaming } = storeToRefs(chat)

const input = ref('')
const threadEl = ref(null)

const readyCount = computed(() => documents.readyDocumentCount)
const canSend = computed(
  () => readyCount.value > 0 && !isStreaming.value && input.value.trim().length > 0,
)

const placeholder = computed(() => {
  if (readyCount.value === 0) {
    return 'Upload documents and wait for them to finish processing before asking…'
  }
  return 'Ask a question across all documents…'
})

async function send() {
  const q = input.value.trim()
  if (!canSend.value) return
  input.value = ''
  await chat.sendMessage(q)
}

function onEnter(e) {
  // Shift+Enter = newline; Enter = send.
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
</script>

<template>
  <section class="chat">
    <div ref="threadEl" class="thread">
      <div v-if="!messages.length" class="empty-chat">
        <p>Ask a question about your uploaded documents.</p>
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
      <textarea
        v-model="input"
        class="input"
        rows="1"
        :placeholder="placeholder"
        :disabled="readyCount === 0"
        :title="readyCount === 0 ? 'No ready documents yet' : ''"
        @keydown="onEnter"
      />
      <button
        class="send-btn"
        :disabled="!canSend"
        :title="readyCount === 0 ? 'No ready documents yet' : 'Send'"
        @click="send"
      >
        {{ isStreaming ? '…' : 'Send' }}
      </button>
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
.empty-chat .hint { font-size: 0.8rem; opacity: 0.8; }

.composer {
  display: flex;
  gap: 0.5rem;
  align-items: flex-end;
  padding: 0.75rem 0 0;
  border-top: 1px solid var(--border, #e2e6ee);
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

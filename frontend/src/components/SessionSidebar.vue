<script setup>
// Session sidebar — the session list (ChatGPT-style threads).
// Lists all chat sessions, lets the user create a new one, switch between them,
// and delete. Auto-titled from the first question (handled in the chat store).

import { computed, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useSessionsStore } from '../stores/sessions'
import { useChatStore } from '../stores/chat'

const sessions = useSessionsStore()
const chat = useChatStore()
const { hasMultiple } = storeToRefs(sessions)

// Active session = the chat store's currentSessionId.
const activeId = computed(() => chat.currentSessionId)

onMounted(async () => {
  await sessions.fetch()
  // If no sessions exist (fresh DB), create one and switch to it.
  if (!sessions.sessions.length) {
    const sess = await sessions.create()
    await chat.switchSession(sess.id)
  } else {
    // Switch to the newest session on load.
    const newest = sessions.sessions[0]
    if (chat.currentSessionId !== newest.id) {
      await chat.switchSession(newest.id)
    } else {
      await chat.loadHistory()
    }
  }
})

async function newSession() {
  const sess = await sessions.create()
  await chat.switchSession(sess.id)
}

async function selectSession(id) {
  if (id === activeId.value) return
  await chat.switchSession(id)
}

async function deleteSession(id) {
  // Don't delete the last remaining session — keep at least one.
  if (sessions.sessions.length <= 1) return
  await sessions.remove(id)
  // If we deleted the active one, switch to the newest remaining.
  if (id === activeId.value) {
    await chat.switchSession(sessions.sessions[0].id)
  }
}
</script>

<template>
  <div class="session-sidebar">
    <button class="new-session-btn" @click="newSession">
      ＋ New chat
    </button>

    <div class="session-list">
      <div
        v-for="s in sessions.sessions"
        :key="s.id"
        class="session-item"
        :class="{ active: s.id === activeId }"
        @click="selectSession(s.id)"
      >
        <span class="session-title">{{ s.title || 'New session' }}</span>
        <button
          v-if="sessions.sessions.length > 1"
          class="session-delete"
          title="Delete session"
          @click.stop="deleteSession(s.id)"
        >
          ✕
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.session-sidebar {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  height: 100%;
  overflow: hidden;
}
.new-session-btn {
  flex: 0 0 auto;
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--accent, #2563eb);
  background: var(--accent-soft, #e7eefd);
  color: var(--accent, #2563eb);
  border-radius: 0.5rem;
  font-weight: 600;
  font-size: 0.85rem;
  cursor: pointer;
  transition: background 0.15s;
}
.new-session-btn:hover {
  background: var(--accent, #2563eb);
  color: #fff;
}
.session-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.4rem;
  padding: 0.4rem 0.6rem;
  border-radius: 0.4rem;
  cursor: pointer;
  font-size: 0.82rem;
  color: var(--text, #1f2533);
  transition: background 0.12s;
}
.session-item:hover {
  background: var(--chip-bg, #eef1f6);
}
.session-item.active {
  background: var(--accent-soft, #e7eefd);
  font-weight: 600;
}
.session-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.session-delete {
  flex: 0 0 auto;
  border: none;
  background: transparent;
  color: var(--muted, #9aa3b2);
  cursor: pointer;
  font-size: 0.75rem;
  padding: 0.1rem 0.3rem;
  border-radius: 0.3rem;
  opacity: 0;
  transition: opacity 0.12s, background 0.12s, color 0.12s;
}
.session-item:hover .session-delete {
  opacity: 1;
}
.session-delete:hover {
  background: #fee2e2;
  color: #b91c1c;
}
</style>

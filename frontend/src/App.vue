<script setup>
// App shell: header + two-panel layout (DocumentManager left, ChatInterface
// right) per docs/06. No business logic here — state lives in the Pinia stores.

import DocumentManager from './components/DocumentManager.vue'
import ChatInterface from './components/ChatInterface.vue'
</script>

<template>
  <div class="app">
    <header class="app-header">
      <div class="brand">
        <span class="logo">🧠</span>
        <div>
          <h1>VectorBrain</h1>
          <p class="tagline">Upload PDFs, ask questions, get cited answers.</p>
        </div>
      </div>
    </header>

    <main class="layout">
      <aside class="panel panel-docs">
        <DocumentManager />
      </aside>
      <section class="panel panel-chat">
        <ChatInterface />
      </section>
    </main>
  </div>
</template>

<style scoped>
.app {
  height: 100vh;
  display: flex;
  flex-direction: column;
}
.app-header {
  flex: 0 0 auto;
  padding: 0.85rem 1.25rem;
  border-bottom: 1px solid var(--border, #e2e6ee);
  background: #fff;
}
.brand {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}
.logo { font-size: 1.6rem; }
.brand h1 {
  margin: 0;
  font-size: 1.1rem;
  letter-spacing: -0.01em;
}
.tagline {
  margin: 0;
  font-size: 0.78rem;
  color: var(--muted, #6b7280);
}
.layout {
  flex: 1;
  display: grid;
  grid-template-columns: minmax(320px, 30%) 1fr;
  gap: 0;
  min-height: 0; /* allow children to scroll */
}
.panel {
  min-height: 0;
  padding: 1.1rem 1.25rem;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.panel-docs {
  border-right: 1px solid var(--border, #e2e6ee);
  background: var(--panel-bg, #fafbfd);
}
.panel-chat {
  background: #fff;
}

/* Responsive: stack panels on narrow screens. */
@media (max-width: 820px) {
  .layout {
    grid-template-columns: 1fr;
    grid-template-rows: auto 1fr;
  }
  .panel-docs {
    border-right: none;
    border-bottom: 1px solid var(--border, #e2e6ee);
    max-height: 40vh;
  }
}
</style>

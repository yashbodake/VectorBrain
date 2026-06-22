<script setup>
// Summary panel — shows cached per-section summaries for a document.
// "Summarize" generates them (one LLM call per 10-page section, cached in DB).
// After that, they load instantly on every revisit.

import { ref, onMounted } from 'vue'
import { getSummaries, generateSummaries } from '../api/client'

const props = defineProps({
  documentId: { type: Number, required: true },
  filename: { type: String, default: '' },
})

const emit = defineEmits(['close'])

const loading = ref(false)
const generating = ref(false)
const error = ref(null)
const summaries = ref([])

onMounted(async () => {
  loading.value = true
  try {
    summaries.value = await getSummaries(props.documentId)
  } catch (e) {
    error.value = 'Failed to load summaries.'
  } finally {
    loading.value = false
  }
})

async function generate() {
  generating.value = true
  error.value = null
  try {
    // Fire-and-forget the POST (it may take minutes on large docs and the
    // browser will timeout the HTTP request). We don't await the response —
    // instead we poll GET /summaries until results appear.
    generateSummaries(props.documentId).catch(() => {})
    // Start polling for results.
    await pollForResults()
  } catch (e) {
    error.value = e?.response?.data?.detail || 'Failed to generate summaries.'
  } finally {
    generating.value = false
  }
}

// Poll GET /summaries every 5s until the backend finishes generating.
// Times out after 5 min (worst case for a 200-page book).
async function pollForResults() {
  const maxAttempts = 60  // 60 × 5s = 5 min
  for (let i = 0; i < maxAttempts; i++) {
    await new Promise(resolve => setTimeout(resolve, 5000))
    try {
      const result = await getSummaries(props.documentId)
      if (result.length > 0) {
        summaries.value = result
        return
      }
    } catch {
      // keep polling
    }
  }
  throw new Error('Timed out waiting for summaries (try again or check backend).')
}
</script>

<template>
  <div class="summary-panel">
    <div class="summary-header">
      <span class="summary-title">📖 Summary: {{ filename.slice(0, 30) }}</span>
      <button class="close-btn" @click="emit('close')">✕</button>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="state">Loading summaries…</div>

    <!-- No summaries yet -->
    <div v-else-if="!summaries.length && !generating" class="state empty">
      <p>No summaries yet. Click the button to generate per-chapter overviews.</p>
      <button class="gen-btn" @click="generate">📖 Generate Summaries</button>
    </div>

    <!-- Generating -->
    <div v-if="generating" class="state">
      <p>✍️ Generating summaries (one per 10-page section)…</p>
    </div>

    <!-- Error -->
    <div v-if="error" class="state error-text">⚠️ {{ error }}</div>

    <!-- Summaries list -->
    <div v-if="summaries.length" class="summaries-list">
      <button class="regen-btn" @click="generate" :disabled="generating">
        {{ generating ? 'Generating…' : '↻ Regenerate' }}
      </button>
      <div v-for="s in summaries" :key="s.id" class="summary-card">
        <div class="summary-card-title">{{ s.title }}</div>
        <p class="summary-card-text">{{ s.summary }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.summary-panel {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 90%;
  max-width: 600px;
  max-height: 85vh;
  overflow-y: auto;
  background: #fff;
  border-radius: 0.75rem;
  box-shadow: 0 12px 40px rgba(15, 23, 42, 0.2);
  z-index: 100;
  padding: 1.5rem;
}
.summary-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
}
.summary-title { font-weight: 700; font-size: 0.95rem; }
.close-btn {
  border: none;
  background: transparent;
  font-size: 1.1rem;
  cursor: pointer;
  color: var(--muted, #9aa3b2);
  border-radius: 0.3rem;
  padding: 0.2rem 0.5rem;
}
.close-btn:hover { background: var(--chip-bg, #eef1f6); }

.state {
  text-align: center;
  padding: 2rem 1rem;
  color: var(--muted, #6b7280);
}
.state.empty p { margin-bottom: 1rem; }
.gen-btn {
  padding: 0.6rem 1.5rem;
  border: none;
  border-radius: 0.5rem;
  background: var(--accent, #2563eb);
  color: #fff;
  font-weight: 600;
  cursor: pointer;
}
.gen-btn:hover { background: #1d4ed8; }

.regen-btn {
  margin-bottom: 1rem;
  padding: 0.4rem 1rem;
  border: 1px solid var(--border, #e2e6ee);
  border-radius: 0.4rem;
  background: #fff;
  color: var(--muted, #6b7280);
  font-size: 0.8rem;
  cursor: pointer;
}
.regen-btn:hover { background: var(--chip-bg, #eef1f6); }
.regen-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.summaries-list { display: flex; flex-direction: column; gap: 0.75rem; }
.summary-card {
  padding: 0.85rem;
  border: 1px solid var(--border, #e2e6ee);
  border-left: 3px solid var(--accent, #2563eb);
  border-radius: 0.5rem;
}
.summary-card-title {
  font-weight: 700;
  font-size: 0.85rem;
  color: var(--accent, #2563eb);
  margin-bottom: 0.35rem;
}
.summary-card-text {
  margin: 0;
  font-size: 0.88rem;
  line-height: 1.5;
  color: var(--text, #1f2533);
}
</style>

<script setup>
// One Q&A turn. User vs assistant are visually distinct. While isStreaming is
// true we show a subtle typing indicator; once streaming finishes, citations
// render as CitationChips below the content. An error during streaming shows
// an inline error state instead of leaving the bubble stuck mid-stream.

import { computed, ref } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import CitationChip from './CitationChip.vue'
import {
  normalizeCitationMarkers,
  renderSentinelsAsCites,
  extractMarkers,
} from '../utils/citations.js'

const props = defineProps({
  role: { type: String, required: true }, // 'user' | 'assistant'
  content: { type: String, default: '' },
  citations: { type: Array, default: () => [] },
  isStreaming: { type: Boolean, default: false },
  error: { type: String, default: null },
})

// Pipeline: normalize dagger artifacts (【1†L1-L4】 -> [1]) -> extract markers
// to sentinels -> markdown parse -> sanitize -> revive sentinels as <sup> cites.
// DOMPurify is mandatory: LLM output is untrusted; never v-html without it.
marked.setOptions({ breaks: true, gfm: true })
const renderedHtml = computed(() => {
  if (props.role !== 'assistant' || !props.content) return ''
  const cleaned = normalizeCitationMarkers(props.content)
  const withSentinels = extractMarkers(cleaned)
  const raw = marked.parse(withSentinels)
  const safe = DOMPurify.sanitize(typeof raw === 'string' ? raw : '')
  return renderSentinelsAsCites(safe)
})

// --- Inline citation hover popup (event delegation on the rendered HTML) ---
// The <sup class="inline-cite"> nodes are injected via v-html, so we can't bind
// Vue handlers to them directly. Instead we delegate mouseenter/leave on the
// container. The data-cite-group attribute maps to the index in `citations`.
const hoveredCite = ref(null) // { idx, filename, pageNumber, excerpt } | null

function onCiteEnter(e) {
  const el = e.target.closest('.inline-cite')
  if (!el) return
  // The number INSIDE the brackets is 1-based (model's [1] = first excerpt).
  // The backend's citations array is in that same order, so [N] -> citations[N-1].
  const citeNum = Number(el.dataset.citeNum)
  const cite = props.citations[citeNum - 1]
  if (!cite) return
  hoveredCite.value = {
    citeNum,
    filename: cite.filename,
    pageNumber: cite.page_number,
    excerpt: cite.excerpt || '',
    // Position the popup near the hovered <sup>.
    rect: el.getBoundingClientRect(),
  }
}
function onCiteLeave(e) {
  // Only hide if we actually left the sup (not just moved into the popup).
  const el = e.target.closest('.inline-cite')
  if (!el) return
  hoveredCite.value = null
}
</script>

<template>
  <div class="msg" :class="role">
    <div class="msg-avatar" aria-hidden="true">
      {{ role === 'user' ? '🧑' : '🤖' }}
    </div>
    <div class="msg-body">
      <div class="meta">{{ role === 'user' ? 'You' : 'Assistant' }}</div>

      <!-- Content. Assistant answers are markdown -> sanitized HTML; user
           messages stay plain text (short, no formatting expected). Inline
           [n] markers are turned into <sup class="inline-cite"> by the
           renderedHtml pipeline; we delegate hover events to pop up the
           matching excerpt (NotebookLM-style). -->
      <div
        v-if="role === 'assistant' && renderedHtml"
        class="content markdown-body"
        v-html="renderedHtml"
        @mouseover="onCiteEnter"
        @mouseout="onCiteLeave"
      />
      <div v-else-if="content" class="content">{{ content }}</div>

      <!-- Streaming indicator: show only when assistant is mid-stream and has
           emitted nothing yet (or as a trailing caret). -->
      <div v-if="isStreaming && !error" class="streaming" aria-live="polite">
        <span v-if="!content" class="typing">Thinking<span class="dot">.</span><span class="dot">.</span><span class="dot">.</span></span>
        <span v-else class="caret" />
      </div>

      <!-- Inline error state (mid-stream failure). -->
      <div v-if="error" class="error">⚠️ {{ error }}</div>

      <!-- Citations, only after streaming completes. -->
      <div v-if="!isStreaming && citations.length" class="citations">
        <CitationChip
          v-for="(c, i) in citations"
          :key="i"
          :filename="c.filename"
          :page-number="c.page_number"
          :excerpt="c.excerpt || ''"
        />
      </div>
    </div>

    <!-- Inline-citation hover popup. Teleported to body so it isn't clipped by
         the bubble's overflow / border-radius. Fixed-positioned at the marker. -->
    <Teleport to="body">
      <div
        v-if="hoveredCite"
        class="inline-cite-pop"
        :style="{ top: (hoveredCite.rect.top - 8) + 'px', left: hoveredCite.rect.left + 'px' }"
        @mouseenter="hoveredCite = hoveredCite"
        @mouseleave="hoveredCite = null"
      >
        <!-- The little arrow points down at the [n] marker. -->
        <div class="inline-cite-arrow" />
        <div class="inline-cite-header">
          <strong>{{ hoveredCite.filename }}</strong>
          <span v-if="hoveredCite.pageNumber !== null" class="inline-cite-page">p. {{ hoveredCite.pageNumber }}</span>
        </div>
        <div v-if="hoveredCite.excerpt" class="inline-cite-excerpt">{{ hoveredCite.excerpt }}</div>
        <div v-else class="inline-cite-excerpt muted">Citation [{{ hoveredCite.citeNum }}] (source loading…)</div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.msg {
  display: flex;
  gap: 0.75rem;
  margin: 0.75rem 0;
}
.msg.user {
  flex-direction: row-reverse;
}
.msg-avatar {
  flex: 0 0 auto;
  width: 2rem;
  height: 2rem;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.1rem;
  background: var(--bubble-avatar, #f1f3f8);
  border-radius: 50%;
}
.msg.user .msg-avatar {
  background: var(--accent-soft, #e7eefd);
}
.msg-body {
  max-width: 80%;
  padding: 0.6rem 0.85rem;
  border-radius: 0.85rem;
  background: var(--bubble-assistant, #f7f8fb);
}
.msg.user .msg-body {
  background: var(--bubble-user, #2563eb);
  color: #fff;
}
.meta {
  font-size: 0.72rem;
  opacity: 0.6;
  margin-bottom: 0.2rem;
}
.content {
  white-space: pre-wrap;
  word-wrap: break-word;
  line-height: 1.5;
}
/* Markdown-rendered assistant answers: the parser emits <br>/<p>/<li>, so we
   must NOT use white-space: pre-wrap here (would double the line breaks). */
.content.markdown-body {
  white-space: normal;
}
.content.markdown-body :deep(p) {
  margin: 0 0 0.5rem;
}
.content.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}
.content.markdown-body :deep(ul),
.content.markdown-body :deep(ol) {
  margin: 0.25rem 0 0.5rem;
  padding-left: 1.25rem;
}
.content.markdown-body :deep(li) {
  margin: 0.15rem 0;
}
.content.markdown-body :deep(strong) {
  font-weight: 700;
}
.content.markdown-body :deep(em) {
  font-style: italic;
}
.content.markdown-body :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.85em;
  background: rgba(0, 0, 0, 0.06);
  padding: 0.1em 0.3em;
  border-radius: 0.3em;
}
.content.markdown-body :deep(a) {
  color: var(--accent, #2563eb);
}
.streaming {
  margin-top: 0.15rem;
}
.typing .dot {
  animation: blink 1.2s infinite;
}
.typing .dot:nth-child(2) { animation-delay: 0.2s; }
.typing .dot:nth-child(3) { animation-delay: 0.4s; }
.caret {
  display: inline-block;
  width: 0.5rem;
  height: 1rem;
  background: currentColor;
  opacity: 0.6;
  animation: blink 1s steps(2) infinite;
  vertical-align: text-bottom;
  margin-left: 1px;
}
@keyframes blink {
  0%, 100% { opacity: 0.2; }
  50% { opacity: 1; }
}
.error {
  margin-top: 0.4rem;
  padding: 0.35rem 0.5rem;
  font-size: 0.85rem;
  color: #b42318;
  background: #fee;
  border: 1px solid #f3c2bd;
  border-radius: 0.4rem;
}
.citations {
  margin-top: 0.55rem;
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
}

/* Inline [n] citation markers inside the answer text (NotebookLM-style). They
   render as clickable-looking superscripts that pop up the source excerpt. */
.content.markdown-body :deep(.inline-cite) {
  display: inline-block;
  font-size: 0.7em;
  font-weight: 700;
  color: var(--accent, #2563eb);
  background: var(--accent-soft, #e7eefd);
  border-radius: 0.3em;
  padding: 0.05em 0.35em;
  margin: 0 0.05em;
  cursor: pointer;
  vertical-align: super;
  line-height: 1;
  transition: background 0.12s, color 0.12s;
  user-select: none;
}
.content.markdown-body :deep(.inline-cite:hover) {
  background: var(--accent, #2563eb);
  color: #fff;
}

/* The hover popup (teleported to body). Fixed-positioned at the marker so it
   never gets clipped by the chat bubble's overflow. */
.inline-cite-pop {
  position: fixed;
  transform: translate(-50%, -100%);
  z-index: 9999;
  width: 320px;
  max-width: 86vw;
  max-height: 240px;
  overflow-y: auto;
  padding: 0.6rem 0.75rem;
  background: #fff;
  border: 1px solid var(--border, #dde2eb);
  border-radius: 0.5rem;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.18);
  font-size: 0.82rem;
  line-height: 1.45;
  color: var(--text, #1f2533);
  text-align: left;
  pointer-events: auto;
}
.inline-cite-arrow {
  position: absolute;
  bottom: -6px;
  left: 50%;
  transform: translateX(-50%) rotate(45deg);
  width: 10px;
  height: 10px;
  background: #fff;
  border-right: 1px solid var(--border, #dde2eb);
  border-bottom: 1px solid var(--border, #dde2eb);
}
.inline-cite-header {
  display: flex;
  justify-content: space-between;
  gap: 0.5rem;
  margin-bottom: 0.35rem;
  padding-bottom: 0.3rem;
  border-bottom: 1px solid var(--border, #eef1f6);
  font-size: 0.78rem;
}
.inline-cite-page {
  color: var(--muted, #6b7280);
  white-space: nowrap;
}
.inline-cite-excerpt {
  color: var(--text, #374151);
}
.inline-cite-excerpt.muted {
  color: var(--muted, #9aa3b2);
  font-style: italic;
}
</style>

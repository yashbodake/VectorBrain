<script setup>
// One Q&A turn. User vs assistant are visually distinct. While isStreaming is
// true we show a subtle typing indicator; once streaming finishes, citations
// render as CitationChips below the content. An error during streaming shows
// an inline error state instead of leaving the bubble stuck mid-stream.

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
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
  citations: { type: Array, default: () => [] }, // per-chunk, for inline [n]
  sources: { type: Array, default: () => [] }, // deduped per-page, for chips
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
const hoveredCite = ref(null) // { citeNum, filename, pageNumber, excerpt, rect } | null
// A chip the user CLICKED (pinned open). Null when none. Click again to close.
const pinnedSource = ref(null)

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

// Click a source chip -> pin its popup open (click again / click elsewhere to
// close). The popup reuses the inline-cite-pop styling for consistency.
function toggleSourcePin(e, idx, source) {
  // Clicking the same pinned chip closes it.
  if (pinnedSource.value && pinnedSource.value.idx === idx) {
    pinnedSource.value = null
    return
  }
  const rect = e.currentTarget.getBoundingClientRect()
  pinnedSource.value = {
    idx,
    filename: source.filename,
    pageNumber: source.page_number,
    excerpt: source.excerpt || '',
    rect,
  }
}

// Click anywhere outside a chip/popup closes the pinned popup.
function onGlobalClick(e) {
  if (!pinnedSource.value) return
  if (e.target.closest('.source-chip-btn') || e.target.closest('.inline-cite-pop.pinned')) return
  pinnedSource.value = null
}
onMounted(() => document.addEventListener('click', onGlobalClick))
onBeforeUnmount(() => document.removeEventListener('click', onGlobalClick))
</script>

<template>
  <div class="msg" :class="role">
    <div class="msg-avatar" aria-hidden="true">
      {{ role === 'user' ? '🧑' : '🧠' }}
    </div>
    <div class="msg-body">
      <div class="meta">{{ role === 'user' ? 'You' : 'VectorBrain' }}</div>

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

      <!-- Source chips: ONE per unique (filename, page), deduped by the backend.
           Clickable — pins a popup open (click again to close). -->
      <div v-if="!isStreaming && sources.length" class="citations">
        <button
          v-for="(s, i) in sources"
          :key="i"
          type="button"
          class="source-chip-btn"
          :class="{ pinned: pinnedSource && pinnedSource.idx === i }"
          :title="`Source: ${s.filename}${s.page_number !== null ? ', p. ' + s.page_number : ''}`"
          @click="toggleSourcePin($event, i, s)"
        >
          <CitationChip
            :filename="s.filename"
            :page-number="s.page_number"
            :excerpt="s.excerpt || ''"
          />
        </button>
      </div>
    </div>

    <!-- Popups teleported to body so they aren't clipped by the bubble. -->
    <Teleport to="body">
      <!-- Click-pinned source-chip popup (stays open until clicked again). -->
      <div
        v-if="pinnedSource"
        class="inline-cite-pop pinned"
        :style="{ top: (pinnedSource.rect.top - 8) + 'px', left: pinnedSource.rect.left + 'px' }"
        @click.stop
      >
        <button class="pop-close" title="Close" @click="pinnedSource = null">✕</button>
        <div class="inline-cite-header">
          <strong>{{ pinnedSource.filename }}</strong>
          <span v-if="pinnedSource.pageNumber !== null" class="inline-cite-page">p. {{ pinnedSource.pageNumber }}</span>
        </div>
        <div v-if="pinnedSource.excerpt" class="inline-cite-excerpt">{{ pinnedSource.excerpt }}</div>
        <div v-else class="inline-cite-excerpt muted">No excerpt available.</div>
      </div>

      <!-- Inline [n] hover popup (transient, follows the mouse). -->
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

/* Source chips are wrapped in a real <button> so they're keyboard-focusable
   and clickable (pins the excerpt popup open). Reset button defaults so the
   CitationChip styling shows through. */
.source-chip-btn {
  padding: 0;
  border: none;
  background: none;
  cursor: pointer;
  font: inherit;
  color: inherit;
  border-radius: 999px; /* match the chip's pill shape for the focus ring */
  transition: transform 0.1s;
}
.source-chip-btn:hover { transform: translateY(-1px); }
.source-chip-btn:focus-visible {
  outline: 2px solid var(--accent, #2563eb);
  outline-offset: 2px;
}
.source-chip-btn.pinned {
  filter: drop-shadow(0 0 0 var(--accent, #2563eb));
}
.source-chip-btn.pinned :deep(.citation-chip),
.source-chip-btn:hover :deep(.citation-chip) {
  background: var(--accent-soft, #e7eefd);
  border-color: var(--accent, #2563eb);
}

/* Close button on the pinned popup. */
.pop-close {
  position: absolute;
  top: 0.35rem;
  right: 0.4rem;
  border: none;
  background: transparent;
  color: var(--muted, #6b7280);
  cursor: pointer;
  font-size: 0.9rem;
  line-height: 1;
  padding: 0.15rem 0.3rem;
  border-radius: 0.3rem;
}
.pop-close:hover { background: var(--chip-bg, #eef1f6); color: var(--text, #1f2533); }
.inline-cite-pop.pinned {
  cursor: default;
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

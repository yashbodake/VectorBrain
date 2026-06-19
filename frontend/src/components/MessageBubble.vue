<script setup>
// One Q&A turn. User vs assistant are visually distinct. While isStreaming is
// true we show a subtle typing indicator; once streaming finishes, citations
// render as CitationChips below the content. An error during streaming shows
// an inline error state instead of leaving the bubble stuck mid-stream.

import { computed } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import CitationChip from './CitationChip.vue'

const props = defineProps({
  role: { type: String, required: true }, // 'user' | 'assistant'
  content: { type: String, default: '' },
  citations: { type: Array, default: () => [] },
  isStreaming: { type: Boolean, default: false },
  error: { type: String, default: null },
})

// Assistant answers are markdown (bold, italic, lists). Parse -> sanitize ->
// render as HTML. DOMPurify is mandatory: the LLM output is untrusted and we
// must never inject raw HTML via v-html without sanitizing (XSS). User
// messages stay plain text in the template (short, no formatting expected).
//
// marked is sync by default; recomputed on every `content` change (i.e. per
// streamed token) — cheap on these small deltas.
marked.setOptions({ breaks: true, gfm: true })
const renderedHtml = computed(() => {
  if (props.role !== 'assistant' || !props.content) return ''
  const raw = marked.parse(props.content)
  return DOMPurify.sanitize(typeof raw === 'string' ? raw : '')
})
</script>

<template>
  <div class="msg" :class="role">
    <div class="msg-avatar" aria-hidden="true">
      {{ role === 'user' ? '🧑' : '🤖' }}
    </div>
    <div class="msg-body">
      <div class="meta">{{ role === 'user' ? 'You' : 'Assistant' }}</div>

      <!-- Content. Assistant answers are markdown -> sanitized HTML; user
           messages stay plain text (short, no formatting expected). -->
      <div v-if="role === 'assistant' && renderedHtml" class="content markdown-body" v-html="renderedHtml" />
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
</style>

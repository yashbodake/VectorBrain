<script setup>
// One source citation under an answer. Renders "🔖 Source: <file>, p. <n>";
// on hover, a card pops up showing the actual source passage (excerpt) the
// answer was drawn from — NotebookLM-style. The page portion is omitted when
// pageNumber is null (chunk spans a page break / unattributable; docs/03).

import { ref } from 'vue'

const props = defineProps({
  filename: { type: String, required: true },
  pageNumber: { type: Number, default: null },
  excerpt: { type: String, default: '' },
})

const showPopover = ref(false)
let hideTimer = null

function onEnter() {
  // Cancel any pending hide so quick mouse wobbles don't flicker the card.
  if (hideTimer) { clearTimeout(hideTimer); hideTimer = null }
  // Only show if there's actually something to display (an excerpt).
  if (props.excerpt) showPopover.value = true
}
function onLeave() {
  // Small delay so moving the mouse from chip -> card doesn't close it.
  hideTimer = setTimeout(() => { showPopover.value = false }, 120)
}
</script>

<template>
  <span
    class="citation-chip"
    tabindex="0"
    :title="`Source: ${filename}`"
    @mouseenter="onEnter"
    @mouseleave="onLeave"
    @focus="onEnter"
    @blur="onLeave"
  >
    <span class="citation-icon" aria-hidden="true">🔖</span>
    <span class="citation-text">
      Source: <strong>{{ filename }}</strong>
      <template v-if="pageNumber !== null">, p. {{ pageNumber }}</template>
    </span>

    <!-- Hover card: the source passage. Positioned just above the chip. -->
    <transition name="pop">
      <div v-if="showPopover" class="citation-popover" role="tooltip" @mouseenter="onEnter" @mouseleave="onLeave">
        <div class="popover-header">
          <strong>{{ filename }}</strong>
          <span v-if="pageNumber !== null" class="popover-page">p. {{ pageNumber }}</span>
        </div>
        <div class="popover-excerpt">{{ excerpt }}</div>
      </div>
    </transition>
  </span>
</template>

<style scoped>
.citation-chip {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.2rem 0.55rem;
  font-size: 0.78rem;
  color: var(--muted, #5b6472);
  background: var(--chip-bg, #eef1f6);
  border: 1px solid var(--chip-border, #dde2eb);
  border-radius: 999px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
  cursor: default;
  outline: none;
}
.citation-chip:hover,
.citation-chip:focus-visible {
  background: var(--accent-soft, #e7eefd);
  border-color: var(--accent, #2563eb);
}
.citation-icon {
  font-size: 0.7rem;
}
.citation-text {
  overflow: hidden;
  text-overflow: ellipsis;
}

/* The hover card. Anchored to the chip, floats above so it doesn't push
   layout. Wide enough to read a passage; capped height with scroll. */
.citation-popover {
  position: absolute;
  bottom: calc(100% + 0.4rem);
  left: 0;
  z-index: 50;
  width: 320px;
  max-width: 80vw;
  max-height: 240px;
  overflow-y: auto;
  padding: 0.6rem 0.75rem;
  background: #fff;
  border: 1px solid var(--border, #dde2eb);
  border-radius: 0.5rem;
  box-shadow: 0 6px 20px rgba(15, 23, 42, 0.15);
  font-size: 0.82rem;
  line-height: 1.45;
  color: var(--text, #1f2533);
  white-space: normal;
  text-align: left;
}
.popover-header {
  display: flex;
  justify-content: space-between;
  gap: 0.5rem;
  margin-bottom: 0.35rem;
  padding-bottom: 0.3rem;
  border-bottom: 1px solid var(--border, #eef1f6);
  font-size: 0.78rem;
}
.popover-page {
  color: var(--muted, #6b7280);
  white-space: nowrap;
}
.popover-excerpt {
  color: var(--text, #374151);
}

/* Transition for the popover. */
.pop-enter-active, .pop-leave-active {
  transition: opacity 0.12s ease, transform 0.12s ease;
}
.pop-enter-from, .pop-leave-to {
  opacity: 0;
  transform: translateY(4px);
}
</style>

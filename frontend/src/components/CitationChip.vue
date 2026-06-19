<script setup>
// One source citation under an answer. Renders "Source: <file>, p. <n>"; the
// page portion is omitted gracefully when pageNumber is null (chunk spans a
// page break / unattributable) per docs/03-database-schema.md.
defineProps({
  filename: { type: String, required: true },
  pageNumber: { type: Number, default: null },
})
</script>

<template>
  <span class="citation-chip" :title="`Source: ${filename}`">
    <span class="citation-icon" aria-hidden="true">🔖</span>
    <span class="citation-text">
      Source: <strong>{{ filename }}</strong>
      <template v-if="pageNumber !== null">, p. {{ pageNumber }}</template>
    </span>
  </span>
</template>

<style scoped>
.citation-chip {
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
}
.citation-icon {
  font-size: 0.7rem;
}
.citation-text {
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>

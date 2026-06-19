<script setup>
// Drag/drop + file picker for PDFs. Multi-file. Each file uploads independently
// (concurrent uploads, each with its own progress) so one slow upload doesn't
// block the others in the UI. Client-side PDF validation gives fast feedback;
// the server still re-validates (docs/06).

import { computed, ref } from 'vue'
import { useDocumentsStore } from '../stores/documents'

const documents = useDocumentsStore()

const dragOver = ref(false)
const fileInput = ref(null)
// { file, progress, error } per in-flight upload
const uploads = ref([])

const isUploading = computed(() => uploads.value.length > 0)

function openPicker() {
  fileInput.value?.click()
}

function onPick(e) {
  handleFiles(e.target.files)
  // reset so picking the same file twice fires change again
  e.target.value = ''
}

function onDrop(e) {
  e.preventDefault()
  dragOver.value = false
  handleFiles(e.dataTransfer?.files)
}

function onDragOver(e) {
  e.preventDefault()
  dragOver.value = true
}

function onDragLeave() {
  dragOver.value = false
}

function isPdf(file) {
  const nameOk = /\.pdf$/i.test(file.name)
  const typeOk = file.type === 'application/pdf' || file.type === ''
  return nameOk && typeOk
}

function handleFiles(fileList) {
  const files = Array.from(fileList || [])
  for (const file of files) {
    if (!isPdf(file)) {
      // Surface a client-side rejection row so the user sees why nothing happened.
      uploads.value.push({ file, progress: 0, error: 'Not a PDF — skipped.' })
      continue
    }
    startUpload(file)
  }
}

async function startUpload(file) {
  const entry = { file, progress: 0, error: null }
  uploads.value.push(entry)
  try {
    await documents.uploadDocument(file, {
      onProgress: (e) => {
        if (e.total) entry.progress = Math.round((e.loaded / e.total) * 100)
      },
    })
    // success — remove this entry shortly so the bar doesn't linger
    removeAfter(entry, 800)
  } catch (e) {
    entry.error = e?.response?.data?.detail || e?.message || 'Upload failed.'
  }
}

function removeAfter(entry, ms) {
  setTimeout(() => {
    uploads.value = uploads.value.filter((u) => u !== entry)
  }, ms)
}
</script>

<template>
  <div class="uploader">
    <div
      class="dropzone"
      :class="{ 'drag-over': dragOver }"
      role="button"
      tabindex="0"
      @click="openPicker"
      @keydown.enter="openPicker"
      @keydown.space.prevent="openPicker"
      @dragover="onDragOver"
      @dragleave="onDragLeave"
      @drop="onDrop"
    >
      <div class="dropzone-text">
        <span class="plus">＋</span>
        <span>Drop PDFs here or <span class="link">browse</span></span>
        <span class="hint">PDF only • multiple files supported</span>
      </div>
      <input
        ref="fileInput"
        type="file"
        multiple
        accept="application/pdf,.pdf"
        class="file-input"
        @change="onPick"
      />
    </div>

    <!-- Per-file progress / error list -->
    <ul v-if="uploads.length" class="upload-list">
      <li v-for="(u, i) in uploads" :key="i" class="upload-row">
        <span class="upload-name" :title="u.file.name">{{ u.file.name }}</span>
        <span v-if="u.error" class="upload-err">⚠️ {{ u.error }}</span>
        <span v-else-if="u.progress < 100" class="upload-prog-label">{{ u.progress }}%</span>
        <span v-else class="upload-prog-label done">uploaded</span>
        <div class="progress-track">
          <div
            class="progress-fill"
            :class="{ err: !!u.error }"
            :style="{ width: u.error ? '100%' : u.progress + '%' }"
          />
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.uploader {
  margin-top: 0.75rem;
}
.dropzone {
  border: 1.5px dashed var(--border, #c4cad6);
  border-radius: 0.6rem;
  padding: 1rem;
  text-align: center;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
  background: var(--drop-bg, #fafbfd);
}
.dropzone:hover,
.dropzone.drag-over {
  border-color: var(--accent, #2563eb);
  background: var(--drop-bg-active, #f0f5ff);
}
.dropzone-text {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  font-size: 0.88rem;
  color: var(--muted, #5b6472);
}
.plus {
  font-size: 1.3rem;
  font-weight: 700;
  color: var(--accent, #2563eb);
}
.link {
  text-decoration: underline;
  color: var(--accent, #2563eb);
}
.hint {
  font-size: 0.72rem;
  opacity: 0.7;
}
.file-input {
  display: none;
}

.upload-list {
  list-style: none;
  margin: 0.6rem 0 0;
  padding: 0;
}
.upload-row {
  margin-bottom: 0.4rem;
  font-size: 0.8rem;
  display: grid;
  grid-template-columns: 1fr auto;
  row-gap: 0.2rem;
  column-gap: 0.5rem;
  align-items: center;
}
.upload-name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.upload-err { color: #b42318; }
.upload-prog-label { color: var(--muted, #6b7280); font-size: 0.74rem; }
.upload-prog-label.done { color: #15803d; }
.progress-track {
  grid-column: 1 / -1;
  height: 4px;
  background: var(--border, #e2e6ee);
  border-radius: 2px;
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  background: var(--accent, #2563eb);
  transition: width 0.2s;
}
.progress-fill.err { background: #dc2626; }
</style>

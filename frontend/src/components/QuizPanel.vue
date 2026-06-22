<script setup>
// Quiz panel — generates questions from a document, lets the student take the
// quiz (one question at a time), grades each answer, and shows a final score.
// Active recall: the #1 evidence-based study technique.

import { computed, ref } from 'vue'
import { generateQuiz, answerQuizQuestion } from '../api/client'

const props = defineProps({
  documentId: { type: Number, required: true },
  filename: { type: String, default: '' },
})

const emit = defineEmits(['close'])

const loading = ref(false)
const error = ref(null)
const questions = ref([])
const currentIndex = ref(0)
const selectedOption = ref(null)
const lastResult = ref(null) // { correct, correct_index, explanation } after answering
const answeredCount = ref(0)
const correctCount = ref(0)

const activeQuestion = computed(() => questions.value[currentIndex.value])
const isLastQuestion = computed(() => currentIndex.value >= questions.value.length - 1)
const showResults = computed(() => answeredCount.value === questions.value.length && questions.value.length > 0)

async function startQuiz() {
  loading.value = true
  error.value = null
  questions.value = []
  currentIndex.value = 0
  answeredCount.value = 0
  correctCount.value = 0
  selectedOption.value = null
  lastResult.value = null
  try {
    questions.value = await generateQuiz(props.documentId, 5)
  } catch (e) {
    error.value = e?.response?.data?.detail || e?.message || 'Failed to generate quiz.'
  } finally {
    loading.value = false
  }
}

async function submitAnswer() {
  if (selectedOption.value === null || lastResult.value) return
  try {
    const result = await answerQuizQuestion(activeQuestion.value.id, selectedOption.value)
    lastResult.value = result
    answeredCount.value++
    if (result.correct) correctCount.value++
  } catch (e) {
    error.value = e?.response?.data?.detail || e?.message || 'Failed to submit answer.'
  }
}

function nextQuestion() {
  if (isLastQuestion.value) return // showResults will kick in
  currentIndex.value++
  selectedOption.value = null
  lastResult.value = null
}
</script>

<template>
  <div class="quiz-panel">
    <!-- Header -->
    <div class="quiz-header">
      <span class="quiz-title">📋 Quiz: {{ filename.slice(0, 30) }}</span>
      <button class="close-btn" @click="emit('close')">✕</button>
    </div>

    <!-- Not started: generate button -->
    <div v-if="!questions.length && !loading && !error" class="quiz-start">
      <p>Generate 5 multiple-choice questions from this document to test your understanding.</p>
      <button class="start-btn" @click="startQuiz">Start Quiz</button>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="quiz-loading">
      <p>🤔 Generating questions from your document...</p>
    </div>

    <!-- Error -->
    <div v-if="error && !loading" class="quiz-error">
      <p>⚠️ {{ error }}</p>
      <button @click="startQuiz">Retry</button>
    </div>

    <!-- Results -->
    <div v-if="showResults" class="quiz-results">
      <div class="score-circle" :class="{ pass: correctCount >= 3 }">
        {{ correctCount }}/{{ questions.length }}
      </div>
      <p class="score-label">
        {{ correctCount >= 4 ? 'Excellent!' : correctCount >= 3 ? 'Good job!' : 'Keep studying!' }}
      </p>
      <button class="retry-btn" @click="startQuiz">Try Again</button>
    </div>

    <!-- Question -->
    <div v-if="questions.length && !showResults && !loading" class="quiz-question-area">
      <div class="progress">
        Question {{ currentIndex + 1 }} of {{ questions.length }}
        <span class="score-so-far">({{ correctCount }} correct)</span>
      </div>

      <div class="question-text">{{ activeQuestion.question }}</div>

      <div class="options">
        <button
          v-for="(opt, i) in activeQuestion.options"
          :key="i"
          class="option"
          :class="{
            selected: selectedOption === i,
            correct: lastResult && i === lastResult.correct_index,
            wrong: lastResult && selectedOption === i && !lastResult.correct,
          }"
          :disabled="!!lastResult"
          @click="selectedOption = i"
        >
          <span class="option-letter">{{ String.fromCharCode(65 + i) }}</span>
          {{ opt }}
        </button>
      </div>

      <!-- Explanation after answering -->
      <div v-if="lastResult" class="explanation" :class="{ correct: lastResult.correct, wrong: !lastResult.correct }">
        <strong>{{ lastResult.correct ? '✅ Correct!' : '❌ Not quite.' }}</strong>
        <p v-if="lastResult.explanation">{{ lastResult.explanation }}</p>
      </div>

      <!-- Action buttons -->
      <div class="quiz-actions">
        <button
          v-if="!lastResult"
          class="submit-btn"
          :disabled="selectedOption === null"
          @click="submitAnswer"
        >
          Submit
        </button>
        <button
          v-if="lastResult && !isLastQuestion"
          class="next-btn"
          @click="nextQuestion"
        >
          Next →
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.quiz-panel {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 90%;
  max-width: 560px;
  max-height: 85vh;
  overflow-y: auto;
  background: #fff;
  border-radius: 0.75rem;
  box-shadow: 0 12px 40px rgba(15, 23, 42, 0.2);
  z-index: 100;
  padding: 1.5rem;
}
.quiz-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
}
.quiz-title { font-weight: 700; font-size: 0.95rem; }
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

.quiz-start, .quiz-loading, .quiz-error {
  text-align: center;
  padding: 2rem 1rem;
  color: var(--muted, #6b7280);
}
.start-btn, .retry-btn {
  margin-top: 1rem;
  padding: 0.6rem 1.5rem;
  border: none;
  border-radius: 0.5rem;
  background: var(--accent, #2563eb);
  color: #fff;
  font-weight: 600;
  cursor: pointer;
}
.start-btn:hover, .retry-btn:hover { background: #1d4ed8; }

.quiz-results {
  text-align: center;
  padding: 2rem 1rem;
}
.score-circle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 5rem;
  height: 5rem;
  border-radius: 50%;
  font-size: 1.4rem;
  font-weight: 800;
  background: #fee2e2;
  color: #b91c1c;
}
.score-circle.pass {
  background: #dcfce7;
  color: #15803d;
}
.score-label {
  margin-top: 0.75rem;
  font-size: 1.1rem;
  font-weight: 600;
}

.progress {
  font-size: 0.8rem;
  color: var(--muted, #6b7280);
  margin-bottom: 0.75rem;
}
.score-so-far { margin-left: 0.5rem; font-weight: 600; }
.question-text {
  font-size: 1rem;
  font-weight: 600;
  margin-bottom: 1rem;
  line-height: 1.5;
}
.options { display: flex; flex-direction: column; gap: 0.5rem; }
.option {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.6rem 0.8rem;
  border: 1.5px solid var(--border, #e2e6ee);
  border-radius: 0.5rem;
  background: #fff;
  cursor: pointer;
  text-align: left;
  font-size: 0.9rem;
  transition: border-color 0.12s, background 0.12s;
}
.option:hover:not(:disabled) {
  border-color: var(--accent, #2563eb);
}
.option.selected {
  border-color: var(--accent, #2563eb);
  background: var(--accent-soft, #e7eefd);
}
.option.correct {
  border-color: #16a34a;
  background: #dcfce7;
}
.option.wrong {
  border-color: #dc2626;
  background: #fee2e2;
}
.option:disabled { cursor: default; }
.option-letter {
  flex: 0 0 auto;
  width: 1.5rem;
  height: 1.5rem;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: var(--chip-bg, #eef1f6);
  font-weight: 700;
  font-size: 0.8rem;
}

.explanation {
  margin-top: 1rem;
  padding: 0.75rem;
  border-radius: 0.5rem;
  font-size: 0.85rem;
}
.explanation.correct { background: #f0fdf4; border: 1px solid #bbf7d0; }
.explanation.wrong { background: #fef2f2; border: 1px solid #fecaca; }

.quiz-actions {
  margin-top: 1rem;
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}
.submit-btn, .next-btn {
  padding: 0.5rem 1.5rem;
  border: none;
  border-radius: 0.5rem;
  font-weight: 600;
  cursor: pointer;
}
.submit-btn { background: var(--accent, #2563eb); color: #fff; }
.submit-btn:disabled { background: var(--border, #c4cad6); cursor: not-allowed; }
.next-btn { background: var(--chip-bg, #eef1f6); color: var(--accent, #2563eb); }
</style>

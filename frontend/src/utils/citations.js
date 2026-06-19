// Turns LLM inline citation markers ([1], [1][3]) in the rendered answer into
// hoverable <sup> elements that the frontend can bind a popup to.
//
// Also strips the cuneiform/dagger artifacts some models emit (【1†L1-L4】,
// 【1】, 〖1〗) down to a clean [1], so non-conforming output still renders.
//
// Indexing convention: the number INSIDE the brackets is 1-based and refers to
// the excerpt position in the prompt ([1] = first excerpt). The backend's
// `citations` array is in that same order, so cite number N maps to
// citations[N - 1]. CRITICAL: do not confuse "marker position in text" with
// "the number inside the brackets" — they're different things.

// Match [n] or [n][m]... runs. n is a positive integer.
const MARKER_RUN = /\[\d+\](?:\[\d+\])*/g
// Match cuneiform/dagger artifacts and collapse to a plain [n]:
//   【1†L1-L4】, 【1】, 〖1〗
const DAGGER = /[【〖]\s*(\d+)\s*(?:†[^\】〗]*)?[】〗]/g

// Private-use sentinel chars that survive markdown + DOMPurify intact.
const SENTINEL_OPEN = '\uE000'
const SENTINEL_CLOSE = '\uE001'

/**
 * Clean model artifacts (dagger ranges, cuneiform brackets) so they normalize
 * to [n]. e.g. "text 【1†L1-L4】 more" -> "text [1] more".
 */
export function normalizeCitationMarkers(text) {
  if (!text) return text
  return text.replace(DAGGER, (_, n) => `[${n}]`)
}

/**
 * Replace each [n] (or [n][m]...) marker with a sentinel carrying the FIRST
 * number in the group. We keep the first number because the popup shows one
 * excerpt; a [1][3] run means "supported by excerpts 1 and 3" — the most
 * relevant is the first (excerpt 1).
 *
 * Returns the text with markers replaced by sentinels.
 */
export function extractMarkers(text) {
  if (!text) return ''
  return text.replace(MARKER_RUN, (run) => {
    const firstNum = Number((run.match(/\d+/) || ['1'])[0])
    return SENTINEL_OPEN + firstNum + SENTINEL_CLOSE
  })
}

/**
 * Turn sentinel tokens in sanitized HTML into <sup class="inline-cite"> nodes.
 * Called AFTER marked + DOMPurify so we operate on safe HTML.
 * data-cite-num carries the 1-based excerpt number from inside the brackets;
 * the component maps it to citations[num - 1].
 */
export function renderSentinelsAsCites(html) {
  if (!html) return html
  const re = new RegExp(SENTINEL_OPEN + '(\\d+)' + SENTINEL_CLOSE, 'g')
  return html.replace(re, (_, num) => {
    return `<sup class="inline-cite" data-cite-num="${num}">[${num}]</sup>`
  })
}

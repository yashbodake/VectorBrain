// Turns LLM inline citation markers ([1], [1][3]) in the rendered answer into
// hoverable <sup> elements that the frontend can bind a popup to.
//
// Also strips the dagger-style artifacts some models emit (【1†L1-L4】) down to
// a clean [1], so a non-conforming model output still renders nicely.
//
// Markers reference excerpts by 1-based index in the order they were fed to the
// model — which matches the order of the `citations` array the backend returns
// in the done event (see backend _dedupe_citations: retrieval order, [1] first).

// Match [n] or [n][m]... runs. n is a positive integer. We deliberately don't
// match things inside code spans/links by keeping this simple — a stray [1] in
// code is rare and the worst case is a harmless superscript.
const MARKER_RUN = /\[\d+\](?:\[\d+\])*/g
// Match the bracket/cuneiform artifacts some models emit. We collapse any of
// these to a plain [n] so they then match MARKER_RUN and become hoverable:
//   【1†L1-L4】  (number + dagger + invented line range)
//   【1】        (number only, no dagger — gpt-oss emits this variant)
//   〖1〗        (alternate brackets)
const DAGGER = /[【〖]\s*(\d+)\s*(?:†[^\】〗]*)?[】〗]/g

/**
 * Clean model artifacts (dagger ranges) so they normalize to [n].
 * e.g. "text 【1†L1-L4】 more" -> "text [1] more".
 */
export function normalizeCitationMarkers(text) {
  if (!text) return text
  return text.replace(DAGGER, (_, n) => `[${n}]`)
}

/**
 * Replace [n] markers in a plain-text answer with placeholder tokens we can
 * revive after markdown rendering. We use a unique sentinel so marked's HTML
 * output won't escape it into oblivion.
 *
 * Returns { text, refs } where refs is the list of marker groups in order,
 * each a list of numbers, e.g. [[1], [1,3]]. The sentinel format is chosen so
 * it survives markdown -> HTML -> sanitize intact.
 */
const SENTINEL_OPEN = '\uE000' // private-use area; survives sanitize
const SENTINEL_CLOSE = '\uE001'

export function extractMarkers(text) {
  if (!text) return { text: '', refs: [] }
  const refs = []
  const out = text.replace(MARKER_RUN, (run) => {
    const nums = (run.match(/\d+/g) || []).map(Number)
    refs.push(nums)
    return SENTINEL_OPEN + (refs.length - 1) + SENTINEL_CLOSE
  })
  return { text: out, refs }
}

/**
 * Turn sentinel tokens in sanitized HTML into <sup class="inline-cite"> nodes.
 * Called AFTER marked + DOMPurify so we operate on safe HTML. Each node carries
 * data-cite-ids="1,3" so the component can look up excerpts on hover.
 */
export function renderSentinelsAsCites(html) {
  if (!html) return html
  // The sentinels are private-use chars; they pass through sanitize untouched.
  const re = new RegExp(SENTINEL_OPEN + '(\\d+)' + SENTINEL_CLOSE, 'g')
  return html.replace(re, (_, idx) => {
    // Placeholder content; the Vue component swaps in the superscript popup.
    return `<sup class="inline-cite" data-cite-group="${idx}">[${idx}]</sup>`
  })
}

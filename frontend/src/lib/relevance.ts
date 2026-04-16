/**
 * Lightweight character-n-gram similarity for file-name matching.
 *
 * Used by AgentPage to sort source-document candidates by relevance to the
 * uploaded template file name. Handles mixed Chinese/Latin characters.
 */

const GRAM_SIZE = 3;

function normalize(s: string): string {
  return s
    .toLowerCase()
    .replace(/\.[^.]+$/, '') // strip extension
    .replace(/[\s_\-—·()（）【】\[\]]+/g, ''); // remove common separators
}

function trigrams(s: string): Set<string> {
  const out = new Set<string>();
  const norm = normalize(s);
  if (norm.length === 0) return out;
  if (norm.length < GRAM_SIZE) {
    out.add(norm);
    return out;
  }
  for (let i = 0; i <= norm.length - GRAM_SIZE; i++) {
    out.add(norm.slice(i, i + GRAM_SIZE));
  }
  return out;
}

/**
 * Jaccard similarity over character trigrams, in [0, 1].
 */
export function charTrigramSimilarity(a: string, b: string): number {
  const A = trigrams(a);
  const B = trigrams(b);
  if (A.size === 0 || B.size === 0) return 0;
  let inter = 0;
  for (const g of A) if (B.has(g)) inter++;
  const union = A.size + B.size - inter;
  return union === 0 ? 0 : inter / union;
}

/**
 * Count how many of the template's token fragments appear as substrings
 * of the candidate's name. Kept as a coarse signal to break ties.
 */
export function keywordHitCount(templateName: string, candidateName: string): number {
  const base = templateName.replace(/\.[^.]+$/, '').toLowerCase();
  const tokens = base
    .split(/[\s_\-—·()（）【】\[\]0-9]+/)
    .filter((t) => t.length >= 2);
  const fn = candidateName.toLowerCase();
  return tokens.filter((tok) => fn.includes(tok)).length;
}

/**
 * Combined relevance score between an uploaded template file name and a
 * source-document file name.
 *
 * Weighting: 0.7 * trigram-similarity + 0.3 * normalized-keyword-hits.
 */
export function scoreDocumentRelevance(templateName: string, candidateName: string): number {
  const sim = charTrigramSimilarity(templateName, candidateName);
  const hits = keywordHitCount(templateName, candidateName);
  const hitsNorm = Math.min(1, hits / 3);
  return 0.7 * sim + 0.3 * hitsNorm;
}

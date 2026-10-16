// Action hash — SCRAM Build Spec, section 4.
// SHA-256 of canonical JSON (sorted keys, no whitespace) of
// { tool, target, normalized_parameters, expected_postcondition }.
// Python equivalent: rfc8785.dumps(obj), decoded as UTF-8.

type Json = string | number | boolean | null | Json[] | { [k: string]: Json };

export function canonicalJson(value: Json): string {
  if (Array.isArray(value)) return '[' + value.map(canonicalJson).join(',') + ']';
  if (value !== null && typeof value === 'object') {
    const keys = Object.keys(value).sort();
    return '{' + keys.map((k) => JSON.stringify(k) + ':' + canonicalJson((value as Record<string, Json>)[k])).join(',') + '}';
  }
  return JSON.stringify(value);
}

export async function actionHash(parts: {
  tool: string;
  target: string;
  normalized_parameters: Record<string, number | string>;
  expected_postcondition: Record<string, number | string>;
}): Promise<string> {
  const text = canonicalJson(parts as unknown as Json);
  const bytes = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

/** Display form: first 4 + last 4 hex characters. */
export function shortHash(hash: string | null | undefined): string {
  if (!hash) return '—';
  return hash.length > 8 ? `${hash.slice(0, 4)}…${hash.slice(-4)}` : hash;
}

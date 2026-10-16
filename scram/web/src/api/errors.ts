// Two kinds of failure, kept apart on purpose (design audit, item 1).
//  - 'refused': the server answered and said no. Its answer is evidence.
//  - 'network': no answer arrived. The request may or may not have been processed,
//    so the screen must treat the outcome as UNKNOWN and reconcile with the server.

export type ApiErrorKind = 'refused' | 'network';

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly code: string | null;

  constructor(kind: ApiErrorKind, message: string, status: number | null = null, code: string | null = null) {
    super(message);
    this.name = 'ApiError';
    this.kind = kind;
    this.status = status;
    this.code = code;
  }
}

export const isNetworkError = (e: unknown): boolean => e instanceof ApiError && e.kind === 'network';

export function errorCode(e: unknown): string {
  if (e instanceof ApiError) return e.code ?? (e.status ? `HTTP_${e.status}` : 'ERROR');
  return 'ERROR';
}

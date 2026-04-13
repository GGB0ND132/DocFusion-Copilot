import { requestJson } from '@/services/http';
import { buildApiUrl } from '@/services/http';
import type { BlockResponse, DocumentResponse, FactResponse, PaginatedBlocksResponse, PaginatedFactsResponse } from '@/services/types';

export async function listDocuments(): Promise<DocumentResponse[]> {
  return requestJson<DocumentResponse[]>('/api/v1/documents');
}

export async function getDocumentBlocks(
  docId: string,
  options?: { limit?: number; offset?: number },
): Promise<PaginatedBlocksResponse> {
  const params = new URLSearchParams();
  if (options?.limit != null) params.set('limit', String(options.limit));
  if (options?.offset != null) params.set('offset', String(options.offset));
  const qs = params.toString();
  const url = `/api/v1/documents/${encodeURIComponent(docId)}/blocks${qs ? `?${qs}` : ''}`;
  return requestJson<PaginatedBlocksResponse>(url);
}

export async function getDocumentFacts(
  docId: string,
  options?: { limit?: number; offset?: number },
): Promise<PaginatedFactsResponse> {
  const params = new URLSearchParams();
  if (options?.limit != null) params.set('limit', String(options.limit));
  if (options?.offset != null) params.set('offset', String(options.offset));
  const qs = params.toString();
  const url = `/api/v1/documents/${encodeURIComponent(docId)}/facts${qs ? `?${qs}` : ''}`;
  return requestJson<PaginatedFactsResponse>(url);
}

export async function deleteDocument(docId: string): Promise<{ doc_id: string; deleted: boolean }> {
  return requestJson<{ doc_id: string; deleted: boolean }>(`/api/v1/documents/${encodeURIComponent(docId)}`, {
    method: 'DELETE',
  });
}

export async function batchDeleteDocuments(docIds: string[]): Promise<{ deleted: string[]; errors: Array<{ doc_id: string; error: string }> }> {
  return requestJson('/api/v1/documents/batch-delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ doc_ids: docIds }),
  });
}

export function getDocumentRawUrl(docId: string): string {
  return buildApiUrl(`/api/v1/documents/${encodeURIComponent(docId)}/raw`);
}
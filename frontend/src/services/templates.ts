import { requestFile, requestJson } from '@/services/http';
import type { DownloadFileResult, SuggestDocumentsResponse } from '@/services/types';

export async function suggestDocuments(
  templateFile: File,
  documentSetId?: string,
): Promise<SuggestDocumentsResponse> {
  const formData = new FormData();
  formData.append('template_file', templateFile);
  if (documentSetId) formData.append('document_set_id', documentSetId);
  return requestJson<SuggestDocumentsResponse>('/api/v1/templates/suggest-documents', {
    method: 'POST',
    body: formData,
  });
}

export async function downloadTemplateResult(taskId: string): Promise<DownloadFileResult> {
  return requestFile(`/api/v1/templates/result/${encodeURIComponent(taskId)}`);
}
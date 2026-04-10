import { requestJson } from '@/services/http';
import type { DocumentBatchUploadAcceptedResponse } from '@/services/types';

export async function uploadDocumentBatch(
  files: File[],
  documentSetId?: string,
): Promise<DocumentBatchUploadAcceptedResponse> {
  const formData = new FormData();
  files.forEach((file) => {
    formData.append('files', file);
  });
  if (documentSetId) {
    formData.append('document_set_id', documentSetId);
  }

  return requestJson<DocumentBatchUploadAcceptedResponse>('/api/v1/documents/upload-batch', {
    method: 'POST',
    body: formData,
  });
}
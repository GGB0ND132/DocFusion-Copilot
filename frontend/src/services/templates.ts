import { requestFile, requestJson } from '@/services/http';
import type { DownloadFileResult, TemplateFillAcceptedResponse, TemplateFillRequest } from '@/services/types';

export async function submitTemplateFill(payload: TemplateFillRequest): Promise<TemplateFillAcceptedResponse> {
  const formData = new FormData();
  formData.append('template_file', payload.templateFile);
  formData.append('document_set_id', payload.documentSetId ?? 'default');
  formData.append('fill_mode', payload.fillMode ?? 'canonical');
  formData.append('auto_match', String(payload.autoMatch ?? true));

  if (payload.documentIds?.length) {
    formData.append('document_ids', payload.documentIds.join(','));
  }

  if (payload.userRequirement) {
    formData.append('user_requirement', payload.userRequirement);
  }

  return requestJson<TemplateFillAcceptedResponse>('/api/v1/templates/fill', {
    method: 'POST',
    body: formData,
  });
}

export async function downloadTemplateResult(taskId: string): Promise<DownloadFileResult> {
  return requestFile(`/api/v1/templates/result/${encodeURIComponent(taskId)}`);
}
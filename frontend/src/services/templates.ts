import { requestFile } from '@/services/http';
import type { DownloadFileResult } from '@/services/types';

export async function downloadTemplateResult(taskId: string): Promise<DownloadFileResult> {
  return requestFile(`/api/v1/templates/result/${encodeURIComponent(taskId)}`);
}
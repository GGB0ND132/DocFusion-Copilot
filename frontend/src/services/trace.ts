import { requestJson } from '@/services/http';
import type { FactTraceResponse } from '@/services/types';

export async function getFactTrace(factId: string): Promise<FactTraceResponse> {
  return requestJson<FactTraceResponse>(`/api/v1/facts/${encodeURIComponent(factId)}/trace`);
}
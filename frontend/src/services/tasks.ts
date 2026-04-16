import { requestJson } from '@/services/http';
import type { TaskResponse } from '@/services/types';

export async function getTaskStatus(taskId: string): Promise<TaskResponse> {
  return requestJson<TaskResponse>(`/api/v1/tasks/${encodeURIComponent(taskId)}`);
}

export async function listTasks(taskType?: string, limit: number = 100): Promise<TaskResponse[]> {
  const params = new URLSearchParams();
  if (taskType) params.set('type', taskType);
  params.set('limit', String(limit));
  return requestJson<TaskResponse[]>(`/api/v1/tasks?${params.toString()}`);
}

export async function deleteTask(taskId: string): Promise<void> {
  await requestJson<{ task_id: string; deleted: boolean }>(
    `/api/v1/tasks/${encodeURIComponent(taskId)}`,
    { method: 'DELETE' },
  );
}
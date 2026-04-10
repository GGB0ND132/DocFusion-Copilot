import { requestFile, requestJson } from '@/services/http';
import type {
  AgentExecuteRequest,
  AgentExecuteResponse,
  ConversationCreateRequest,
  ConversationResponse,
  DownloadFileResult,
} from '@/services/types';

export async function runAgentExecute(payload: AgentExecuteRequest): Promise<AgentExecuteResponse> {
  if (payload.templateFile) {
    const formData = new FormData();
    formData.append('message', payload.message);
    if (payload.contextId) {
      formData.append('context_id', payload.contextId);
    }
    if (payload.documentSetId) {
      formData.append('document_set_id', payload.documentSetId);
    }
    if (payload.documentIds?.length) {
      formData.append('document_ids', payload.documentIds.join(','));
    }
    formData.append('fill_mode', payload.fillMode ?? 'canonical');
    formData.append('auto_match', String(payload.autoMatch ?? true));
    if (payload.userRequirement) {
      formData.append('user_requirement', payload.userRequirement);
    }
    formData.append('template_file', payload.templateFile);

    return requestJson<AgentExecuteResponse>('/api/v1/agent/execute', {
      method: 'POST',
      body: formData,
    });
  }

  return requestJson<AgentExecuteResponse>('/api/v1/agent/execute', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      message: payload.message,
      context_id: payload.contextId ?? null,
      document_ids: payload.documentIds ?? [],
      document_set_id: payload.documentSetId ?? null,
      fill_mode: payload.fillMode ?? 'canonical',
      auto_match: payload.autoMatch ?? true,
    }),
  });
}

export async function downloadAgentArtifact(fileName: string): Promise<DownloadFileResult> {
  return requestFile(`/api/v1/agent/artifacts/${encodeURIComponent(fileName)}`);
}

export async function clearAgentConversation(contextId: string): Promise<void> {
  await requestJson<{ context_id: string; cleared: boolean }>(
    `/api/v1/agent/conversations/${encodeURIComponent(contextId)}`,
    { method: 'DELETE' },
  );
}

// ── Conversation CRUD ──

export async function listConversations(): Promise<ConversationResponse[]> {
  return requestJson<ConversationResponse[]>('/api/v1/agent/conversations');
}

export async function createConversation(payload?: ConversationCreateRequest): Promise<ConversationResponse> {
  return requestJson<ConversationResponse>('/api/v1/agent/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload ?? {}),
  });
}

export async function getConversation(conversationId: string): Promise<ConversationResponse> {
  return requestJson<ConversationResponse>(
    `/api/v1/agent/conversations/${encodeURIComponent(conversationId)}`,
  );
}

export async function deleteConversation(conversationId: string): Promise<void> {
  await requestJson<{ context_id: string; cleared: boolean }>(
    `/api/v1/agent/conversations/${encodeURIComponent(conversationId)}`,
    { method: 'DELETE' },
  );
}
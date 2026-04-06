import { create } from 'zustand';
import { getFactTrace } from '@/services';
import type { AgentExecuteResponse, ConversationResponse, DocumentResponse, FactTraceResponse, TaskResponse } from '@/services';

type ToastTone = 'success' | 'error' | 'info';

type ToastItem = {
  id: string;
  title: string;
  message: string;
  tone: ToastTone;
};

type UploadedDocumentEntry = {
  taskId: string;
  status: string;
  fileSizeText: string;
  document: DocumentResponse;
};

type TracePanelState = {
  factId: string | null;
  cellLabel: string | null;
  loading: boolean;
  error: string | null;
  data: FactTraceResponse | null;
};

export type ChatMessage =
  | { role: 'user'; text: string; timestamp: number }
  | { role: 'assistant'; text: string; timestamp: number; data?: AgentExecuteResponse | null; taskId?: string }
  | { role: 'system'; text: string; timestamp: number };

const AGENT_WELCOME_MESSAGE =
  '你好，我是 DocFusion Agent。我可以帮你总结已上传文档、查询结构化事实、追溯来源，也可以在你上传 Word 或 Excel 模板后自动回填。';

type UiState = {
  uploadedDocuments: UploadedDocumentEntry[];
  currentDocumentSetId: string | null;
  selectedTemplateFile: File | null;
  selectedTemplateName: string | null;
  taskSnapshots: Record<string, TaskResponse>;
  latestDocumentTaskId: string | null;
  latestTemplateTaskId: string | null;
  tracePanel: TracePanelState;
  toasts: ToastItem[];
  addUploadedDocument: (entry: UploadedDocumentEntry) => void;
  addUploadedDocuments: (entries: UploadedDocumentEntry[], documentSetId: string | null) => void;
  setCurrentDocumentSetId: (documentSetId: string | null) => void;
  setSelectedTemplateFile: (file: File | null) => void;
  upsertTaskSnapshot: (task: TaskResponse) => void;
  setLatestTemplateTaskId: (taskId: string | null) => void;
  openTraceByFactId: (factId: string, cellLabel?: string | null) => Promise<void>;
  closeTrace: () => void;
  pushToast: (toast: Omit<ToastItem, 'id'>) => void;
  dismissToast: (toastId: string) => void;
  clearFileCache: () => void;
  removeUploadedDocument: (docId: string) => void;
  agentMessages: ChatMessage[];
  agentContextId: string | null;
  addAgentMessage: (msg: ChatMessage) => void;
  setAgentContextId: (id: string | null) => void;
  clearAgentConversation: () => void;
  conversationList: ConversationResponse[];
  setConversationList: (list: ConversationResponse[]) => void;
  switchConversation: (conv: ConversationResponse) => void;
  startNewConversation: () => void;
  removeConversationFromList: (conversationId: string) => void;
};

export const useUiStore = create<UiState>((set) => ({
  uploadedDocuments: [],
  currentDocumentSetId: null,
  selectedTemplateFile: null,
  selectedTemplateName: null,
  taskSnapshots: {},
  latestDocumentTaskId: null,
  latestTemplateTaskId: null,
  tracePanel: {
    factId: null,
    cellLabel: null,
    loading: false,
    error: null,
    data: null,
  },
  toasts: [],
  addUploadedDocument: (entry) =>
    set((state) => ({
      uploadedDocuments: [entry, ...state.uploadedDocuments.filter((item) => item.document.doc_id !== entry.document.doc_id)],
      latestDocumentTaskId: entry.taskId,
      currentDocumentSetId: String(entry.document.metadata.document_set_id ?? state.currentDocumentSetId ?? 'default'),
    })),
  addUploadedDocuments: (entries, documentSetId) =>
    set((state) => ({
      uploadedDocuments: [
        ...entries,
        ...state.uploadedDocuments.filter(
          (item) => !entries.some((entry) => entry.document.doc_id === item.document.doc_id),
        ),
      ],
      latestDocumentTaskId: entries[0]?.taskId ?? state.latestDocumentTaskId,
      currentDocumentSetId: documentSetId ?? state.currentDocumentSetId,
    })),
  setCurrentDocumentSetId: (documentSetId) => set({ currentDocumentSetId: documentSetId }),
  setSelectedTemplateFile: (file) =>
    set({
      selectedTemplateFile: file,
      selectedTemplateName: file?.name ?? null,
    }),
  upsertTaskSnapshot: (task) =>
    set((state) => ({
      taskSnapshots: {
        ...state.taskSnapshots,
        [task.task_id]: task,
      },
    })),
  setLatestTemplateTaskId: (taskId) => set({ latestTemplateTaskId: taskId }),
  openTraceByFactId: async (factId, cellLabel) => {
    set({
      tracePanel: {
        factId,
        cellLabel: cellLabel ?? null,
        loading: true,
        error: null,
        data: null,
      },
    });

    try {
      const data = await getFactTrace(factId);
      set({
        tracePanel: {
          factId,
          cellLabel: cellLabel ?? null,
          loading: false,
          error: null,
          data,
        },
      });
    } catch (error) {
      set({
        tracePanel: {
          factId,
          cellLabel: cellLabel ?? null,
          loading: false,
          error: error instanceof Error ? error.message : '来源追溯查询失败。',
          data: null,
        },
      });
    }
  },
  closeTrace: () =>
    set({
      tracePanel: {
        factId: null,
        cellLabel: null,
        loading: false,
        error: null,
        data: null,
      },
    }),
  pushToast: (toast) => {
    const toastId = `toast_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;
    set((state) => ({
      toasts: [...state.toasts, { id: toastId, ...toast }],
    }));

    window.setTimeout(() => {
      set((state) => ({
        toasts: state.toasts.filter((item) => item.id !== toastId),
      }));
    }, 3200);
  },
  dismissToast: (toastId) =>
    set((state) => ({
      toasts: state.toasts.filter((item) => item.id !== toastId),
    })),
  clearFileCache: () =>
    set({
      uploadedDocuments: [],
      currentDocumentSetId: null,
      selectedTemplateFile: null,
      selectedTemplateName: null,
      taskSnapshots: {},
      latestDocumentTaskId: null,
      latestTemplateTaskId: null,
    }),
  removeUploadedDocument: (docId) =>
    set((state) => ({
      uploadedDocuments: state.uploadedDocuments.filter((item) => item.document.doc_id !== docId),
    })),
  agentMessages: [
    { role: 'system' as const, text: AGENT_WELCOME_MESSAGE, timestamp: Date.now() },
  ],
  agentContextId: null,
  addAgentMessage: (msg) =>
    set((state) => ({ agentMessages: [...state.agentMessages, msg] })),
  setAgentContextId: (id) => set({ agentContextId: id }),
  clearAgentConversation: () =>
    set({
      agentMessages: [
        { role: 'system' as const, text: AGENT_WELCOME_MESSAGE, timestamp: Date.now() },
      ],
      agentContextId: null,
    }),
  conversationList: [],
  setConversationList: (list) => set({ conversationList: list }),
  switchConversation: (conv) =>
    set(() => {
      const restored: ChatMessage[] = [
        { role: 'system' as const, text: AGENT_WELCOME_MESSAGE, timestamp: new Date(conv.created_at).getTime() },
        ...conv.messages.map((m) => ({
          role: (String(m.role) === 'user' ? 'user' : 'assistant') as 'user' | 'assistant',
          text: String(m.content ?? ''),
          timestamp: Date.now(),
        })),
      ];
      return { agentMessages: restored, agentContextId: conv.conversation_id };
    }),
  startNewConversation: () =>
    set({
      agentMessages: [
        { role: 'system' as const, text: AGENT_WELCOME_MESSAGE, timestamp: Date.now() },
      ],
      agentContextId: null,
    }),
  removeConversationFromList: (conversationId) =>
    set((state) => ({
      conversationList: state.conversationList.filter((c) => c.conversation_id !== conversationId),
    })),
}));

export type ApiDateString = string;

export interface DocumentResponse {
  doc_id: string;
  file_name: string;
  stored_path: string;
  doc_type: string;
  upload_time: ApiDateString;
  status: string;
  metadata: Record<string, unknown>;
}

export interface BlockResponse {
  block_id: string;
  doc_id: string;
  block_type: string;
  text: string;
  section_path: string[];
  page_or_index: number | null;
  metadata: Record<string, unknown>;
}

export interface PaginatedBlocksResponse {
  items: BlockResponse[];
  total: number;
  offset: number;
  limit: number | null;
}

export interface PaginatedFactsResponse {
  items: FactResponse[];
  total: number;
  offset: number;
  limit: number;
}

export interface FactResponse {
  fact_id: string;
  entity_type: string;
  entity_name: string;
  field_name: string;
  value_num: number | null;
  value_text: string;
  unit: string | null;
  year: number | null;
  source_doc_id: string;
  source_block_id: string;
  source_span: string;
  confidence: number;
  conflict_group_id: string | null;
  is_canonical: boolean;
  status: string;
  metadata: Record<string, unknown>;
}

export interface TaskResponse {
  task_id: string;
  task_type: string;
  status: string;
  created_at: ApiDateString;
  updated_at: ApiDateString;
  progress: number;
  message: string;
  error: string | null;
  result: Record<string, unknown>;
}

export interface DocumentBatchUploadItemResponse {
  task_id: string;
  status: string;
  document: DocumentResponse;
}

export interface DocumentBatchUploadAcceptedResponse {
  document_set_id: string;
  items: DocumentBatchUploadItemResponse[];
}

export interface FilledCellResponse {
  sheet_name: string;
  cell_ref: string;
  entity_name: string;
  field_name: string;
  value: string | number;
  fact_id: string;
  confidence: number;
  evidence_text?: string;
}

export interface TemplateResultResponse {
  task_id: string;
  template_name: string;
  output_path: string;
  output_file_name: string;
  created_at: ApiDateString;
  fill_mode: string;
  document_ids: string[];
  filled_cells: FilledCellResponse[];
}

export interface FactTraceResponse {
  fact: FactResponse;
  document: DocumentResponse | null;
  block: BlockResponse | null;
  usages: Array<Record<string, unknown>>;
}

export interface AgentExecuteRequest {
  message: string;
  contextId?: string;
  documentIds?: string[];
  documentSetId?: string;
  fillMode?: string;
  autoMatch?: boolean;
  templateFile?: File | null;
  userRequirement?: string;
}

export interface AgentExecutionArtifactResponse {
  doc_id: string;
  operation: string;
  file_name: string;
  output_path: string;
  change_count?: number | null;
}

export interface AgentExecuteResponse {
  intent: string;
  entities: string[];
  fields: string[];
  target: string;
  need_db_store: boolean;
  context_id: string | null;
  preview: Array<Record<string, unknown>>;
  edits: Array<Record<string, string>>;
  planner: string;
  execution_type: string;
  summary: string;
  facts: FactResponse[];
  artifacts: AgentExecutionArtifactResponse[];
  document_ids: string[];
  task_id?: string | null;
  task_status?: string | null;
  template_name?: string | null;
}

// ── Template suggest types ──

export interface SuggestDocumentCandidate {
  doc_id: string;
  file_name: string;
  score: number;
  field_hits: string[];
  entity_hits: string[];
  keyword_hits: string[];
  recommended: boolean;
}

export interface SuggestDocumentsResponse {
  template_profile: {
    template_name: string;
    field_names: string[];
    entity_names: string[];
  };
  candidates: SuggestDocumentCandidate[];
  match_reason?: string;
  message?: string;
}

export interface DownloadFileResult {
  blob: Blob;
  fileName: string;
}

// ── Conversation types ──

export interface ConversationResponse {
  conversation_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: Array<Record<string, unknown>>;
  metadata: Record<string, unknown>;
}

export interface ConversationCreateRequest {
  title?: string;
  metadata?: Record<string, unknown>;
}
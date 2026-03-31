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

export interface DocumentUploadAcceptedResponse {
  task_id: string;
  status: string;
  document: DocumentResponse;
  document_set_id?: string | null;
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

export interface TemplateFillAcceptedResponse {
  task_id: string;
  status: string;
  template_name: string;
  document_set_id?: string | null;
  auto_match?: boolean;
}

export interface FilledCellResponse {
  sheet_name: string;
  cell_ref: string;
  entity_name: string;
  field_name: string;
  value: string | number;
  fact_id: string;
  confidence: number;
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

export interface FactReviewRequest {
  status: string;
  reviewer?: string;
  note?: string;
}

export interface FactQueryParams {
  entityName?: string;
  fieldName?: string;
  status?: string;
  minConfidence?: number;
  canonicalOnly?: boolean;
  documentIds?: string[];
}

export interface FactTraceResponse {
  fact: FactResponse;
  document: DocumentResponse | null;
  block: BlockResponse | null;
  usages: Array<Record<string, unknown>>;
}

export interface AgentChatRequest {
  message: string;
  contextId?: string;
}

export interface AgentChatResponse {
  intent: string;
  entities: string[];
  fields: string[];
  target: string;
  need_db_store: boolean;
  context_id: string | null;
  preview: Array<Record<string, unknown>>;
  edits: Array<Record<string, string>>;
  planner: string;
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

export interface FactEvaluationAcceptedResponse {
  task_id: string;
  status: string;
  annotation_name: string;
}

export interface TemplateBenchmarkAcceptedResponse {
  task_id: string;
  status: string;
  template_name: string;
  expected_result_name: string;
}

export interface BenchmarkReportResponse {
  task_id: string;
  task_type: string;
  report: Record<string, unknown>;
}

export interface FactEvaluationRequest {
  annotationFile: File;
  documentIds?: string[];
  canonicalOnly?: boolean;
  minConfidence?: number;
}

export interface TemplateBenchmarkRequest {
  templateFile: File;
  expectedResultFile: File;
  documentSetId?: string;
  fillMode?: string;
  documentIds?: string[];
}

export interface TemplateFillRequest {
  templateFile: File;
  documentSetId?: string;
  fillMode?: string;
  documentIds?: string[];
  autoMatch?: boolean;
  userRequirement?: string;
}

export interface DownloadFileResult {
  blob: Blob;
  fileName: string;
}
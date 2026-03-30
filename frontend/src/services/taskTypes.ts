const TEMPLATE_FILL_TASK_TYPES = new Set(['fill_template', 'template_fill']);

const BENCHMARK_TASK_TYPES = new Set(['evaluate_facts', 'benchmark_template_fill']);

export function isTemplateFillTask(taskType: string | null | undefined): boolean {
  return TEMPLATE_FILL_TASK_TYPES.has(String(taskType ?? ''));
}

export function isDocumentParseTask(taskType: string | null | undefined): boolean {
  return String(taskType ?? '') === 'parse_document';
}

export function isBenchmarkTask(taskType: string | null | undefined): boolean {
  return BENCHMARK_TASK_TYPES.has(String(taskType ?? ''));
}

export function getTaskTypeLabel(taskType: string | null | undefined): string {
  if (isTemplateFillTask(taskType)) {
    return '模板回填';
  }
  if (isDocumentParseTask(taskType)) {
    return '文档解析';
  }
  if (isBenchmarkTask(taskType)) {
    return '评测任务';
  }
  return '后台任务';
}

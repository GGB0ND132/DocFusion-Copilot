import { useEffect, useMemo, useState } from 'react';
import AppButton from '@/components/AppButton';
import EmptyStateCard from '@/components/EmptyStateCard';
import ErrorStateCard from '@/components/ErrorStateCard';
import LoadingStateCard from '@/components/LoadingStateCard';
import { downloadTemplateResult, getTaskStatus, submitTemplateFill, type FilledCellResponse } from '@/services';
import { useUiStore } from '@/stores/uiStore';

export default function ResultPreviewPage() {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [pageError, setPageError] = useState<string | null>(null);
  const [traceFactIdInput, setTraceFactIdInput] = useState('');
  const uploadedDocuments = useUiStore((state) => state.uploadedDocuments);
  const currentDocumentSetId = useUiStore((state) => state.currentDocumentSetId);
  const selectedTemplateFile = useUiStore((state) => state.selectedTemplateFile);
  const selectedTemplateName = useUiStore((state) => state.selectedTemplateName);
  const latestTemplateTaskId = useUiStore((state) => state.latestTemplateTaskId);
  const taskSnapshots = useUiStore((state) => state.taskSnapshots);
  const upsertTaskSnapshot = useUiStore((state) => state.upsertTaskSnapshot);
  const setLatestTemplateTaskId = useUiStore((state) => state.setLatestTemplateTaskId);
  const openTraceByFactId = useUiStore((state) => state.openTraceByFactId);
  const pushToast = useUiStore((state) => state.pushToast);
  const [fillMode, setFillMode] = useState('canonical');
  const [autoMatch, setAutoMatch] = useState(true);
  const [documentSetIdInput, setDocumentSetIdInput] = useState(currentDocumentSetId ?? 'default');
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);

  const currentTemplateTask = latestTemplateTaskId ? taskSnapshots[latestTemplateTaskId] : undefined;
  const uploadedDocumentIds = useMemo(() => uploadedDocuments.map((item) => item.document.doc_id), [uploadedDocuments]);
  const templateTaskResult = useMemo(() => normalizeTemplateTaskResult(currentTemplateTask?.result), [currentTemplateTask?.result]);

  useEffect(() => {
    setDocumentSetIdInput(currentDocumentSetId ?? 'default');
  }, [currentDocumentSetId]);

  useEffect(() => {
    if (!uploadedDocumentIds.length) {
      setSelectedDocumentIds([]);
      return;
    }

    setSelectedDocumentIds((current) => {
      const existing = current.filter((docId) => uploadedDocumentIds.includes(docId));
      return existing.length ? existing : uploadedDocumentIds;
    });
  }, [uploadedDocumentIds]);

  useEffect(() => {
    if (!latestTemplateTaskId) {
      return undefined;
    }

    const task = taskSnapshots[latestTemplateTaskId];
    if (!task || ['succeeded', 'failed', 'completed'].includes(task.status)) {
      return undefined;
    }

    const timer = window.setInterval(async () => {
      try {
        const latestTask = await getTaskStatus(latestTemplateTaskId);
        upsertTaskSnapshot(latestTask);
      } catch {
        window.clearInterval(timer);
      }
    }, 3000);

    return () => window.clearInterval(timer);
  }, [latestTemplateTaskId, taskSnapshots, upsertTaskSnapshot]);

  async function handleSubmitFill() {
    if (!selectedTemplateFile) {
      setPageError('请先在上传页选择模板文件。');
      return;
    }
    if (!uploadedDocumentIds.length) {
      setPageError('请先上传至少一个原始文档。');
      return;
    }

    setIsSubmitting(true);
    setPageError(null);

    try {
      const response = await submitTemplateFill({
        templateFile: selectedTemplateFile,
        documentSetId: documentSetIdInput.trim() || 'default',
        fillMode,
        documentIds: autoMatch ? undefined : selectedDocumentIds,
        autoMatch,
      });
      setLatestTemplateTaskId(response.task_id);

      const task = await getTaskStatus(response.task_id);
      upsertTaskSnapshot(task);
      pushToast({
        title: '模板回填任务已提交',
        message: `${response.template_name} 已生成任务 ${response.task_id}。`,
        tone: 'success',
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : '模板回填提交失败。';
      setPageError(message);
      pushToast({
        title: '回填提交失败',
        message,
        tone: 'error',
      });
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleDownload() {
    if (!latestTemplateTaskId) {
      setPageError('当前没有可下载的回填任务。');
      return;
    }

    setIsDownloading(true);
    setPageError(null);

    try {
      const file = await downloadTemplateResult(latestTemplateTaskId);
      const url = window.URL.createObjectURL(file.blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = file.fileName;
      anchor.click();
      window.URL.revokeObjectURL(url);
      pushToast({
        title: '结果已开始下载',
        message: file.fileName,
        tone: 'info',
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : '下载结果文件失败。';
      setPageError(message);
      pushToast({
        title: '下载失败',
        message,
        tone: 'error',
      });
    } finally {
      setIsDownloading(false);
    }
  }

  async function handleTraceLookup() {
    const trimmedFactId = traceFactIdInput.trim();
    if (!trimmedFactId) {
      setPageError('请输入要查询的 fact_id。');
      return;
    }
    if (!trimmedFactId.startsWith('fact_')) {
      setPageError('来源追溯接口只接受 fact_id。当前输入看起来像 document_set_id、doc_id 或 task_id。');
      pushToast({
        title: '追溯参数不正确',
        message: '请传入 fact_id，例如 fact_xxx；document_set_id 不能用于 /facts/{fact_id}/trace。',
        tone: 'error',
      });
      return;
    }

    setPageError(null);
    await openTraceByFactId(trimmedFactId, null);
  }

  return (
    <div className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
      <section className="glass-panel rounded-[28px] border border-white/70 p-6 shadow-card">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">步骤 03</div>
            <h3 className="mt-2 text-2xl font-semibold text-ink">结果预览</h3>
          </div>
          <div className="flex flex-wrap gap-3">
            <AppButton
              onClick={handleDownload}
              variant="secondary"
              disabled={!currentTemplateTask || !['succeeded', 'completed', 'success'].includes(currentTemplateTask.status) || isDownloading}
              loading={isDownloading}
              loadingText="下载中..."
            >
              导出结果
            </AppButton>
            <AppButton
              onClick={handleSubmitFill}
              loading={isSubmitting}
              loadingText="提交中..."
            >
              提交模板回填
            </AppButton>
          </div>
        </div>

        {isSubmitting ? (
          <div className="mt-4">
            <LoadingStateCard title="正在提交回填任务" description="模板文件与文档集合正在发送到后端，请稍候。" />
          </div>
        ) : null}

        {isDownloading ? (
          <div className="mt-4">
            <LoadingStateCard title="正在准备下载" description="前端正在接收回填结果文件流，并准备保存到本地。" />
          </div>
        ) : null}

        {pageError ? <div className="mt-4"><ErrorStateCard title="操作失败" description={pageError} /></div> : null}

        {!selectedTemplateFile ? (
          <div className="mt-6">
            <EmptyStateCard title="还没有模板文件" description="先回到上传页选择一个模板文件，结果页才可以提交模板回填任务。" />
          </div>
        ) : null}

        <div className="mt-6 grid gap-4 md:grid-cols-3">
          <InfoCard title="当前模板" value={selectedTemplateName ?? '未选择'} desc="模板文件在上传页选择" />
          <InfoCard
            title="回填任务"
            value={latestTemplateTaskId ?? '未提交'}
            desc={currentTemplateTask ? currentTemplateTask.message : '提交后会生成 task_id'}
          />
          <InfoCard
            title="已填充单元格"
            value={String(templateTaskResult.filledCells.length)}
            desc={templateTaskResult.outputFileName ?? '等待任务完成'}
          />
        </div>

        <div className="mt-6 rounded-[24px] border border-white/70 bg-white/80 p-5">
          <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-400">回填控制项</div>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <label className="block">
              <div className="mb-2 text-sm font-semibold text-ink">document_set_id</div>
              <input
                value={documentSetIdInput}
                onChange={(event) => setDocumentSetIdInput(event.target.value)}
                className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 outline-none transition focus:border-teal"
                placeholder="default 或上传返回的 document_set_id"
              />
            </label>
            <label className="block">
              <div className="mb-2 text-sm font-semibold text-ink">fill_mode</div>
              <select
                value={fillMode}
                onChange={(event) => setFillMode(event.target.value)}
                className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 outline-none transition focus:border-teal"
              >
                <option value="canonical">canonical</option>
                <option value="candidate">candidate</option>
              </select>
            </label>
          </div>

          <label className="mt-4 flex items-center gap-3 rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-700">
            <input type="checkbox" checked={autoMatch} onChange={(event) => setAutoMatch(event.target.checked)} className="h-4 w-4 accent-teal" />
            自动匹配文档。关闭后将使用下方手动勾选的 document_ids。
          </label>

          <div className="mt-4 rounded-2xl border border-dashed border-slate-200 p-4">
            <div className="text-sm font-semibold text-ink">document_ids</div>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              {uploadedDocuments.length ? (
                uploadedDocuments.map((item) => (
                  <label key={item.document.doc_id} className="flex items-start gap-3 rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={selectedDocumentIds.includes(item.document.doc_id)}
                      disabled={autoMatch}
                      onChange={() => toggleDocumentId(item.document.doc_id, setSelectedDocumentIds)}
                      className="mt-1 h-4 w-4 accent-teal"
                    />
                    <span>
                      <span className="block font-semibold text-ink">{item.document.file_name}</span>
                      <span className="block text-xs text-slate-500">{item.document.doc_id}</span>
                    </span>
                  </label>
                ))
              ) : (
                <div className="text-sm text-slate-500">当前没有可选文档，请先在上传页建立文档批次。</div>
              )}
            </div>
          </div>
        </div>

        <div className="mt-6 overflow-hidden rounded-[24px] border border-slate-200 bg-white">
          <div className="border-b border-slate-100 bg-slate-50 px-4 py-4 text-sm font-semibold text-slate-600">模板回填任务快照</div>
          <div className="grid gap-4 p-4 md:grid-cols-2">
            <Snapshot label="任务状态" value={currentTemplateTask?.status ?? '未提交'} />
            <Snapshot label="进度" value={`${Math.round((currentTemplateTask?.progress ?? 0) * 100)}%`} />
            <Snapshot label="输出文件" value={templateTaskResult.outputFileName ?? '暂无'} />
            <Snapshot label="批次标识" value={documentSetIdInput || 'default'} />
            <Snapshot label="匹配文档" value={templateTaskResult.matchedDocumentIds.join(', ') || '等待匹配'} />
            <Snapshot label="耗时" value={templateTaskResult.elapsedSeconds ? `${templateTaskResult.elapsedSeconds}s` : '等待完成'} />
            <Snapshot label="错误信息" value={currentTemplateTask?.error ?? '无'} />
          </div>
          <div className="border-t border-slate-100 px-4 py-4">
            {templateTaskResult.filledCells.length ? (
              <div>
                <div className="text-sm font-semibold text-ink">已回填单元格</div>
                <div className="mt-3 space-y-3">
                  {templateTaskResult.filledCells.slice(0, 8).map((cell) => (
                    <div key={`${cell.sheet_name}-${cell.cell_ref}-${cell.fact_id}`} className="rounded-2xl bg-slate-50 px-4 py-4">
                      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                        <div>
                          <div className="text-sm font-semibold text-ink">{cell.sheet_name} / {cell.cell_ref}</div>
                          <div className="mt-1 text-sm text-slate-600">{cell.entity_name} · {cell.field_name} · {String(cell.value)}</div>
                          <div className="mt-1 text-xs text-slate-500">fact_id: {cell.fact_id} · confidence: {(cell.confidence * 100).toFixed(1)}%</div>
                        </div>
                        <AppButton size="sm" variant="ghost" onClick={() => openTraceByFactId(cell.fact_id, `${cell.sheet_name} ${cell.cell_ref}`)}>
                          查看追溯
                        </AppButton>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div className="text-sm leading-7 text-slate-600">
                当前页面已经开始展示真实任务元信息、匹配文档和 filled_cells。若任务尚未完成，这里会在轮询后自动刷新。
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="space-y-6">
        <div className="glass-panel rounded-[28px] border border-white/70 p-6 shadow-card">
          <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">来源追溯查询</div>
          <div className="mt-4 flex gap-3">
            <input
              value={traceFactIdInput}
              onChange={(event) => setTraceFactIdInput(event.target.value)}
              placeholder="输入 fact_id（例如 fact_xxx）后查询来源"
              className="flex-1 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 outline-none transition focus:border-teal"
            />
            <AppButton
              onClick={handleTraceLookup}
              variant="accent"
            >
              查询
            </AppButton>
          </div>
          <ul className="mt-4 space-y-3 text-sm leading-7 text-slate-600">
            <li>当前 trace 接口是 GET /api/v1/facts/{'{fact_id}'}/trace。</li>
            <li>document_set_id、doc_id、task_id 都不能代替 fact_id 调用追溯接口。</li>
            <li>README.txt 这类提示词文件不会生成 fact_id，因此不会出现在追溯和模板回填结果中。</li>
            <li>模板结果下载接口返回的是文件流，trace 需要基于 fact_id 单独查询。</li>
            <li>当前页面已经把追溯按钮挂到 filled_cells 上，便于直接回看证据链。</li>
          </ul>
        </div>

        <div className="glass-panel rounded-[28px] border border-white/70 p-6 shadow-card">
          <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">联调说明</div>
          <div className="mt-4 rounded-2xl bg-amber-50 p-4 text-sm leading-7 text-amber-900">
            结果页提交模板回填时会优先使用当前批次的 document_set_id。若同一批次里包含 README.txt 这类提示词文档，后端会自动将其排除，不影响真实数据回填。
          </div>

          <div className="mt-4 rounded-2xl border border-dashed border-slate-300 p-4 text-sm leading-7 text-slate-600">
            当前这页更适合验证三件事：批次是否正确传递、候选文档是否正确过滤、回填结果里的 filled_cells 是否能正常追溯。
          </div>
        </div>
      </section>
    </div>
  );
}

function Snapshot({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl bg-slate-50 px-4 py-4">
      <div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">{label}</div>
      <div className="mt-3 break-all text-base font-medium text-ink">{value}</div>
    </div>
  );
}

function InfoCard({ title, value, desc }: { title: string; value: string; desc: string }) {
  return (
    <div className="rounded-2xl border border-white/80 bg-white/85 px-4 py-4">
      <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-400">{title}</div>
      <div className="mt-3 break-all text-2xl font-semibold text-ink">{value}</div>
      <div className="mt-2 text-sm text-slate-500">{desc}</div>
    </div>
  );
}

function toggleDocumentId(documentId: string, setSelectedDocumentIds: React.Dispatch<React.SetStateAction<string[]>>) {
  setSelectedDocumentIds((current) =>
    current.includes(documentId) ? current.filter((item) => item !== documentId) : [...current, documentId],
  );
}

function normalizeTemplateTaskResult(result: Record<string, unknown> | undefined): {
  outputFileName: string | null;
  matchedDocumentIds: string[];
  elapsedSeconds: number | null;
  filledCells: FilledCellResponse[];
} {
  const outputFileName = typeof result?.output_file_name === 'string' ? result.output_file_name : null;
  const matchedDocumentIds = Array.isArray(result?.matched_document_ids)
    ? result.matched_document_ids.filter((item): item is string => typeof item === 'string')
    : [];
  const elapsedSeconds = typeof result?.elapsed_seconds === 'number' ? result.elapsed_seconds : null;
  const filledCells = Array.isArray(result?.filled_cells) ? (result.filled_cells as FilledCellResponse[]) : [];

  return {
    outputFileName,
    matchedDocumentIds,
    elapsedSeconds,
    filledCells,
  };
}

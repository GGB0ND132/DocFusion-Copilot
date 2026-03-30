import { useEffect, useMemo, useState } from 'react';
import AppButton from '@/components/AppButton';
import EmptyStateCard from '@/components/EmptyStateCard';
import ErrorStateCard from '@/components/ErrorStateCard';
import LoadingStateCard from '@/components/LoadingStateCard';
import StatusBadge from '@/components/StatusBadge';
import {
  downloadAgentArtifact,
  downloadTemplateResult,
  getTaskStatus,
  runAgentChat,
  runAgentExecute,
  submitTemplateFill,
  type FilledCellResponse,
} from '@/services';
import { isTemplateFillTask } from '@/services/taskTypes';
import type { AgentChatResponse, AgentExecuteResponse } from '@/services';
import { useUiStore } from '@/stores/uiStore';

type ActiveTab = 'template' | 'agent';

export default function AgentExecutePage() {
  const [activeTab, setActiveTab] = useState<ActiveTab>('template');

  return (
    <div className="space-y-6">
      <div className="flex gap-2">
        <TabButton active={activeTab === 'template'} onClick={() => setActiveTab('template')}>
          模板回填
        </TabButton>
        <TabButton active={activeTab === 'agent'} onClick={() => setActiveTab('agent')}>
          Agent 执行
        </TabButton>
      </div>

      <div>
        {activeTab === 'template' ? <TemplateFillPanel /> : <AgentPanel />}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════
   Template Fill Panel (merged from ResultPreviewPage)
   ═══════════════════════════════════════════════════════════ */

function TemplateFillPanel() {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [pageError, setPageError] = useState<string | null>(null);
  const [traceFactIdInput, setTraceFactIdInput] = useState('');
  const uploadedDocuments = useUiStore((s) => s.uploadedDocuments);
  const currentDocumentSetId = useUiStore((s) => s.currentDocumentSetId);
  const selectedTemplateFile = useUiStore((s) => s.selectedTemplateFile);
  const selectedTemplateName = useUiStore((s) => s.selectedTemplateName);
  const latestTemplateTaskId = useUiStore((s) => s.latestTemplateTaskId);
  const taskSnapshots = useUiStore((s) => s.taskSnapshots);
  const upsertTaskSnapshot = useUiStore((s) => s.upsertTaskSnapshot);
  const setLatestTemplateTaskId = useUiStore((s) => s.setLatestTemplateTaskId);
  const openTraceByFactId = useUiStore((s) => s.openTraceByFactId);
  const pushToast = useUiStore((s) => s.pushToast);

  const [fillMode, setFillMode] = useState('canonical');
  const [autoMatch, setAutoMatch] = useState(true);
  const [documentSetIdInput, setDocumentSetIdInput] = useState(currentDocumentSetId ?? 'default');
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);

  const currentTemplateTask = latestTemplateTaskId ? taskSnapshots[latestTemplateTaskId] : undefined;
  const uploadedDocumentIds = useMemo(() => uploadedDocuments.map((i) => i.document.doc_id), [uploadedDocuments]);
  const templateTaskResult = useMemo(() => normalizeTemplateTaskResult(currentTemplateTask?.result), [currentTemplateTask?.result]);

  const templateFillTasks = useMemo(() => {
    return Object.values(taskSnapshots)
      .filter((t) => isTemplateFillTask(t.task_type))
      .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
  }, [taskSnapshots]);

  useEffect(() => { setDocumentSetIdInput(currentDocumentSetId ?? 'default'); }, [currentDocumentSetId]);

  useEffect(() => {
    if (!uploadedDocumentIds.length) { setSelectedDocumentIds([]); return; }
    setSelectedDocumentIds((cur) => {
      const existing = cur.filter((id) => uploadedDocumentIds.includes(id));
      return existing.length ? existing : uploadedDocumentIds;
    });
  }, [uploadedDocumentIds]);

  useEffect(() => {
    if (!latestTemplateTaskId) return undefined;
    const task = taskSnapshots[latestTemplateTaskId];
    if (!task || ['succeeded', 'failed', 'completed'].includes(task.status)) return undefined;
    const timer = window.setInterval(async () => {
      try { const t = await getTaskStatus(latestTemplateTaskId); upsertTaskSnapshot(t); }
      catch { window.clearInterval(timer); }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [latestTemplateTaskId, taskSnapshots, upsertTaskSnapshot]);

  async function handleSubmitFill() {
    if (!selectedTemplateFile) { setPageError('请先在上传页选择模板文件。'); return; }
    if (!uploadedDocumentIds.length) { setPageError('请先上传至少一个原始文档。'); return; }
    setIsSubmitting(true);
    setPageError(null);
    try {
      const resp = await submitTemplateFill({
        templateFile: selectedTemplateFile,
        documentSetId: documentSetIdInput.trim() || 'default',
        fillMode,
        documentIds: autoMatch ? undefined : selectedDocumentIds,
        autoMatch,
      });
      setLatestTemplateTaskId(resp.task_id);
      const task = await getTaskStatus(resp.task_id);
      upsertTaskSnapshot(task);
      pushToast({ title: '模板回填任务已提交', message: `${resp.template_name} → ${resp.task_id}`, tone: 'success' });
    } catch (e) {
      const msg = e instanceof Error ? e.message : '模板回填提交失败。';
      setPageError(msg);
      pushToast({ title: '回填提交失败', message: msg, tone: 'error' });
    } finally { setIsSubmitting(false); }
  }

  async function handleDownload() {
    if (!latestTemplateTaskId) { setPageError('当前没有可下载的回填任务。'); return; }
    setIsDownloading(true);
    setPageError(null);
    try {
      const file = await downloadTemplateResult(latestTemplateTaskId);
      const url = window.URL.createObjectURL(file.blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = file.fileName;
      a.click();
      window.URL.revokeObjectURL(url);
      pushToast({ title: '结果已开始下载', message: file.fileName, tone: 'info' });
    } catch (e) {
      const msg = e instanceof Error ? e.message : '下载结果文件失败。';
      setPageError(msg);
    } finally { setIsDownloading(false); }
  }

  async function handleTraceLookup() {
    if (!traceFactIdInput.trim()) { setPageError('请输入要查询的 fact_id。'); return; }
    setPageError(null);
    await openTraceByFactId(traceFactIdInput.trim(), null);
  }

  return (
    <div className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
      <section className="glass-panel rounded-[28px] border border-white/70 p-6 shadow-card">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">模板回填</div>
            <h3 className="mt-2 text-2xl font-semibold text-ink">提交回填与结果预览</h3>
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
            <AppButton onClick={handleSubmitFill} loading={isSubmitting} loadingText="提交中...">
              提交模板回填
            </AppButton>
          </div>
        </div>

        {isSubmitting ? <div className="mt-4"><LoadingStateCard title="正在提交回填任务" description="模板文件与文档集合正在发送到后端。" /></div> : null}
        {pageError ? <div className="mt-4"><ErrorStateCard title="操作失败" description={pageError} /></div> : null}
        {!selectedTemplateFile ? <div className="mt-6"><EmptyStateCard title="还没有模板文件" description="先回到上传页选择一个模板文件。" /></div> : null}

        <div className="mt-6 grid gap-4 md:grid-cols-3">
          <InfoCard title="当前模板" value={selectedTemplateName ?? '未选择'} desc="模板文件在上传页选择" />
          <InfoCard title="回填任务" value={latestTemplateTaskId ?? '未提交'} desc={currentTemplateTask ? currentTemplateTask.message : '提交后会生成 task_id'} />
          <InfoCard title="已填充单元格" value={String(templateTaskResult.filledCells.length)} desc={templateTaskResult.outputFileName ?? '等待任务完成'} />
        </div>

        {/* ── 回填控制项 ── */}
        <div className="mt-6 rounded-[24px] border border-white/70 bg-white/80 p-5">
          <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-400">回填控制项</div>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <label className="block">
              <div className="mb-2 text-sm font-semibold text-ink">document_set_id</div>
              <input
                value={documentSetIdInput}
                onChange={(e) => setDocumentSetIdInput(e.target.value)}
                className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 outline-none transition focus:border-teal"
                placeholder="default 或上传返回的 document_set_id"
              />
            </label>
            <label className="block">
              <div className="mb-2 text-sm font-semibold text-ink">fill_mode</div>
              <select
                value={fillMode}
                onChange={(e) => setFillMode(e.target.value)}
                className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 outline-none transition focus:border-teal"
              >
                <option value="canonical">canonical</option>
                <option value="candidate">candidate</option>
              </select>
            </label>
          </div>
          <label className="mt-4 flex items-center gap-3 rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-700">
            <input type="checkbox" checked={autoMatch} onChange={(e) => setAutoMatch(e.target.checked)} className="h-4 w-4 accent-teal" />
            自动匹配文档。关闭后将使用下方手动勾选的 document_ids。
          </label>
          <DocumentIdSelector
            uploadedDocuments={uploadedDocuments}
            selectedDocumentIds={selectedDocumentIds}
            setSelectedDocumentIds={setSelectedDocumentIds}
            autoMatch={autoMatch}
          />
        </div>

        {/* ── 模板回填任务进度 ── */}
        {templateFillTasks.length > 0 && (
          <div className="mt-6 rounded-[24px] border border-white/70 bg-white/80 p-5">
            <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-400">模板回填任务进度</div>
            <div className="mt-4 space-y-3">
              {templateFillTasks.map((task) => {
                const progress = Math.round(task.progress * 100);
                return (
                  <div key={task.task_id} className="flex items-center gap-4 rounded-2xl bg-slate-50 px-4 py-3">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-semibold text-ink">{String(task.result.template_name ?? task.task_id)}</div>
                      <div className="mt-1 h-2 rounded-full bg-slate-200">
                        <div className="h-2 rounded-full bg-gradient-to-r from-teal to-ember" style={{ width: `${progress}%` }} />
                      </div>
                    </div>
                    <StatusBadge status={mapTaskStatus(task.status)} />
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* ── filled cells ── */}
        <div className="mt-6 overflow-hidden rounded-[24px] border border-slate-200 bg-white">
          <div className="border-b border-slate-100 bg-slate-50 px-4 py-4 text-sm font-semibold text-slate-600">已回填单元格</div>
          <div className="p-4">
            {templateTaskResult.filledCells.length ? (
              <div className="space-y-3">
                {templateTaskResult.filledCells.slice(0, 12).map((cell) => (
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
            ) : (
              <div className="text-sm leading-7 text-slate-600">任务尚未完成或无已回填单元格，完成后会自动刷新。</div>
            )}
          </div>
        </div>
      </section>

      {/* ── right sidebar ── */}
      <section className="space-y-6">
        <div className="glass-panel rounded-[28px] border border-white/70 p-6 shadow-card">
          <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">来源追溯查询</div>
          <div className="mt-4 flex gap-3">
            <input
              value={traceFactIdInput}
              onChange={(e) => setTraceFactIdInput(e.target.value)}
              placeholder="输入 fact_id 后查询来源"
              className="flex-1 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 outline-none transition focus:border-teal"
            />
            <AppButton onClick={handleTraceLookup} variant="accent">查询</AppButton>
          </div>
        </div>
        <div className="glass-panel rounded-[28px] border border-white/70 p-6 shadow-card">
          <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">联调说明</div>
          <div className="mt-4 rounded-2xl bg-amber-50 p-4 text-sm leading-7 text-amber-900">
            结果页提交模板回填时会优先使用当前批次的 document_set_id。README.txt 等提示词文档会被后端自动排除。
          </div>
        </div>
      </section>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════
   Agent Panel (original agent functionality)
   ═══════════════════════════════════════════════════════════ */

function AgentPanel() {
  const uploadedDocuments = useUiStore((s) => s.uploadedDocuments);
  const selectedTemplateFile = useUiStore((s) => s.selectedTemplateFile);
  const currentDocumentSetId = useUiStore((s) => s.currentDocumentSetId);
  const pushToast = useUiStore((s) => s.pushToast);
  const openTraceByFactId = useUiStore((s) => s.openTraceByFactId);

  const [message, setMessage] = useState('请根据当前批次文档提取关键指标，并生成一份摘要。');
  const [contextId, setContextId] = useState('demo-agent-context');
  const [fillMode, setFillMode] = useState('canonical');
  const [autoMatch, setAutoMatch] = useState(true);
  const [useTemplateForExecution, setUseTemplateForExecution] = useState(false);
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);
  const [planningResult, setPlanningResult] = useState<AgentChatResponse | null>(null);
  const [executionResult, setExecutionResult] = useState<AgentExecuteResponse | null>(null);
  const [isPlanning, setIsPlanning] = useState(false);
  const [isExecuting, setIsExecuting] = useState(false);
  const [pageError, setPageError] = useState<string | null>(null);
  const [downloadingArtifact, setDownloadingArtifact] = useState<string | null>(null);

  const documentOptions = useMemo(
    () => uploadedDocuments.map((i) => ({ id: i.document.doc_id, name: i.document.file_name })),
    [uploadedDocuments],
  );

  async function handlePlan() {
    if (!message.trim()) { setPageError('请输入 agent 指令。'); return; }
    setIsPlanning(true);
    setPageError(null);
    try {
      const r = await runAgentChat({ message: message.trim(), contextId: contextId.trim() || undefined });
      setPlanningResult(r);
      pushToast({ title: '规划完成', message: `已识别意图 ${r.intent}。`, tone: 'success' });
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'agent/chat 调用失败。';
      setPageError(msg);
    } finally { setIsPlanning(false); }
  }

  async function handleExecute() {
    if (!message.trim()) { setPageError('请输入 agent 指令。'); return; }
    setIsExecuting(true);
    setPageError(null);
    try {
      const r = await runAgentExecute({
        message: message.trim(),
        contextId: contextId.trim() || undefined,
        documentSetId: currentDocumentSetId ?? undefined,
        documentIds: autoMatch ? undefined : selectedDocumentIds,
        fillMode,
        autoMatch,
        templateFile: useTemplateForExecution ? selectedTemplateFile : undefined,
      });
      setExecutionResult(r);
      pushToast({ title: '执行完成', message: r.summary, tone: 'success' });
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'agent/execute 调用失败。';
      setPageError(msg);
    } finally { setIsExecuting(false); }
  }

  async function handleDownloadArtifact(fileName: string) {
    setDownloadingArtifact(fileName);
    try {
      const file = await downloadAgentArtifact(fileName);
      const url = window.URL.createObjectURL(file.blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = file.fileName;
      a.click();
      window.URL.revokeObjectURL(url);
    } finally { setDownloadingArtifact(null); }
  }

  return (
    <div className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
      <section className="glass-panel rounded-[28px] border border-white/70 p-6 shadow-card">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">Agent Lab</div>
            <h3 className="mt-2 text-2xl font-semibold text-ink">自然语言执行台</h3>
          </div>
          <div className="flex flex-wrap gap-3">
            <AppButton onClick={handlePlan} variant="secondary" loading={isPlanning} loadingText="规划中...">先做规划</AppButton>
            <AppButton onClick={handleExecute} loading={isExecuting} loadingText="执行中...">直接执行</AppButton>
          </div>
        </div>

        {pageError ? <div className="mt-4"><ErrorStateCard title="Agent 调用失败" description={pageError} /></div> : null}
        {isPlanning && !planningResult ? <div className="mt-4"><LoadingStateCard title="正在生成执行计划" description="前端正在请求 agent/chat。" /></div> : null}
        {isExecuting && !executionResult ? <div className="mt-4"><LoadingStateCard title="正在执行 agent 指令" description="如果携带模板文件走 multipart。" /></div> : null}

        <div className="mt-6 space-y-4">
          <label className="block">
            <div className="mb-2 text-sm font-semibold text-ink">自然语言指令</div>
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              rows={4}
              className="w-full rounded-[24px] border border-slate-200 bg-white px-4 py-4 text-sm leading-7 text-slate-700 outline-none transition focus:border-teal"
              placeholder="例如：根据当前文档批次汇总工业增加值。"
            />
          </label>

          <div className="grid gap-4 md:grid-cols-2">
            <label className="block">
              <div className="mb-2 text-sm font-semibold text-ink">context_id</div>
              <input
                value={contextId}
                onChange={(e) => setContextId(e.target.value)}
                className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 outline-none transition focus:border-teal"
              />
            </label>
            <label className="block">
              <div className="mb-2 text-sm font-semibold text-ink">fill_mode</div>
              <select
                value={fillMode}
                onChange={(e) => setFillMode(e.target.value)}
                className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 outline-none transition focus:border-teal"
              >
                <option value="canonical">canonical</option>
                <option value="candidate">candidate</option>
              </select>
            </label>
          </div>

          <label className="flex items-center gap-3 rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-700">
            <input type="checkbox" checked={autoMatch} onChange={(e) => setAutoMatch(e.target.checked)} className="h-4 w-4 accent-teal" />
            自动匹配文档。
          </label>

          <div className="rounded-[24px] border border-dashed border-slate-200 p-4">
            <div className="text-sm font-semibold text-ink">模板执行</div>
            <label className="mt-3 flex items-center gap-3 rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={useTemplateForExecution}
                onChange={(e) => setUseTemplateForExecution(e.target.checked)}
                disabled={!selectedTemplateFile}
                className="h-4 w-4 accent-teal"
              />
              携带模板文件并触发模板回填。
            </label>
            <div className="mt-3 rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-600">
              {selectedTemplateFile ? (
                <>
                  <div className="font-semibold text-ink">当前模板</div>
                  <div className="mt-1 break-all">{selectedTemplateFile.name}</div>
                </>
              ) : (
                <div className="text-xs text-slate-500">未选择模板，按普通 agent 指令处理。</div>
              )}
            </div>
          </div>

          <div className="rounded-[24px] border border-dashed border-slate-200 p-4">
            <div className="text-sm font-semibold text-ink">文档选择</div>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              {documentOptions.length ? (
                documentOptions.map((doc) => (
                  <label key={doc.id} className="flex items-start gap-3 rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={selectedDocumentIds.includes(doc.id)}
                      disabled={autoMatch}
                      onChange={() =>
                        setSelectedDocumentIds((cur) =>
                          cur.includes(doc.id) ? cur.filter((i) => i !== doc.id) : [...cur, doc.id],
                        )
                      }
                      className="mt-1 h-4 w-4 accent-teal"
                    />
                    <span>
                      <span className="block font-semibold text-ink">{doc.name}</span>
                      <span className="block text-xs text-slate-500">{doc.id}</span>
                    </span>
                  </label>
                ))
              ) : (
                <div className="text-sm text-slate-500">当前没有可用文档。</div>
              )}
            </div>
          </div>
        </div>

        {/* ── results ── */}
        <div className="mt-6 grid gap-6 lg:grid-cols-2">
          <ResultPanel title="规划结果" emptyTitle="尚未生成规划" emptyDescription="先调用 agent/chat。">
            {planningResult ? (
              <div className="space-y-3 text-sm text-slate-600">
                <PanelLine label="intent" value={planningResult.intent} />
                <PanelLine label="target" value={planningResult.target} />
                <PanelLine label="planner" value={planningResult.planner} />
                <PanelLine label="entities" value={planningResult.entities.join(', ') || '无'} />
                <PanelLine label="fields" value={planningResult.fields.join(', ') || '无'} />
                <div className="rounded-2xl bg-slate-50 p-4">
                  <div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">preview</div>
                  <pre className="mt-3 overflow-auto text-xs leading-6 text-slate-700">{JSON.stringify(planningResult.preview, null, 2)}</pre>
                </div>
              </div>
            ) : null}
          </ResultPanel>

          <ResultPanel title="执行结果" emptyTitle="尚未执行" emptyDescription="执行后会在这里显示。">
            {executionResult ? (
              <div className="space-y-4 text-sm text-slate-600">
                <PanelLine label="execution_type" value={executionResult.execution_type} />
                <PanelLine label="summary" value={executionResult.summary} />
                <PanelLine label="task_status" value={executionResult.task_status ?? '无任务'} />
                <div className="rounded-2xl bg-slate-50 p-4">
                  <div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">facts</div>
                  <div className="mt-3 space-y-3">
                    {executionResult.facts.length ? (
                      executionResult.facts.slice(0, 6).map((fact) => (
                        <div key={fact.fact_id} className="rounded-2xl bg-white px-4 py-3">
                          <div className="text-sm font-semibold text-ink">{fact.entity_name} · {fact.field_name}</div>
                          <div className="mt-1 text-sm text-slate-600">{fact.value_text || String(fact.value_num ?? '-')}</div>
                          <div className="mt-3">
                            <AppButton size="sm" variant="ghost" onClick={() => openTraceByFactId(fact.fact_id, fact.field_name)}>
                              查看追溯
                            </AppButton>
                          </div>
                        </div>
                      ))
                    ) : (
                      <EmptyStateCard title="未返回 facts" description="该次执行可能更偏向摘要或产物输出。" />
                    )}
                  </div>
                </div>
                <div className="rounded-2xl border border-dashed border-slate-200 p-4">
                  <div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">artifacts</div>
                  <div className="mt-3 space-y-3">
                    {executionResult.artifacts.length ? (
                      executionResult.artifacts.map((art) => (
                        <div key={art.file_name} className="flex flex-col gap-3 rounded-2xl bg-slate-50 px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
                          <div>
                            <div className="text-sm font-semibold text-ink">{art.file_name}</div>
                            <div className="mt-1 text-xs text-slate-500">{art.operation} · {art.doc_id}</div>
                          </div>
                          <AppButton
                            size="sm"
                            variant="secondary"
                            loading={downloadingArtifact === art.file_name}
                            loadingText="下载中..."
                            onClick={() => handleDownloadArtifact(art.file_name)}
                          >
                            下载产物
                          </AppButton>
                        </div>
                      ))
                    ) : (
                      <EmptyStateCard title="未生成产物" description="当前执行可能只返回文本总结或 facts。" />
                    )}
                  </div>
                </div>
              </div>
            ) : null}
          </ResultPanel>
        </div>
      </section>

      <section className="space-y-6">
        <HintCard title="已接后端能力">
          <ul className="space-y-3 text-sm leading-7 text-slate-600">
            <li>POST /api/v1/agent/chat：结构化规划。</li>
            <li>POST /api/v1/agent/execute：JSON 与 multipart 两种执行入口。</li>
            <li>GET /api/v1/agent/artifacts/{'{file_name}'}：下载产物文件。</li>
          </ul>
        </HintCard>
      </section>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════
   Shared sub-components
   ═══════════════════════════════════════════════════════════ */

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={[
        'rounded-2xl px-5 py-3 text-sm font-semibold transition',
        active ? 'bg-ink text-white shadow-sm' : 'bg-white/70 text-slate-600 hover:bg-white',
      ].join(' ')}
    >
      {children}
    </button>
  );
}

function DocumentIdSelector({
  uploadedDocuments,
  selectedDocumentIds,
  setSelectedDocumentIds,
  autoMatch,
}: {
  uploadedDocuments: { document: { doc_id: string; file_name: string } }[];
  selectedDocumentIds: string[];
  setSelectedDocumentIds: React.Dispatch<React.SetStateAction<string[]>>;
  autoMatch: boolean;
}) {
  return (
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
                onChange={() =>
                  setSelectedDocumentIds((cur) =>
                    cur.includes(item.document.doc_id) ? cur.filter((i) => i !== item.document.doc_id) : [...cur, item.document.doc_id],
                  )
                }
                className="mt-1 h-4 w-4 accent-teal"
              />
              <span>
                <span className="block font-semibold text-ink">{item.document.file_name}</span>
                <span className="block text-xs text-slate-500">{item.document.doc_id}</span>
              </span>
            </label>
          ))
        ) : (
          <div className="text-sm text-slate-500">当前没有可选文档。</div>
        )}
      </div>
    </div>
  );
}

function ResultPanel({
  title,
  emptyTitle,
  emptyDescription,
  children,
}: {
  title: string;
  emptyTitle: string;
  emptyDescription: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-[24px] border border-white/80 bg-white/85 p-5">
      <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-400">{title}</div>
      <div className="mt-4">{children ?? <EmptyStateCard title={emptyTitle} description={emptyDescription} />}</div>
    </div>
  );
}

function PanelLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl bg-slate-50 px-4 py-3">
      <div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">{label}</div>
      <div className="mt-2 break-all text-sm text-ink">{value}</div>
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

function HintCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="glass-panel rounded-[28px] border border-white/70 p-6 shadow-card">
      <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">{title}</div>
      <div className="mt-4">{children}</div>
    </div>
  );
}

function mapTaskStatus(status: string): 'queued' | 'processing' | 'completed' | 'warning' {
  if (status === 'queued' || status === 'pending') return 'queued';
  if (status === 'running' || status === 'processing') return 'processing';
  if (status === 'succeeded' || status === 'completed' || status === 'success') return 'completed';
  return 'warning';
}

function normalizeTemplateTaskResult(result: Record<string, unknown> | undefined): {
  outputFileName: string | null;
  matchedDocumentIds: string[];
  elapsedSeconds: number | null;
  filledCells: FilledCellResponse[];
} {
  const outputFileName = typeof result?.output_file_name === 'string' ? result.output_file_name : null;
  const matchedDocumentIds = Array.isArray(result?.matched_document_ids)
    ? result.matched_document_ids.filter((i): i is string => typeof i === 'string')
    : [];
  const elapsedSeconds = typeof result?.elapsed_seconds === 'number' ? result.elapsed_seconds : null;
  const filledCells = Array.isArray(result?.filled_cells) ? (result.filled_cells as FilledCellResponse[]) : [];
  return { outputFileName, matchedDocumentIds, elapsedSeconds, filledCells };
}

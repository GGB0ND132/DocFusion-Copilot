import { useEffect, useMemo, useState } from 'react';
import AppButton from '@/components/AppButton';
import EmptyStateCard from '@/components/EmptyStateCard';
import ErrorStateCard from '@/components/ErrorStateCard';
import LoadingStateCard from '@/components/LoadingStateCard';
import StatusBadge from '@/components/StatusBadge';
import { getDocumentBlocks, getDocumentDetail, getDocumentFacts, getTaskStatus, listDocuments } from '@/services';
import { isDocumentParseTask } from '@/services/taskTypes';
import type { BlockResponse, DocumentResponse, FactResponse } from '@/services';
import { useUiStore } from '@/stores/uiStore';

export default function DocumentDetailPage() {
  const [documents, setDocuments] = useState<DocumentResponse[]>([]);
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);
  const [selectedDocument, setSelectedDocument] = useState<DocumentResponse | null>(null);
  const [blocks, setBlocks] = useState<BlockResponse[]>([]);
  const [facts, setFacts] = useState<FactResponse[]>([]);
  const [isLoadingList, setIsLoadingList] = useState(true);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [blockTypeFilter, setBlockTypeFilter] = useState('all');
  const openTraceByFactId = useUiStore((state) => state.openTraceByFactId);
  const pushToast = useUiStore((state) => state.pushToast);
  const taskSnapshots = useUiStore((state) => state.taskSnapshots);
  const upsertTaskSnapshot = useUiStore((state) => state.upsertTaskSnapshot);
  const clearFileCache = useUiStore((state) => state.clearFileCache);

  /* ── auto-poll active document parsing tasks ── */
  const docParseTasks = useMemo(() => {
    return Object.values(taskSnapshots)
      .filter((t) => isDocumentParseTask(t.task_type))
      .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
  }, [taskSnapshots]);

  useEffect(() => {
    const activeIds = docParseTasks
      .filter((t) => !['succeeded', 'completed', 'success', 'failed'].includes(t.status))
      .map((t) => t.task_id);
    if (!activeIds.length) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const tasks = await Promise.all(activeIds.map((id) => getTaskStatus(id)));
        tasks.forEach((t) => upsertTaskSnapshot(t));
      } catch { window.clearInterval(timer); }
    }, 4000);
    return () => window.clearInterval(timer);
  }, [docParseTasks, upsertTaskSnapshot]);

  useEffect(() => {
    async function loadDocuments() {
      setIsLoadingList(true);
      setListError(null);

      try {
        const result = await listDocuments();
        setDocuments(result);
        setSelectedDocId((current) => current ?? result[0]?.doc_id ?? null);
      } catch (error) {
        setListError(error instanceof Error ? error.message : '文档列表加载失败。');
      } finally {
        setIsLoadingList(false);
      }
    }

    void loadDocuments();
  }, []);

  useEffect(() => {
    if (!selectedDocId) {
      setSelectedDocument(null);
      setBlocks([]);
      setFacts([]);
      return;
    }

    const docId = selectedDocId;

    async function loadDocumentDetail() {
      setIsLoadingDetail(true);
      setDetailError(null);

      try {
        const [document, blockItems, factItems] = await Promise.all([
          getDocumentDetail(docId),
          getDocumentBlocks(docId),
          getDocumentFacts(docId),
        ]);
        setSelectedDocument(document);
        setBlocks(blockItems);
        setFacts(factItems);
      } catch (error) {
        setDetailError(error instanceof Error ? error.message : '文档详情加载失败。');
      } finally {
        setIsLoadingDetail(false);
      }
    }

    void loadDocumentDetail();
  }, [selectedDocId]);

  const filteredBlocks = useMemo(() => {
    if (blockTypeFilter === 'all') {
      return blocks;
    }
    return blocks.filter((block) => block.block_type === blockTypeFilter);
  }, [blockTypeFilter, blocks]);

  const blockTypes = useMemo(() => ['all', ...Array.from(new Set(blocks.map((block) => block.block_type)))], [blocks]);

  function handleDocumentSelect(docId: string) {
    setSelectedDocId(docId);
  }

  return (
    <div className="grid gap-6 xl:grid-cols-[0.8fr_1.2fr]">
      <section className="glass-panel rounded-[28px] border border-white/70 p-6 shadow-card">
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">步骤 02</div>
            <h3 className="mt-2 text-2xl font-semibold text-ink">文档详情视图</h3>
          </div>
          <div className="flex gap-3">
            <AppButton
              size="sm"
              variant="ghost"
              onClick={() => {
                clearFileCache();
                setDocuments([]);
                setSelectedDocId(null);
                setSelectedDocument(null);
                setBlocks([]);
                setFacts([]);
                pushToast({ title: '缓存已清除', message: '前端文档缓存、任务快照和模板文件已全部清空。', tone: 'info' });
              }}
            >
              清除缓存
            </AppButton>
            <AppButton variant="secondary" onClick={() => window.location.reload()}>
              重载列表
            </AppButton>
          </div>
        </div>

        {isLoadingList ? <div className="mt-4"><LoadingStateCard title="正在加载文档列表" description="前端正在读取后端已登记的文档集合。" /></div> : null}
        {listError ? <div className="mt-4"><ErrorStateCard title="文档列表加载失败" description={listError} /></div> : null}

        <div className="mt-6 space-y-3">
          {documents.length ? (
            documents.map((document) => {
              const isActive = document.doc_id === selectedDocId;
              const documentSetId = typeof document.metadata.document_set_id === 'string' ? document.metadata.document_set_id : 'default';

              return (
                <button
                  key={document.doc_id}
                  type="button"
                  onClick={() => handleDocumentSelect(document.doc_id)}
                  className={[
                    'w-full rounded-[24px] border px-4 py-4 text-left transition',
                    isActive
                      ? 'border-amber-300 bg-amber-50 shadow-sm'
                      : 'border-white/80 bg-white/80 hover:border-slate-200 hover:bg-white',
                  ].join(' ')}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-sm font-semibold text-ink">{document.file_name}</div>
                      <div className="mt-1 text-xs text-slate-500">{document.doc_id}</div>
                    </div>
                    <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">{document.status}</span>
                  </div>
                  <div className="mt-3 text-xs uppercase tracking-[0.2em] text-slate-400">document_set_id</div>
                  <div className="mt-1 text-sm text-slate-600">{documentSetId}</div>
                </button>
              );
            })
          ) : !isLoadingList ? (
            <EmptyStateCard title="暂无文档" description="先在上传页建立文档批次，文档详情页才会出现可查看的记录。" />
          ) : null}
        </div>
      </section>

      <section className="space-y-6">
        {/* ── 文档解析任务进度 ── */}
        {docParseTasks.length > 0 && (
          <div className="glass-panel rounded-[28px] border border-white/70 p-6 shadow-card">
            <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">文档解析任务进度</div>
            <div className="mt-4 space-y-3">
              {docParseTasks.map((task) => {
                const progress = Math.round(task.progress * 100);
                const mappedStatus = mapTaskStatus(task.status);
                return (
                  <article key={task.task_id} className="rounded-[24px] border border-white/80 bg-white/85 p-4">
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-semibold text-ink">
                          {String(task.result.file_name ?? task.task_id)}
                        </div>
                        <div className="mt-1 text-xs text-slate-500">{task.message}</div>
                      </div>
                      <StatusBadge status={mappedStatus} />
                    </div>
                    <div className="mt-3">
                      <div className="flex items-center justify-between text-xs text-slate-500">
                        <span>进度</span>
                        <span>{progress}%</span>
                      </div>
                      <div className="mt-1 h-2 rounded-full bg-slate-100">
                        <div className="h-2 rounded-full bg-gradient-to-r from-teal to-ember" style={{ width: `${progress}%` }} />
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          </div>
        )}

        <div className="glass-panel rounded-[28px] border border-white/70 p-6 shadow-card">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">文档元信息</div>
              <h3 className="mt-2 text-2xl font-semibold text-ink">{selectedDocument?.file_name ?? '未选择文档'}</h3>
              <p className="mt-2 max-w-2xl text-sm leading-7 text-slate-600">
                当前页面同时读取文档详情、解析 blocks 和抽取 facts，用于给评委展示“文档进来后系统到底理解到了什么”。
              </p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <MetricCard label="解析块数" value={String(blocks.length)} />
              <MetricCard label="事实条数" value={String(facts.length)} />
            </div>
          </div>

          {isLoadingDetail ? <div className="mt-4"><LoadingStateCard title="正在加载文档详情" description="详情、blocks 和 facts 会一起返回。" /></div> : null}
          {detailError ? <div className="mt-4"><ErrorStateCard title="文档详情加载失败" description={detailError} /></div> : null}

          {selectedDocument ? (
            <div className="mt-6 grid gap-4 md:grid-cols-3">
              <InfoCard title="文档类型" value={selectedDocument.doc_type} desc={selectedDocument.status} />
              <InfoCard title="上传时间" value={formatTime(selectedDocument.upload_time)} desc={selectedDocument.doc_id} />
              <InfoCard
                title="批次标识"
                value={String(selectedDocument.metadata.document_set_id ?? 'default')}
                desc="模板回填和文档详情共享同一批次维度"
              />
            </div>
          ) : null}
        </div>

        <div className="grid gap-6 lg:grid-cols-[1.1fr_0.9fr]">
          <div className="glass-panel rounded-[28px] border border-white/70 p-6 shadow-card">
            <div className="flex items-center justify-between gap-4">
              <div>
                <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">解析 Blocks</div>
                <h4 className="mt-2 text-xl font-semibold text-ink">结构化片段预览</h4>
              </div>
              <select
                value={blockTypeFilter}
                onChange={(event) => setBlockTypeFilter(event.target.value)}
                className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 outline-none transition focus:border-teal"
              >
                {blockTypes.map((blockType) => (
                  <option key={blockType} value={blockType}>
                    {blockType === 'all' ? '全部 block 类型' : blockType}
                  </option>
                ))}
              </select>
            </div>

            <div className="mt-5 space-y-4">
              {filteredBlocks.length ? (
                filteredBlocks.slice(0, 10).map((block) => (
                  <article key={block.block_id} className="rounded-[24px] border border-white/80 bg-white/85 p-4">
                    <div className="flex flex-wrap items-center gap-2 text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
                      <span>{block.block_type}</span>
                      <span>#{block.page_or_index ?? '-'}</span>
                    </div>
                    <div className="mt-3 text-xs text-slate-500">{block.section_path.join(' / ') || '未标注路径'}</div>
                    <p className="mt-3 line-clamp-5 text-sm leading-7 text-slate-700">{block.text}</p>
                  </article>
                ))
              ) : !isLoadingDetail ? (
                <EmptyStateCard title="暂无 blocks" description="当前文档还没有解析块，可能任务尚未完成，或该文档暂无可展示片段。" />
              ) : null}
            </div>
          </div>

          <div className="glass-panel rounded-[28px] border border-white/70 p-6 shadow-card">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">抽取 Facts</div>
              <h4 className="mt-2 text-xl font-semibold text-ink">核心字段结果</h4>
            </div>

            <div className="mt-5 space-y-4">
              {facts.length ? (
                facts.slice(0, 10).map((fact) => (
                  <article key={fact.fact_id} className="rounded-[24px] border border-white/80 bg-white/85 p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">{fact.entity_name}</div>
                        <div className="mt-2 text-base font-semibold text-ink">{fact.field_name}</div>
                      </div>
                      <span className={[
                        'rounded-full px-3 py-1 text-xs font-semibold',
                        fact.confidence >= 0.8 ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800',
                      ].join(' ')}>
                        {(fact.confidence * 100).toFixed(1)}%
                      </span>
                    </div>

                    <div className="mt-3 text-sm leading-7 text-slate-700">{fact.value_text || String(fact.value_num ?? '-')}</div>
                    <div className="mt-2 text-xs text-slate-500">来源区块：{fact.source_block_id}</div>

                    <div className="mt-4 flex flex-wrap gap-2">
                      <AppButton size="sm" variant="ghost" onClick={() => openTraceByFactId(fact.fact_id, fact.field_name)}>
                        查看追溯
                      </AppButton>
                      <AppButton
                        size="sm"
                        variant="secondary"
                        onClick={() => {
                          pushToast({
                            title: '已记录低置信度提示',
                            message: `${fact.field_name} 当前置信度 ${(fact.confidence * 100).toFixed(1)}%。`,
                            tone: fact.confidence >= 0.8 ? 'info' : 'error',
                          });
                        }}
                      >
                        标记关注
                      </AppButton>
                    </div>
                  </article>
                ))
              ) : !isLoadingDetail ? (
                <EmptyStateCard title="暂无 facts" description="当前文档还没有抽取出可展示的事实记录，可能任务尚未完成。" />
              ) : null}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-white/80 bg-white/85 px-4 py-4">
      <div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">{label}</div>
      <div className="mt-3 text-2xl font-semibold text-ink">{value}</div>
    </div>
  );
}

function InfoCard({ title, value, desc }: { title: string; value: string; desc: string }) {
  return (
    <div className="rounded-2xl border border-white/80 bg-white/85 px-4 py-4">
      <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-400">{title}</div>
      <div className="mt-3 break-all text-xl font-semibold text-ink">{value}</div>
      <div className="mt-2 text-sm text-slate-500">{desc}</div>
    </div>
  );
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function mapTaskStatus(status: string): 'queued' | 'processing' | 'completed' | 'warning' {
  if (status === 'queued' || status === 'pending') return 'queued';
  if (status === 'running' || status === 'processing') return 'processing';
  if (status === 'succeeded' || status === 'completed' || status === 'success') return 'completed';
  return 'warning';
}

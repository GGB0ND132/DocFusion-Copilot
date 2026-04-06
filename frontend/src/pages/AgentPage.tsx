import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import {
  Send,
  FileUp,
  Download,
  Loader2,
  Bot,
  User,
  FileSpreadsheet,
  CheckCircle2,
  XCircle,
  Clock,
  Search,
  RotateCcw,
  AlertTriangle,
  MessageSquarePlus,
  Trash2,
  PanelLeftClose,
  PanelLeftOpen,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from '@/components/ui/resizable';
import { useUiStore, type ChatMessage } from '@/stores/uiStore';
import {
  getTaskStatus,
  runAgentExecute,
  downloadAgentArtifact,
  downloadTemplateResult,
  clearAgentConversation,
  listDocuments,
  listConversations,
  createConversation,
  getConversation,
  deleteConversation,
  type AgentExecuteResponse,
  type ConversationResponse,
  type DocumentResponse,
  type FilledCellResponse,
  type TaskResponse,
} from '@/services';

export default function AgentPage() {
  const scrollRef = useRef<HTMLDivElement>(null);
  const templateInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const messages = useUiStore((s) => s.agentMessages);
  const addAgentMessage = useUiStore((s) => s.addAgentMessage);
  const agentContextId = useUiStore((s) => s.agentContextId);
  const setAgentContextId = useUiStore((s) => s.setAgentContextId);
  const clearConversation = useUiStore((s) => s.clearAgentConversation);

  const [inputText, setInputText] = useState('');
  const [isExecuting, setIsExecuting] = useState(false);
  const [templateFile, setTemplateFile] = useState<File | null>(null);
  const [fillTaskId, setFillTaskId] = useState<string | null>(null);
  const [fillTask, setFillTask] = useState<TaskResponse | null>(null);
  const [traceInput, setTraceInput] = useState('');
  const [availableDocuments, setAvailableDocuments] = useState<DocumentResponse[]>([]);
  const [documentsHydrated, setDocumentsHydrated] = useState(false);

  const uploadedDocuments = useUiStore((s) => s.uploadedDocuments);
  const currentDocumentSetId = useUiStore((s) => s.currentDocumentSetId);
  const upsertTaskSnapshot = useUiStore((s) => s.upsertTaskSnapshot);
  const openTraceByFactId = useUiStore((s) => s.openTraceByFactId);
  const conversationList = useUiStore((s) => s.conversationList);
  const setConversationList = useUiStore((s) => s.setConversationList);
  const switchConversation = useUiStore((s) => s.switchConversation);
  const startNewConversation = useUiStore((s) => s.startNewConversation);
  const removeConversationFromList = useUiStore((s) => s.removeConversationFromList);

  const [sidebarOpen, setSidebarOpen] = useState(true);

  const refreshAvailableDocuments = useCallback(async () => {
    const docs = await listDocuments();
    setAvailableDocuments(docs);
    setDocumentsHydrated(true);
    return docs;
  }, []);

  const knownDocuments = useMemo(
    () => mergeKnownDocuments(availableDocuments, uploadedDocuments),
    [availableDocuments, uploadedDocuments],
  );
  const documentScope = useMemo(
    () => resolveAgentDocumentScope(knownDocuments, currentDocumentSetId),
    [knownDocuments, currentDocumentSetId],
  );
  const scopedDocuments = documentScope.scopedDocuments;
  const effectiveDocumentSetId = documentScope.effectiveDocumentSetId;
  const scopedDocumentIds = useMemo(() => scopedDocuments.map((doc) => doc.doc_id), [scopedDocuments]);
  const parsedScopedDocuments = useMemo(
    () => scopedDocuments.filter((doc) => doc.status === 'parsed'),
    [scopedDocuments],
  );
  const parsedScopedDocIds = useMemo(
    () => parsedScopedDocuments.map((doc) => doc.doc_id),
    [parsedScopedDocuments],
  );

  // Auto-scroll to bottom
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages]);

  // Load conversation list on mount
  useEffect(() => {
    listConversations().then(setConversationList).catch(() => {});
  }, [setConversationList]);

  useEffect(() => {
    refreshAvailableDocuments().catch(() => {
      setDocumentsHydrated(true);
    });
  }, [refreshAvailableDocuments]);

  useEffect(() => {
    if (!documentsHydrated) return;
    const hasPendingDocuments = scopedDocuments.some((doc) => doc.status === 'uploaded' || doc.status === 'parsing');
    if (!hasPendingDocuments) return;
    const timer = window.setInterval(() => {
      refreshAvailableDocuments().catch(() => {});
    }, 3000);
    return () => window.clearInterval(timer);
  }, [documentsHydrated, scopedDocuments, refreshAvailableDocuments]);

  // Refresh conversation list when context changes
  const refreshConversations = useCallback(() => {
    listConversations().then(setConversationList).catch(() => {});
  }, [setConversationList]);

  const handleNewConversation = useCallback(() => {
    startNewConversation();
    setFillTaskId(null);
    setFillTask(null);
  }, [startNewConversation]);

  const handleSwitchConversation = useCallback(async (conv: ConversationResponse) => {
    switchConversation(conv);
    setFillTaskId(null);
    setFillTask(null);
    try {
      const fullConversation = await getConversation(conv.conversation_id);
      switchConversation(fullConversation);
      setConversationList(
        conversationList.map((item) => (
          item.conversation_id === fullConversation.conversation_id ? fullConversation : item
        )),
      );
    } catch {
      toast.error('?????????????????????');
    }
  }, [switchConversation, setConversationList, conversationList]);

  const handleDeleteConversation = useCallback(async (convId: string) => {
    try {
      await deleteConversation(convId);
      removeConversationFromList(convId);
      if (agentContextId === convId) {
        startNewConversation();
        setFillTaskId(null);
        setFillTask(null);
      }
      toast.info('对话已删除');
    } catch {
      toast.error('删除对话失败');
    }
  }, [agentContextId, removeConversationFromList, startNewConversation]);

  // Poll fill task status
  useEffect(() => {
    if (!fillTaskId) return;
    if (fillTask && ['succeeded', 'completed', 'success', 'failed'].includes(fillTask.status)) return;
    const timer = window.setInterval(async () => {
      try {
        const t = await getTaskStatus(fillTaskId);
        setFillTask(t);
        upsertTaskSnapshot(t);
        if (['succeeded', 'completed', 'success', 'failed'].includes(t.status)) {
          window.clearInterval(timer);
        }
      } catch {
        window.clearInterval(timer);
      }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [fillTaskId, fillTask, upsertTaskSnapshot]);

  useEffect(() => {
    if (!templateFile || !documentsHydrated || parsedScopedDocIds.length > 0) return;
    toast.info('当前还没有已解析的源文档。请先上传并完成原始文档解析，再回填模板。');
  }, [templateFile, documentsHydrated, parsedScopedDocIds.length]);

  const handleTemplateSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] ?? null;
    setTemplateFile(file);
    if (file) {
      toast.info(`模板已选择：${file.name}`);
    }
    if (templateInputRef.current) templateInputRef.current.value = '';
  }, []);

  const handleSend = useCallback(async () => {
    const text = inputText.trim();
    if (!text || isExecuting) return;

    setInputText('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    addAgentMessage({ role: 'user', text, timestamp: Date.now() });

    let activeContextId = agentContextId;
    if (!activeContextId) {
      try {
        const conversation = await createConversation();
        activeContextId = conversation.conversation_id;
        setAgentContextId(activeContextId);
        refreshConversations();
      } catch (err) {
        const msg = err instanceof Error ? err.message : '创建对话失败';
        addAgentMessage({ role: 'assistant', text: `错误：${msg}`, timestamp: Date.now() });
        toast.error(msg);
        return;
      }
    }

    let runtimeParsedDocIds = parsedScopedDocIds;
    let runtimeDocumentSetId = effectiveDocumentSetId;
    try {
      const latestDocuments = await refreshAvailableDocuments();
      const latestScope = resolveAgentDocumentScope(
        mergeKnownDocuments(latestDocuments, uploadedDocuments),
        currentDocumentSetId,
      );
      runtimeParsedDocIds = latestScope.scopedDocuments
        .filter((doc) => doc.status === 'parsed')
        .map((doc) => doc.doc_id);
      runtimeDocumentSetId = latestScope.effectiveDocumentSetId;
    } catch {
      // Keep the locally derived scope if refresh fails.
    }

    if (templateFile && runtimeParsedDocIds.length === 0) {
      const warning = '当前还没有已解析的源文档。请先上传并完成原始文档解析，再回填模板。';
      addAgentMessage({ role: 'assistant', text: warning, timestamp: Date.now() });
      toast.info(warning);
      return;
    }

    // If there's a template file, do template fill instead of agent execute
    if (templateFile) {
      setIsExecuting(true);
      try {
        const resp = await runAgentExecute({
          message: text,
          contextId: activeContextId ?? undefined,
          documentSetId: runtimeDocumentSetId ?? undefined,
          documentIds: runtimeParsedDocIds,
          autoMatch: true,
          templateFile,
        });
        if (resp.context_id && resp.context_id !== agentContextId) {
          setAgentContextId(resp.context_id);
        }
        if (!resp.task_id) {
          throw new Error('未返回模板回填任务 ID');
        }
        setFillTaskId(resp.task_id);
        const task = await getTaskStatus(resp.task_id);
        setFillTask(task);
        upsertTaskSnapshot(task);
        addAgentMessage({
          role: 'assistant',
          text: `模板回填任务已提交。\n模板：${resp.template_name}\n任务 ID：${resp.task_id}\n状态：${task.status}`,
          timestamp: Date.now(),
          taskId: resp.task_id,
        });
        refreshConversations();
      } catch (err) {
        const msg = err instanceof Error ? err.message : '模板回填失败';
        addAgentMessage({ role: 'assistant', text: `错误：${msg}`, timestamp: Date.now() });
        toast.error(msg);
      } finally {
        setIsExecuting(false);
      }
      return;
    }

    // Otherwise run agent execute
    setIsExecuting(true);
    try {
      const r = await runAgentExecute({
        message: text,
        contextId: activeContextId ?? undefined,
        documentSetId: runtimeDocumentSetId ?? undefined,
        documentIds: runtimeParsedDocIds,
        autoMatch: true,
      });
      if (r.context_id && r.context_id !== agentContextId) {
        setAgentContextId(r.context_id);
      }
      const summary = formatAgentReply(r);
      addAgentMessage({
        role: 'assistant',
        text: summary,
        timestamp: Date.now(),
        data: shouldRenderOperationCard(r) ? r : undefined,
      });
      refreshConversations();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Agent 执行失败';
      addAgentMessage({ role: 'assistant', text: `错误：${msg}`, timestamp: Date.now() });
      toast.error(msg);
    } finally {
      setIsExecuting(false);
    }
  }, [inputText, isExecuting, templateFile, parsedScopedDocIds, effectiveDocumentSetId, refreshAvailableDocuments, uploadedDocuments, currentDocumentSetId, upsertTaskSnapshot, addAgentMessage, agentContextId, setAgentContextId, refreshConversations]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  const handleDownloadResult = useCallback(async () => {
    if (!fillTaskId) return;
    try {
      const file = await downloadTemplateResult(fillTaskId);
      const url = window.URL.createObjectURL(file.blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = file.fileName;
      a.click();
      window.URL.revokeObjectURL(url);
      toast.success(`已下载：${file.fileName}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '下载失败');
    }
  }, [fillTaskId]);

  const handleDownloadArtifact = useCallback(async (fileName: string) => {
    try {
      const file = await downloadAgentArtifact(fileName);
      const url = window.URL.createObjectURL(file.blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = file.fileName;
      a.click();
      window.URL.revokeObjectURL(url);
      toast.success(`已下载：${file.fileName}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '下载失败');
    }
  }, []);

  const handleTraceLookup = useCallback(async () => {
    const id = traceInput.trim();
    if (!id) return;
    await openTraceByFactId(id, null);
    setTraceInput('');
  }, [traceInput, openTraceByFactId]);

  const handleClearConversation = useCallback(async () => {
    if (agentContextId) {
      clearAgentConversation(agentContextId).catch(() => {});
    }
    clearConversation();
    setFillTaskId(null);
    setFillTask(null);
    refreshConversations();
    toast.info('对话已清空');
  }, [agentContextId, clearConversation, refreshConversations]);

  const handleTextareaChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInputText(e.target.value);
    const el = e.target;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, []);

  const filledCells = useMemo<FilledCellResponse[]>(() => {
    if (!fillTask?.result) return [];
    return Array.isArray(fillTask.result.filled_cells) ? (fillTask.result.filled_cells as FilledCellResponse[]) : [];
  }, [fillTask]);

  const fillTaskDone = fillTask && ['succeeded', 'completed', 'success'].includes(fillTask.status);

  return (
    <div className="flex h-full">
      {/* ── Left: Conversation Sidebar ── */}
      <div className={`flex flex-col border-r bg-muted/30 transition-all ${sidebarOpen ? 'w-56' : 'w-0 overflow-hidden'}`}>
        <div className="flex items-center justify-between gap-1 border-b px-2 py-2">
          <span className="text-xs font-medium truncate">对话列表</span>
          <div className="flex gap-0.5">
            <Button variant="ghost" size="icon" className="h-6 w-6" onClick={handleNewConversation} title="新建对话">
              <MessageSquarePlus className="h-3.5 w-3.5" />
            </Button>
            <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => setSidebarOpen(false)} title="收起">
              <PanelLeftClose className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
        <ScrollArea className="flex-1">
          <div className="space-y-0.5 p-1">
            {conversationList.map((conv) => (
              <div
                key={conv.conversation_id}
                className={`group flex items-center gap-1 rounded px-2 py-1.5 text-xs cursor-pointer hover:bg-muted ${agentContextId === conv.conversation_id ? 'bg-muted font-medium' : ''}`}
                onClick={() => handleSwitchConversation(conv)}
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate">{getConversationDisplayTitle(conv)}</div>
                  <div className="truncate text-[10px] text-muted-foreground">
                    {getConversationPreview(conv)}
                  </div>
                </div>
                <button
                  className="invisible group-hover:visible shrink-0 text-muted-foreground hover:text-destructive"
                  onClick={(e) => { e.stopPropagation(); handleDeleteConversation(conv.conversation_id); }}
                  title="删除"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </div>
            ))}
            {conversationList.length === 0 && (
              <p className="text-[10px] text-muted-foreground text-center py-4">暂无历史对话</p>
            )}
          </div>
        </ScrollArea>
      </div>

      {/* Sidebar toggle when collapsed */}
      {!sidebarOpen && (
        <Button variant="ghost" size="icon" className="h-full w-8 shrink-0 rounded-none border-r" onClick={() => setSidebarOpen(true)} title="展开对话列表">
          <PanelLeftOpen className="h-4 w-4" />
        </Button>
      )}

    <ResizablePanelGroup className="h-full">
      {/* ── Main chat area ── */}
      <ResizablePanel defaultSize={70} minSize={40}>
      <div className="flex h-full flex-col">
        {/* Chat messages */}
        <ScrollArea className="flex-1" ref={scrollRef}>
          <div className="mx-auto max-w-3xl space-y-4 p-4">
            {messages.map((msg, i) => (
              <div key={i} className={`flex gap-3 ${msg.role === 'user' ? 'justify-end' : ''}`}>
                {msg.role !== 'user' && (
                  <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10">
                    <Bot className="h-4 w-4 text-primary" />
                  </div>
                )}
                <div
                  className={`max-w-[80%] rounded-lg px-3 py-2 text-sm ${
                    msg.role === 'user'
                      ? 'bg-primary text-primary-foreground'
                      : msg.role === 'system'
                        ? 'bg-muted text-muted-foreground'
                        : 'bg-card border'
                  }`}
                >
                  <p className="whitespace-pre-wrap">{msg.text}</p>
                  {/* Operation result cards */}
                  {msg.role === 'assistant' && msg.data && <OperationResultCard data={msg.data} onDownload={handleDownloadArtifact} />}
                </div>
                {msg.role === 'user' && (
                  <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-muted">
                    <User className="h-4 w-4 text-muted-foreground" />
                  </div>
                )}
              </div>
            ))}
            {isExecuting && (
              <div className="flex gap-3">
                <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10">
                  <Loader2 className="h-4 w-4 animate-spin text-primary" />
                </div>
                <div className="rounded-lg border bg-card px-3 py-2 text-sm text-muted-foreground">思考中…</div>
              </div>
            )}
          </div>
        </ScrollArea>

        {/* Input bar */}
        <div className="border-t bg-card p-3">
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <input ref={templateInputRef} type="file" accept=".xlsx,.docx" className="hidden" onChange={handleTemplateSelect} />
            <Button
              variant="ghost"
              size="icon"
              className="h-9 w-9 shrink-0"
              onClick={() => templateInputRef.current?.click()}
              title="选择模板文件"
            >
              <FileUp className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-9 w-9 shrink-0"
              onClick={handleClearConversation}
              title="清空对话"
            >
              <RotateCcw className="h-4 w-4" />
            </Button>
            <div className="relative flex-1">
              {templateFile && (
                <div className="mb-1 flex items-center gap-1 text-[10px] text-muted-foreground">
                  <FileSpreadsheet className="h-3 w-3" />
                  <span className="truncate">{templateFile.name}</span>
                  <button className="ml-1 text-destructive" onClick={() => setTemplateFile(null)}>
                    ✕
                  </button>
                </div>
              )}
              <textarea
                ref={textareaRef}
                value={inputText}
                onChange={handleTextareaChange}
                onKeyDown={handleKeyDown}
                placeholder={templateFile ? '输入用户要求（如：将日期从2020/7/1到2020/8/31的数据填入模板）' : '输入自然语言指令…'}
                className="w-full resize-none overflow-y-auto rounded-md border bg-background px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-ring"
                style={{ minHeight: '40px', maxHeight: '200px' }}
              />
            </div>
            <Button size="icon" className="h-9 w-9 shrink-0" disabled={!inputText.trim() || isExecuting} onClick={handleSend}>
              <Send className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>
      </ResizablePanel>

      <ResizableHandle withHandle />

      {/* ── Right sidebar: results ── */}
      <ResizablePanel defaultSize={30} minSize={15}>
      <div className="flex h-full flex-col bg-card">
        <Tabs defaultValue="result" className="flex h-full flex-col">
          <div className="border-b px-3">
            <TabsList className="h-9 w-full grid grid-cols-3">
              <TabsTrigger value="result" className="text-xs">回填结果</TabsTrigger>
              <TabsTrigger value="context" className="text-xs">上下文</TabsTrigger>
              <TabsTrigger value="trace" className="text-xs">追溯</TabsTrigger>
            </TabsList>
          </div>

          {/* ── Result tab ── */}
          <TabsContent value="result" className="flex-1 overflow-hidden m-0">
            <ScrollArea className="h-full">
              <div className="p-3 space-y-3">
                {/* Fill task status */}
                {fillTask && (
                  <Card className="p-0 shadow-none">
                    <CardContent className="p-3 space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-medium">模板回填任务</span>
                        <TaskStatusBadge status={fillTask.status} />
                      </div>
                      <div className="text-[10px] text-muted-foreground truncate">{fillTaskId}</div>
                      <div className="h-1.5 w-full rounded-full bg-muted">
                        <div
                          className="h-1.5 rounded-full bg-primary transition-all"
                          style={{ width: `${Math.round(fillTask.progress * 100)}%` }}
                        />
                      </div>
                      {fillTaskDone && (
                        <Button variant="outline" size="sm" className="w-full h-7 text-xs gap-1" onClick={handleDownloadResult}>
                          <Download className="h-3 w-3" /> 下载结果文件
                        </Button>
                      )}
                    </CardContent>
                  </Card>
                )}

                {/* Filled cells */}
                {filledCells.length > 0 && (
                  <>
                    <div className="text-xs font-medium">已填充单元格 ({filledCells.length})</div>
                    {filledCells.slice(0, 20).map((cell) => (
                      <Card
                        key={`${cell.sheet_name}-${cell.cell_ref}`}
                        className={`p-0 shadow-none ${cell.confidence < 0.7 ? 'border-amber-400 bg-amber-50 dark:bg-amber-950/20' : ''}`}
                        title={cell.evidence_text ? `来源：${cell.evidence_text}` : undefined}
                      >
                        <CardContent className="p-2 space-y-0.5">
                          <div className="flex items-center justify-between">
                            <span className="text-[11px] font-medium">{cell.sheet_name} / {cell.cell_ref}</span>
                            <div className="flex items-center gap-1">
                              {cell.confidence < 0.7 && <AlertTriangle className="h-3 w-3 text-amber-500" />}
                              <Badge variant={cell.confidence < 0.7 ? 'destructive' : 'outline'} className="text-[9px] h-4">
                                {(cell.confidence * 100).toFixed(0)}%
                              </Badge>
                            </div>
                          </div>
                          <div className="text-[10px] text-muted-foreground">
                            {cell.entity_name} · {cell.field_name}
                          </div>
                          <div className="text-xs text-foreground">{String(cell.value)}</div>
                          {cell.evidence_text && (
                            <div className="text-[9px] text-muted-foreground truncate" title={cell.evidence_text}>
                              📎 {cell.evidence_text}
                            </div>
                          )}
                          <div className="text-[9px] font-mono text-muted-foreground/60 truncate">
                            {cell.fact_id}
                          </div>
                        </CardContent>
                      </Card>
                    ))}
                  </>
                )}

                {!fillTask && filledCells.length === 0 && (
                  <p className="text-xs text-muted-foreground py-4 text-center">上传模板并发送需求后，回填结果将显示在这里。</p>
                )}
              </div>
            </ScrollArea>
          </TabsContent>

          {/* ── Context tab ── */}
          {/* ?? Context tab ?? */}
          <TabsContent value="context" className="flex-1 overflow-hidden m-0">
            <ScrollArea className="h-full">
              <div className="p-3 space-y-3 text-xs">
                <div className="space-y-1">
                  <span className="text-muted-foreground">document_set_id</span>
                  <p className="font-mono text-[10px] break-all">{effectiveDocumentSetId ?? currentDocumentSetId ?? '未设置'}</p>
                </div>
                <Separator />
                <div className="space-y-1">
                  <span className="text-muted-foreground">可用源文档 ({scopedDocumentIds.length})</span>
                  {scopedDocuments.length === 0 ? (
                    <p className="text-muted-foreground">无。请先在工作台上传文档。</p>
                  ) : (
                    scopedDocuments.map((doc) => (
                      <div key={doc.doc_id} className="flex items-center gap-1.5 py-0.5">
                        <StatusDot status={doc.status} />
                        <span className="truncate">{doc.file_name}</span>
                      </div>
                    ))
                  )}
                </div>
                <Separator />
                <div className="space-y-1">
                  <span className="text-muted-foreground">当前模板</span>
                  <p>{templateFile ? templateFile.name : '未选择'}</p>
                </div>
              </div>
            </ScrollArea>
          </TabsContent>

          {/* ?? Trace tab ?? */}
          <TabsContent value="trace" className="flex-1 overflow-hidden m-0">
            <div className="p-3 space-y-3">
              <div className="flex gap-1">
                <Input
                  value={traceInput}
                  onChange={(e) => setTraceInput(e.target.value)}
                  placeholder="输入 fact_id"
                  className="h-8 text-xs"
                />
                <Button variant="outline" size="icon" className="h-8 w-8 shrink-0" onClick={handleTraceLookup}>
                  <Search className="h-3.5 w-3.5" />
                </Button>
              </div>
              <p className="text-[10px] text-muted-foreground">输入事实 ID 后查询其来源追溯信息。</p>
            </div>
          </TabsContent>
        </Tabs>
      </div>
      </ResizablePanel>
    </ResizablePanelGroup>
    </div>
  );
}

/* ?? Helpers ?? */

function TaskStatusBadge({ status }: { status: string }) {
  if (['succeeded', 'completed', 'success'].includes(status))
    return <Badge className="text-[9px] h-4 gap-0.5 bg-green-600"><CheckCircle2 className="h-2.5 w-2.5" />完成</Badge>;
  if (status === 'failed')
    return <Badge variant="destructive" className="text-[9px] h-4 gap-0.5"><XCircle className="h-2.5 w-2.5" />失败</Badge>;
  return <Badge variant="secondary" className="text-[9px] h-4 gap-0.5"><Clock className="h-2.5 w-2.5" />进行中</Badge>;
}

function StatusDot({ status }: { status: string }) {
  const color =
    status === 'parsed' ? 'bg-green-500' : status === 'parsing' ? 'bg-amber-400' : status === 'failed' ? 'bg-red-500' : 'bg-gray-300';
  return <span className={`inline-block h-1.5 w-1.5 rounded-full ${color}`} />;
}

function mergeKnownDocuments(
  documents: DocumentResponse[],
  uploadedEntries: Array<{ document: DocumentResponse; status: string }>,
): DocumentResponse[] {
  const merged = new Map<string, DocumentResponse>();
  documents.forEach((doc) => {
    merged.set(doc.doc_id, doc);
  });
  uploadedEntries.forEach((entry) => {
    const existing = merged.get(entry.document.doc_id);
    merged.set(entry.document.doc_id, {
      ...(existing ?? entry.document),
      ...entry.document,
      status: entry.status || entry.document.status || existing?.status || 'uploaded',
    });
  });
  return Array.from(merged.values());
}

function getDocumentSetId(document: DocumentResponse): string | null {
  const rawValue = document.metadata?.document_set_id;
  return typeof rawValue === 'string' && rawValue.trim().length > 0 ? rawValue : null;
}

function isSourceDocument(document: DocumentResponse): boolean {
  return document.metadata?.document_role !== 'prompt_instruction';
}

function resolveAgentDocumentScope(
  documents: DocumentResponse[],
  currentDocumentSetId: string | null,
): { scopedDocuments: DocumentResponse[]; effectiveDocumentSetId: string | null } {
  const sourceDocuments = documents.filter(isSourceDocument);
  if (!currentDocumentSetId) {
    return { scopedDocuments: sourceDocuments, effectiveDocumentSetId: null };
  }

  const sameSetDocuments = sourceDocuments.filter((doc) => getDocumentSetId(doc) === currentDocumentSetId);
  if (sameSetDocuments.length > 0) {
    return {
      scopedDocuments: sameSetDocuments,
      effectiveDocumentSetId: currentDocumentSetId,
    };
  }

  return { scopedDocuments: sourceDocuments, effectiveDocumentSetId: null };
}


function getConversationDisplayTitle(conv: ConversationResponse): string {
  const title = typeof conv.title === 'string' ? conv.title.trim() : '';
  if (title) {
    return title;
  }
  const firstUserMessage = conv.messages.find((message) => String(message.role ?? '') === 'user');
  const fallback = String(firstUserMessage?.content ?? '').trim();
  return fallback || '未命名对话';
}

function getConversationPreview(conv: ConversationResponse): string {
  const messages = Array.isArray(conv.messages) ? conv.messages : [];
  const lastMessage = [...messages].reverse().find((message) => {
    const content = String(message.content ?? '').trim();
    return content.length > 0 && String(message.role ?? '') !== 'system';
  });
  if (!lastMessage) {
    return '暂无消息';
  }
  return String(lastMessage.content ?? '').replace(/\s+/g, ' ').trim();
}

function formatAgentReply(data: AgentExecuteResponse): string {
  const { execution_type, summary, facts, artifacts, entities, fields, template_name } = data;

  if (['conversation', 'qa', 'status', 'summary'].includes(execution_type)) {
    return summary;
  }

  if (execution_type === 'template_fill_task') {
    return [template_name ? `\u5df2\u63d0\u4ea4\u6a21\u677f\u56de\u586b\u4efb\u52a1\uff1a${template_name}` : '\u5df2\u63d0\u4ea4\u6a21\u677f\u56de\u586b\u4efb\u52a1\u3002', summary]
      .filter(Boolean)
      .join('\n');
  }

  if (execution_type === 'fact_query') {
    const scope = [
      entities.length ? `\u5b9e\u4f53\uff1a${entities.join('\u3001')}` : null,
      fields.length ? `\u5b57\u6bb5\uff1a${fields.join('\u3001')}` : null,
    ]
      .filter(Boolean)
      .join('\uff1b');
    const lead = facts.length > 0 ? `\u5df2\u5339\u914d ${facts.length} \u6761\u4e8b\u5b9e\u3002` : '\u6682\u672a\u5339\u914d\u5230\u53ef\u5c55\u793a\u7684\u4e8b\u5b9e\u3002';
    return [lead, scope || null, summary].filter(Boolean).join('\n');
  }

  if (execution_type === 'extract') {
    return `\u5df2\u5b8c\u6210\u5b57\u6bb5\u63d0\u53d6\u3002\n${summary}`;
  }

  if (execution_type === 'export') {
    return ['\u5df2\u751f\u6210\u5bfc\u51fa\u7ed3\u679c\u3002', summary, artifacts.length ? `\u4ea7\u7269\u6570\u91cf\uff1a${artifacts.length}` : null]
      .filter(Boolean)
      .join('\n');
  }

  if (execution_type === 'edit' || execution_type === 'reformat') {
    return `\u5df2\u5b8c\u6210\u6587\u6863\u5904\u7406\u3002\n${summary}`;
  }

  return summary;
}

function shouldRenderOperationCard(data: AgentExecuteResponse): boolean {
  return ['extract', 'fact_query', 'edit', 'reformat', 'export'].includes(data.execution_type);
}

const EXECUTION_LABELS: Record<string, string> = {
  edit: '\u6587\u6863\u7f16\u8f91',
  reformat: '\u683c\u5f0f\u6574\u7406',
  extract: '\u5b57\u6bb5\u63d0\u53d6',
  export: '\u7ed3\u679c\u5bfc\u51fa',
  fact_query: '\u4e8b\u5b9e\u67e5\u8be2',
  summary: '\u6587\u6863\u6458\u8981',
  qa: '\u667a\u80fd\u95ee\u7b54',
  status: '\u7cfb\u7edf\u72b6\u6001',
};

function OperationResultCard({
  data,
  onDownload,
}: {
  data: AgentExecuteResponse;
  onDownload: (fileName: string) => void;
}) {
  const { execution_type, artifacts, facts } = data;
  const label = EXECUTION_LABELS[execution_type];
  if (!label && !artifacts.length) return null;

  return (
    <div className="mt-2 space-y-2">
      {label && (
        <div className="flex items-center gap-2 flex-wrap">
          <Badge variant="secondary" className="text-[9px] h-4">{label}</Badge>
          {artifacts.map((art) =>
            art.change_count != null ? (
              <span key={art.file_name} className="text-[10px] text-muted-foreground">
                {art.operation === 'edit_document'
                  ? `${art.change_count} \u5904\u66ff\u6362`
                  : art.operation === 'extract_fields'
                    ? `${art.change_count} \u6761\u8bb0\u5f55`
                    : art.operation === 'export_results'
                      ? `${art.change_count} \u6761\u5bfc\u51fa`
                      : art.operation === 'query_facts'
                        ? `${art.change_count} \u6761\u5339\u914d`
                        : null}
              </span>
            ) : null,
          )}
        </div>
      )}

      {['extract', 'fact_query'].includes(execution_type) && facts.length > 0 && (
        <div className="rounded border text-[10px] overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b bg-muted/50">
                <th className="px-1.5 py-0.5 text-left font-medium">{'\u5b9e\u4f53'}</th>
                <th className="px-1.5 py-0.5 text-left font-medium">{'\u5b57\u6bb5'}</th>
                <th className="px-1.5 py-0.5 text-right font-medium">{'\u503c'}</th>
                <th className="px-1.5 py-0.5 text-left font-medium">{'\u5355\u4f4d'}</th>
              </tr>
            </thead>
            <tbody>
              {facts.slice(0, 8).map((f) => (
                <tr key={f.fact_id} className="border-b last:border-0">
                  <td className="px-1.5 py-0.5">{f.entity_name}</td>
                  <td className="px-1.5 py-0.5">{f.field_name}</td>
                  <td className="px-1.5 py-0.5 text-right">{f.value_num ?? f.value_text}</td>
                  <td className="px-1.5 py-0.5">{f.unit ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {facts.length > 8 && (
            <div className="px-1.5 py-0.5 text-center text-muted-foreground">{`\u2026\u53ca\u5176\u4ed6 ${facts.length - 8} \u6761`}</div>
          )}
        </div>
      )}

      {artifacts.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {artifacts.map((art) => (
            <Button
              key={art.file_name}
              variant="outline"
              size="sm"
              className="h-7 gap-1 text-xs"
              onClick={() => onDownload(art.file_name)}
            >
              <Download className="h-3 w-3" /> {art.file_name}
            </Button>
          ))}
        </div>
      )}
    </div>
  );
}

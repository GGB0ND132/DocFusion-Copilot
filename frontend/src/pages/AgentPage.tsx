import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
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
  RotateCcw,
  MessageSquarePlus,
  Trash2,
  Sparkles,
  CheckSquare,
  Square,
  X,
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

  type SuggestDocumentCandidate,
  type TaskResponse,
} from '@/services';
import DocumentSelectDialog from '@/components/DocumentSelectDialog';
import { scoreDocumentRelevance } from '@/lib/relevance';

export default function AgentPage() {
  const scrollRef = useRef<HTMLDivElement>(null);
  const templateInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const messages = useUiStore((s) => s.agentMessages);
  const addAgentMessage = useUiStore((s) => s.addAgentMessage);
  const agentContextId = useUiStore((s) => s.agentContextId);
  const setAgentContextId = useUiStore((s) => s.setAgentContextId);
  const clearConversation = useUiStore((s) => s.clearAgentConversation);

  const fillTaskHistory = useUiStore((s) => s.fillTaskHistory);
  const fillTasksHydrated = useUiStore((s) => s.fillTasksHydrated);
  const loadFillTasks = useUiStore((s) => s.loadFillTasks);
  const upsertFillTask = useUiStore((s) => s.upsertFillTask);
  const removeFillTask = useUiStore((s) => s.removeFillTask);

  const [inputText, setInputText] = useState('');
  const [isExecuting, setIsExecuting] = useState(false);
  const [thinkingStartTime, setThinkingStartTime] = useState<number | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const [templateFile, setTemplateFile] = useState<File | null>(null);
  const [availableDocuments, setAvailableDocuments] = useState<DocumentResponse[]>([]);
  const [documentsHydrated, setDocumentsHydrated] = useState(false);

  // ── Document selection dialog state ──
  const [docSelectOpen, setDocSelectOpen] = useState(false);
  const [docSelectCandidates, setDocSelectCandidates] = useState<SuggestDocumentCandidate[]>([]);
  const [docSelectTemplateName, setDocSelectTemplateName] = useState('');
  const [docSelectFieldNames, setDocSelectFieldNames] = useState<string[]>([]);
  const [pendingFillText, setPendingFillText] = useState('');
  const [pendingFillContextId, setPendingFillContextId] = useState<string | null>(null);
  const [pendingFillDocSetId, setPendingFillDocSetId] = useState<string | null>(null);
  const [fillSourceDocs, setFillSourceDocs] = useState<{ id: string; name: string }[]>([]);

  const uploadedDocuments = useUiStore((s) => s.uploadedDocuments);
  const currentDocumentSetId = useUiStore((s) => s.currentDocumentSetId);
  const upsertTaskSnapshot = useUiStore((s) => s.upsertTaskSnapshot);

  const conversationList = useUiStore((s) => s.conversationList);
  const setConversationList = useUiStore((s) => s.setConversationList);
  const switchConversation = useUiStore((s) => s.switchConversation);
  const startNewConversation = useUiStore((s) => s.startNewConversation);
  const removeConversationFromList = useUiStore((s) => s.removeConversationFromList);

  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [selectMode, setSelectMode] = useState(false);
  const [selectedConvIds, setSelectedConvIds] = useState<Set<string>>(new Set());

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

  // ── Document selection confirm handler ──
  const handleDocSelectConfirm = useCallback(async (selectedDocIds: string[]) => {
    setDocSelectOpen(false);
    if (!templateFile || selectedDocIds.length === 0) return;
    const templateNameAtSubmit = templateFile.name;
    const selectedNames = selectedDocIds
      .map((id) => docSelectCandidates.find((c) => c.doc_id === id)?.file_name ?? id);
    setFillSourceDocs(
      selectedDocIds.map((id) => ({
        id,
        name: docSelectCandidates.find((c) => c.doc_id === id)?.file_name ?? id,
      })),
    );
    const ac = new AbortController();
    abortControllerRef.current = ac;
    setIsExecuting(true);
    setThinkingStartTime(Date.now());
    try {
      const resp = await runAgentExecute({
        message: pendingFillText,
        contextId: pendingFillContextId ?? undefined,
        documentSetId: pendingFillDocSetId ?? undefined,
        documentIds: selectedDocIds,
        autoMatch: false,
        templateFile,
        userRequirement: pendingFillText,
      }, { signal: ac.signal });
      if (resp.context_id && resp.context_id !== agentContextId) {
        setAgentContextId(resp.context_id);
      }
      if (!resp.task_id) {
        throw new Error('未返回模板回填任务 ID');
      }
      const task = await getTaskStatus(resp.task_id);
      upsertTaskSnapshot(task);
      upsertFillTask(task);
      const displayTemplate = resp.template_name || templateNameAtSubmit;
      const docList = selectedNames.map((n) => `- ${n}`).join('\n');
      addAgentMessage({
        role: 'assistant',
        text:
          `模板回填任务已提交。\n` +
          `**模板**：${displayTemplate}\n` +
          `**源文档 (${selectedNames.length})**：\n${docList}\n` +
          `**任务 ID**：${resp.task_id}\n` +
          `**状态**：${task.status}`,
        timestamp: Date.now(),
        taskId: resp.task_id,
      });
      // reset template selection so next turn is clean
      setTemplateFile(null);
      refreshConversations();
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        addAgentMessage({ role: 'assistant', text: '已中止回答。', timestamp: Date.now() });
      } else {
        const msg = err instanceof Error ? err.message : '模板回填失败';
        addAgentMessage({ role: 'assistant', text: `错误：${msg}`, timestamp: Date.now() });
        toast.error(msg);
      }
    } finally {
      abortControllerRef.current = null;
      setIsExecuting(false);
      setThinkingStartTime(null);
    }
  }, [templateFile, pendingFillText, pendingFillContextId, pendingFillDocSetId, docSelectCandidates, agentContextId, setAgentContextId, addAgentMessage, upsertTaskSnapshot, upsertFillTask, refreshConversations]);

  const handleDocSelectCancel = useCallback(() => {
    setDocSelectOpen(false);
    addAgentMessage({ role: 'assistant', text: '已取消模板回填。', timestamp: Date.now() });
  }, [addAgentMessage]);

  const handleNewConversation = useCallback(() => {
    startNewConversation();
  }, [startNewConversation]);

  const handleSwitchConversation = useCallback(async (conv: ConversationResponse) => {
    switchConversation(conv);
    try {
      const fullConversation = await getConversation(conv.conversation_id);
      switchConversation(fullConversation);
      setConversationList(
        conversationList.map((item) => (
          item.conversation_id === fullConversation.conversation_id ? fullConversation : item
        )),
      );
    } catch {
      toast.error('加载对话详情失败');
    }
  }, [switchConversation, setConversationList, conversationList]);

  const handleDeleteConversation = useCallback(async (convId: string) => {
    try {
      await deleteConversation(convId);
      removeConversationFromList(convId);
      if (agentContextId === convId) {
        startNewConversation();
      }
      toast.info('对话已删除');
    } catch {
      toast.error('删除对话失败');
    }
  }, [agentContextId, removeConversationFromList, startNewConversation]);

  const toggleConvSelection = useCallback((convId: string) => {
    setSelectedConvIds((prev) => {
      const next = new Set(prev);
      if (next.has(convId)) next.delete(convId); else next.add(convId);
      return next;
    });
  }, []);

  const handleBatchDelete = useCallback(async () => {
    if (selectedConvIds.size === 0) return;
    const ids = [...selectedConvIds];
    let deleted = 0;
    for (const id of ids) {
      try {
        await deleteConversation(id);
        removeConversationFromList(id);
        if (agentContextId === id) {
          startNewConversation();
        }
        deleted++;
      } catch { /* continue */ }
    }
    setSelectedConvIds(new Set());
    setSelectMode(false);
    toast.info(`已删除 ${deleted} 个对话`);
  }, [selectedConvIds, agentContextId, removeConversationFromList, startNewConversation]);

  const handleSelectAll = useCallback(() => {
    if (selectedConvIds.size === conversationList.length) {
      setSelectedConvIds(new Set());
    } else {
      setSelectedConvIds(new Set(conversationList.map((c) => c.conversation_id)));
    }
  }, [selectedConvIds.size, conversationList]);

  const exitSelectMode = useCallback(() => {
    setSelectMode(false);
    setSelectedConvIds(new Set());
  }, []);

  // Poll fill task status for all pending tasks
  useEffect(() => {
    if (!fillTasksHydrated) return;
    const pendingIds = fillTaskHistory
      .filter((t) => !['succeeded', 'completed', 'success', 'failed'].includes(t.status))
      .map((t) => t.task_id);
    if (pendingIds.length === 0) return;
    const timer = window.setInterval(async () => {
      for (const id of pendingIds) {
        try {
          const t = await getTaskStatus(id);
          upsertTaskSnapshot(t);
          upsertFillTask(t);
          // Update only the status line in the bound assistant message (in place).
          useUiStore.setState((state) => {
            const next = state.agentMessages.map((m) => {
              if (m.role !== 'assistant' || m.taskId !== id) return m;
              const patched = m.text.replace(
                /(\*\*状态\*\*：)\S.*/,
                `$1${t.status}`,
              );
              return patched === m.text ? m : { ...m, text: patched };
            });
            return { agentMessages: next };
          });
        } catch { /* ignore */ }
      }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [fillTaskHistory, fillTasksHydrated, upsertTaskSnapshot, upsertFillTask]);

  // Hydrate fill task history on mount
  useEffect(() => {
    if (!fillTasksHydrated) {
      loadFillTasks();
    }
  }, [fillTasksHydrated, loadFillTasks]);

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

  const handleAbort = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
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

    const ac = new AbortController();
    abortControllerRef.current = ac;
    setIsExecuting(true);
    setThinkingStartTime(Date.now());

    // ── 模板回填快捷路径：跳过 agent 推理和 embedding 搜索，直接弹出文档选择 ──
    if (templateFile) {
      // 构造候选列表：全部已解析源文档（不限 scope），按模板名模糊匹配排序
      const allKnown = mergeKnownDocuments(
        await refreshAvailableDocuments().catch(() => availableDocuments),
        uploadedDocuments,
      );
      const allParsedSrc = allKnown
        .filter(isSourceDocument)
        .filter((doc) => doc.status === 'parsed');

      const tplName = templateFile.name;
      const scored = allParsedSrc.map((doc) => ({
        doc,
        score: scoreDocumentRelevance(tplName, doc.file_name),
      }));
      scored.sort((a, b) => b.score - a.score);

      const latestParsedDocs: SuggestDocumentCandidate[] = scored.map(({ doc, score }) => ({
        doc_id: doc.doc_id,
        file_name: doc.file_name,
        score,
        field_hits: [] as string[],
        entity_hits: [] as string[],
        keyword_hits: [] as string[],
        recommended: false,
      }));

      if (latestParsedDocs.length === 0) {
        addAgentMessage({
          role: 'assistant',
          text: '没有找到可用于回填的已解析源文档。请先上传源文档并完成解析。',
          timestamp: Date.now(),
        });
      } else {
        setPendingFillText(text);
        setPendingFillContextId(activeContextId);
        setPendingFillDocSetId(runtimeDocumentSetId);
        setDocSelectCandidates(latestParsedDocs);
        setDocSelectTemplateName(templateFile.name);
        setDocSelectFieldNames([]);
        setDocSelectOpen(true);
      }
      abortControllerRef.current = null;
      setIsExecuting(false);
      setThinkingStartTime(null);
      return;
    }

    // ── 非模板路径：走 agent LLM 推理 ──
    try {
      const r = await runAgentExecute({
        message: text,
        contextId: activeContextId ?? undefined,
        documentSetId: runtimeDocumentSetId ?? undefined,
        documentIds: runtimeParsedDocIds,
        autoMatch: true,
      }, { signal: ac.signal });
      if (r.context_id && r.context_id !== agentContextId) {
        setAgentContextId(r.context_id);
      }

      // Normal response for non-fill intents
      const elapsed = thinkingStartTime ? ((Date.now() - thinkingStartTime) / 1000).toFixed(1) : null;
      const summary = formatAgentReply(r);
      const timeTag = elapsed ? `\n\n> ⏱️ 思考耗时 ${elapsed}s` : '';
      addAgentMessage({
        role: 'assistant',
        text: summary + timeTag,
        timestamp: Date.now(),
        data: shouldRenderOperationCard(r) ? r : undefined,
      });
      refreshConversations();
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        addAgentMessage({ role: 'assistant', text: '已中止回答。', timestamp: Date.now() });
      } else {
        const msg = err instanceof Error ? err.message : 'Agent 执行失败';
        addAgentMessage({ role: 'assistant', text: `错误：${msg}`, timestamp: Date.now() });
        toast.error(msg);
      }
    } finally {
      abortControllerRef.current = null;
      setIsExecuting(false);
      setThinkingStartTime(null);
    }
  }, [inputText, isExecuting, templateFile, parsedScopedDocIds, knownDocuments, effectiveDocumentSetId, refreshAvailableDocuments, uploadedDocuments, currentDocumentSetId, upsertTaskSnapshot, addAgentMessage, agentContextId, setAgentContextId, refreshConversations, thinkingStartTime]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  const handleDownloadResult = useCallback(async (taskId: string) => {
    try {
      const file = await downloadTemplateResult(taskId);
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

  const handleClearConversation = useCallback(async () => {
    if (agentContextId) {
      clearAgentConversation(agentContextId).catch(() => {});
    }
    clearConversation();
    refreshConversations();
    toast.info('对话已清空');
  }, [agentContextId, clearConversation, refreshConversations]);

  const handleTextareaChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInputText(e.target.value);
    const el = e.target;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, []);

  return (
    <>
    <ResizablePanelGroup className="h-full">
      {/* ── Left: Conversation Sidebar ── */}
      <div className={`flex flex-col border-r bg-muted/30 transition-all ${sidebarOpen ? 'w-56' : 'w-0 overflow-hidden'}`}>
        <div className="flex items-center justify-between gap-1 border-b px-2 py-2">
          {selectMode ? (
            <>
              <span className="text-xs font-medium truncate text-destructive">已选 {selectedConvIds.size}</span>
              <div className="flex gap-0.5">
                <Button variant="ghost" size="icon" className="h-6 w-6" onClick={handleSelectAll} title={selectedConvIds.size === conversationList.length ? '取消全选' : '全选'}>
                  <CheckSquare className="h-3.5 w-3.5" />
                </Button>
                <Button variant="ghost" size="icon" className="h-6 w-6 text-destructive hover:text-destructive" onClick={handleBatchDelete} title="删除选中" disabled={selectedConvIds.size === 0}>
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
                <Button variant="ghost" size="icon" className="h-6 w-6" onClick={exitSelectMode} title="取消">
                  <X className="h-3.5 w-3.5" />
                </Button>
              </div>
            </>
          ) : (
            <>
              <span className="text-xs font-medium truncate">对话列表</span>
              <div className="flex gap-0.5">
                <Button variant="ghost" size="icon" className="h-6 w-6" onClick={handleNewConversation} title="新建对话">
                  <MessageSquarePlus className="h-3.5 w-3.5" />
                </Button>
                {conversationList.length > 0 && (
                  <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => setSelectMode(true)} title="批量删除">
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                )}
                <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => setSidebarOpen(false)} title="收起">
                  <PanelLeftClose className="h-3.5 w-3.5" />
                </Button>
              </div>
            </>
          )}
        </div>
        <div className="flex-1 overflow-y-auto overflow-x-hidden">
          <div className="p-2 space-y-0.5">
            {conversationList.map((conv) => (
              <div
                key={conv.conversation_id}
                className={`group flex items-center gap-1 rounded px-2 py-1.5 text-xs cursor-pointer hover:bg-muted overflow-hidden ${agentContextId === conv.conversation_id ? 'bg-muted font-medium' : ''} ${selectMode && selectedConvIds.has(conv.conversation_id) ? 'bg-primary/10' : ''}`}
                onClick={() => selectMode ? toggleConvSelection(conv.conversation_id) : handleSwitchConversation(conv)}
              >
                {selectMode && (
                  <span className="shrink-0">
                    {selectedConvIds.has(conv.conversation_id)
                      ? <CheckSquare className="h-3.5 w-3.5 text-primary" />
                      : <Square className="h-3.5 w-3.5 text-muted-foreground/50" />}
                  </span>
                )}
                <div className="min-w-0 flex-1">
                  <div className="truncate">{getConversationDisplayTitle(conv)}</div>
                  <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
                    <span className="shrink-0">{formatConversationTime(conv.updated_at)}</span>
                    <span className="truncate">{getConversationPreview(conv)}</span>
                  </div>
                </div>

              </div>
            ))}
            {conversationList.length === 0 && (
              <p className="px-2 py-4 text-center text-xs text-muted-foreground">暂无历史对话</p>
            )}
          </div>
        </div>
      </div>

      {/* ── Sidebar expand button (visible when collapsed) ── */}
      {!sidebarOpen && (
        <div className="flex items-center border-r">
          <Button variant="ghost" size="icon" className="h-8 w-8 rounded-none" onClick={() => setSidebarOpen(true)} title="展开对话列表">
            <PanelLeftOpen className="h-4 w-4" />
          </Button>
        </div>
      )}

      {/* ── Main chat area ── */}
      <ResizablePanel defaultSize={48} minSize={30}>
      <div className="flex h-full flex-col">
        {/* Chat messages */}
        <ScrollArea className="flex-1" ref={scrollRef}>
          <div className="mx-auto max-w-3xl space-y-4 p-4">
            {messages.length === 0 && !isExecuting && (
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <Bot className="h-10 w-10 text-primary/40 mb-3" />
                <h3 className="text-sm font-medium mb-1">欢迎使用 DocFusion Agent</h3>
                <p className="text-xs text-muted-foreground mb-4 max-w-md">
                  上传文档后，您可以用自然语言指令完成以下操作：
                </p>
                <div className="grid grid-cols-2 gap-2 text-[11px] text-left max-w-sm">
                  {[
                    ['📊', '帮我智能填表', '上传模板 + 发送指令'],
                    ['🔍', '查询上海的 GDP', '按实体/字段精确查询'],
                    ['📝', '总结一下这些文档', '自动生成文档摘要'],
                    ['✂️', '把甲方替换为乙方', '编辑/删除/替换内容'],
                    ['📥', '导出为 Excel', '将事实数据导出为文件'],
                    ['📁', '当前系统状态', '查看文档/解析进度'],
                  ].map(([icon, title, desc]) => (
                    <button
                      key={title}
                      className="flex items-start gap-1.5 rounded-md border p-2 hover:bg-muted transition-colors text-left"
                      onClick={() => { setInputText(title); textareaRef.current?.focus(); }}
                    >
                      <span>{icon}</span>
                      <div>
                        <div className="font-medium">{title}</div>
                        <div className="text-muted-foreground text-[10px]">{desc}</div>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            )}
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
                  {msg.role === 'assistant' ? (
                    <div className="prose prose-sm max-w-none dark:prose-invert prose-p:my-1 prose-headings:my-2 prose-ul:my-1 prose-li:my-0">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.text}</ReactMarkdown>
                    </div>
                  ) : (
                    <p className="whitespace-pre-wrap">{msg.text}</p>
                  )}
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
                <div className="rounded-lg border bg-card px-3 py-2 text-sm text-muted-foreground flex items-center gap-2">
                  思考中…
                  {thinkingStartTime && <ThinkingTimer startTime={thinkingStartTime} />}
                  <button
                    onClick={handleAbort}
                    className="ml-1 rounded-md border px-1.5 py-0.5 text-xs text-destructive hover:bg-destructive/10 transition-colors flex items-center gap-1"
                    title="中止回答"
                  >
                    <Square className="h-3 w-3 fill-current" /> 停止
                  </button>
                </div>
              </div>
            )}
          </div>
        </ScrollArea>

        {/* Quick prompt chips + Input bar */}
        <div className="border-t bg-card p-3">
          {!templateFile && messages.length > 0 && (
            <div className="mx-auto max-w-3xl mb-2 flex gap-1.5 overflow-x-auto pb-1 scrollbar-none">
              {['总结一下这些文档', '查询事实数据', '导出为 Excel', '当前系统状态'].map((prompt) => (
                <button
                  key={prompt}
                  className="shrink-0 rounded-full border px-2.5 py-0.5 text-[10px] text-muted-foreground hover:bg-muted hover:text-foreground transition-colors flex items-center gap-1"
                  onClick={() => { setInputText(prompt); textareaRef.current?.focus(); }}
                >
                  <Sparkles className="h-2.5 w-2.5" />
                  {prompt}
                </button>
              ))}
            </div>
          )}
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <input ref={templateInputRef} type="file" accept=".xlsx,.docx,.txt,.md" className="hidden" onChange={handleTemplateSelect} />
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
            {isExecuting ? (
              <Button size="icon" className="h-9 w-9 shrink-0" variant="destructive" onClick={handleAbort} title="中止回答">
                <Square className="h-4 w-4 fill-current" />
              </Button>
            ) : (
              <Button size="icon" className="h-9 w-9 shrink-0" disabled={!inputText.trim()} onClick={handleSend}>
                <Send className="h-4 w-4" />
              </Button>
            )}
          </div>
        </div>
      </div>
      </ResizablePanel>

      <ResizableHandle withHandle />

      {/* ── Right sidebar: results ── */}
      <ResizablePanel defaultSize={30} minSize={15}>
      <div className="flex h-full flex-col bg-card">
        <Tabs defaultValue="tasks" className="flex h-full flex-col">
          <div className="border-b px-3">
            <TabsList className="h-9 w-full grid grid-cols-2">
              <TabsTrigger value="tasks" className="text-xs">任务</TabsTrigger>
              <TabsTrigger value="context" className="text-xs">上下文</TabsTrigger>
            </TabsList>
          </div>

          {/* ── Context tab ── */}
          <TabsContent value="context" className="flex-1 overflow-hidden m-0">
            <ScrollArea className="h-full">
              <div className="p-3 space-y-3 text-xs">
                {fillSourceDocs.length > 0 && (
                  <>
                    <div className="space-y-1">
                      <span className="text-muted-foreground">回填源文档 ({fillSourceDocs.length})</span>
                      {fillSourceDocs.map((doc) => (
                        <div key={doc.id} className="flex items-center gap-1.5 py-0.5">
                          <span className="h-1.5 w-1.5 rounded-full bg-blue-500 shrink-0" />
                          <span className="truncate">{doc.name}</span>
                        </div>
                      ))}
                    </div>
                    <Separator />
                  </>
                )}
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

          {/* ── Tasks tab ── */}
          <TabsContent value="tasks" className="flex-1 overflow-hidden m-0">
            <ScrollArea className="h-full">
              <div className="p-3 space-y-3">
                {fillTaskHistory.length === 0 && (
                  <p className="text-[10px] text-muted-foreground">暂无回填任务。选择模板并提交后，任务会显示在这里。</p>
                )}
                {fillTaskHistory.map((task) => {
                  const taskId = task.task_id;
                  const done = ['succeeded', 'completed', 'success'].includes(task.status);
                  return (
                    <Card key={taskId} className="p-0 shadow-none">
                      <CardContent className="p-3 space-y-2">
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-medium">模板回填任务</span>
                          <div className="flex items-center gap-1">
                            <TaskStatusBadge status={task.status} />
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-5 w-5"
                              onClick={() => {
                                if (window.confirm('确定要删除这个任务及其结果文件吗？')) {
                                  removeFillTask(taskId).then(() => toast.info('任务已删除'));
                                }
                              }}
                              title="删除任务"
                            >
                              <Trash2 className="h-3 w-3 text-destructive" />
                            </Button>
                          </div>
                        </div>
                        <div className="flex items-center justify-between text-[10px] text-muted-foreground">
                          <span className="truncate">{taskId}</span>
                          <TaskElapsed task={task} />
                        </div>
                        <div className="h-1.5 w-full rounded-full bg-muted">
                          <div
                            className="h-1.5 rounded-full bg-primary transition-all"
                            style={{ width: `${Math.round(task.progress * 100)}%` }}
                          />
                        </div>
                        {done && (
                          <Button variant="outline" size="sm" className="w-full h-7 text-xs gap-1" onClick={() => handleDownloadResult(taskId)}>
                            <Download className="h-3 w-3" /> 下载结果文件
                          </Button>
                        )}
                      </CardContent>
                    </Card>
                  );
                })}
              </div>
            </ScrollArea>
          </TabsContent>
        </Tabs>
      </div>
      </ResizablePanel>
    </ResizablePanelGroup>

    <DocumentSelectDialog
      open={docSelectOpen}
      candidates={docSelectCandidates}
      templateName={docSelectTemplateName}
      fieldNames={docSelectFieldNames}
      onConfirm={handleDocSelectConfirm}
      onCancel={handleDocSelectCancel}
    />
    </>
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

function TaskElapsed({ task }: { task: TaskResponse }) {
  const done = ['succeeded', 'completed', 'success', 'failed'].includes(task.status);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (done) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [done]);
  const start = new Date(task.created_at).getTime();
  const end = done ? new Date(task.updated_at).getTime() : now;
  const sec = Math.max(0, Math.round((end - start) / 1000));
  const label = `${sec}s`;
  return <span className="shrink-0 tabular-nums">{done ? `耗时 ${label}` : `已用 ${label}`}</span>;
}

function StatusDot({ status }: { status: string }) {
  const color =
    status === 'parsed' ? 'bg-green-500' : status === 'parsing' ? 'bg-amber-400' : status === 'failed' ? 'bg-red-500' : 'bg-gray-300 dark:bg-gray-600';
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
      status: existing?.status || entry.status || entry.document.status || 'uploaded',
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

function formatConversationTime(isoString: string | undefined): string {
  if (!isoString) return '';
  try {
    const date = new Date(isoString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return '刚刚';
    if (diffMin < 60) return `${diffMin}分钟前`;
    const diffHour = Math.floor(diffMin / 60);
    if (diffHour < 24) return `${diffHour}小时前`;
    const isThisYear = date.getFullYear() === now.getFullYear();
    if (isThisYear) {
      return `${date.getMonth() + 1}/${date.getDate()} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
    }
    return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()}`;
  } catch {
    return '';
  }
}

function getConversationDisplayTitle(conv: ConversationResponse): string {
  const title = typeof conv.title === 'string' ? conv.title.trim() : '';
  if (title) {
    return title;
  }
  const firstUserMessage = conv.messages.find((message) => String(message.role ?? '') === 'user');
  const fallback = String(firstUserMessage?.content ?? '').trim();
  const text = fallback || '未命名对话';
  return text.length > 20 ? text.slice(0, 20) + '…' : text;
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
  const text = String(lastMessage.content ?? '').replace(/\s+/g, ' ').trim();
  return text.length > 50 ? text.slice(0, 50) + '…' : text;
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

function ThinkingTimer({ startTime }: { startTime: number }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - startTime) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [startTime]);
  const minutes = Math.floor(elapsed / 60);
  const seconds = elapsed % 60;
  const display = minutes > 0 ? `${minutes}:${String(seconds).padStart(2, '0')}` : `${seconds}s`;
  return <span className="text-xs font-mono tabular-nums text-muted-foreground/70">{display}</span>;
}
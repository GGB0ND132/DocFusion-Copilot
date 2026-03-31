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
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useUiStore } from '@/stores/uiStore';
import {
  getTaskStatus,
  runAgentExecute,
  downloadAgentArtifact,
  submitTemplateFill,
  downloadTemplateResult,
  type AgentExecuteResponse,
  type FilledCellResponse,
  type TaskResponse,
} from '@/services';

/* ── Message types for the chat log ── */
type ChatMessage =
  | { role: 'user'; text: string; timestamp: number }
  | { role: 'assistant'; text: string; timestamp: number; data?: AgentExecuteResponse | null; taskId?: string }
  | { role: 'system'; text: string; timestamp: number };

export default function AgentPage() {
  const scrollRef = useRef<HTMLDivElement>(null);
  const templateInputRef = useRef<HTMLInputElement>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([
    { role: 'system', text: '欢迎使用 DocFusion Agent。上传模板文件并输入需求，或直接输入自然语言指令。', timestamp: Date.now() },
  ]);
  const [inputText, setInputText] = useState('');
  const [isExecuting, setIsExecuting] = useState(false);
  const [templateFile, setTemplateFile] = useState<File | null>(null);
  const [fillTaskId, setFillTaskId] = useState<string | null>(null);
  const [fillTask, setFillTask] = useState<TaskResponse | null>(null);
  const [traceInput, setTraceInput] = useState('');

  const uploadedDocuments = useUiStore((s) => s.uploadedDocuments);
  const currentDocumentSetId = useUiStore((s) => s.currentDocumentSetId);
  const upsertTaskSnapshot = useUiStore((s) => s.upsertTaskSnapshot);
  const openTraceByFactId = useUiStore((s) => s.openTraceByFactId);

  const uploadedDocIds = useMemo(() => uploadedDocuments.map((d) => d.document.doc_id), [uploadedDocuments]);

  // Auto-scroll to bottom
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages]);

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
    setMessages((prev) => [...prev, { role: 'user', text, timestamp: Date.now() }]);

    // If there's a template file, do template fill instead of agent execute
    if (templateFile) {
      setIsExecuting(true);
      try {
        const resp = await submitTemplateFill({
          templateFile,
          documentSetId: currentDocumentSetId ?? 'default',
          fillMode: 'canonical',
          autoMatch: true,
          userRequirement: text,
        });
        setFillTaskId(resp.task_id);
        const task = await getTaskStatus(resp.task_id);
        setFillTask(task);
        upsertTaskSnapshot(task);
        setMessages((prev) => [
          ...prev,
          {
            role: 'assistant',
            text: `模板回填任务已提交。\n模板：${resp.template_name}\n任务 ID：${resp.task_id}\n状态：${task.status}`,
            timestamp: Date.now(),
            taskId: resp.task_id,
          },
        ]);
      } catch (err) {
        const msg = err instanceof Error ? err.message : '模板回填失败';
        setMessages((prev) => [...prev, { role: 'assistant', text: `错误：${msg}`, timestamp: Date.now() }]);
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
        documentSetId: currentDocumentSetId ?? undefined,
        autoMatch: true,
      });
      const summary = [
        `意图：${r.intent}`,
        r.entities.length ? `实体：${r.entities.join(', ')}` : null,
        r.fields.length ? `字段：${r.fields.join(', ')}` : null,
        `执行类型：${r.execution_type}`,
        r.summary,
        r.facts.length ? `提取了 ${r.facts.length} 条事实。` : null,
        r.artifacts.length ? `生成了 ${r.artifacts.length} 个产物文件。` : null,
      ]
        .filter(Boolean)
        .join('\n');

      setMessages((prev) => [...prev, { role: 'assistant', text: summary, timestamp: Date.now(), data: r }]);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Agent 执行失败';
      setMessages((prev) => [...prev, { role: 'assistant', text: `错误：${msg}`, timestamp: Date.now() }]);
      toast.error(msg);
    } finally {
      setIsExecuting(false);
    }
  }, [inputText, isExecuting, templateFile, currentDocumentSetId, upsertTaskSnapshot]);

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

  const filledCells = useMemo<FilledCellResponse[]>(() => {
    if (!fillTask?.result) return [];
    return Array.isArray(fillTask.result.filled_cells) ? (fillTask.result.filled_cells as FilledCellResponse[]) : [];
  }, [fillTask]);

  const fillTaskDone = fillTask && ['succeeded', 'completed', 'success'].includes(fillTask.status);

  return (
    <div className="flex h-full">
      {/* ── Main chat area ── */}
      <div className="flex flex-1 flex-col">
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
                  {/* Artifacts download buttons */}
                  {msg.role === 'assistant' && msg.data?.artifacts.length ? (
                    <div className="mt-2 space-y-1">
                      {msg.data.artifacts.map((art) => (
                        <Button
                          key={art.file_name}
                          variant="outline"
                          size="sm"
                          className="h-7 gap-1 text-xs"
                          onClick={() => handleDownloadArtifact(art.file_name)}
                        >
                          <Download className="h-3 w-3" /> {art.file_name}
                        </Button>
                      ))}
                    </div>
                  ) : null}
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
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                onKeyDown={handleKeyDown}
                rows={1}
                placeholder={templateFile ? '输入用户要求（如：将日期从2020/7/1到2020/8/31的数据填入模板）' : '输入自然语言指令…'}
                className="w-full resize-none rounded-md border bg-background px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-ring"
              />
            </div>
            <Button size="icon" className="h-9 w-9 shrink-0" disabled={!inputText.trim() || isExecuting} onClick={handleSend}>
              <Send className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>

      {/* ── Right sidebar: results ── */}
      <div className="flex w-80 flex-col border-l bg-card">
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
                      <Card key={`${cell.sheet_name}-${cell.cell_ref}`} className="p-0 shadow-none">
                        <CardContent className="p-2 space-y-0.5">
                          <div className="flex items-center justify-between">
                            <span className="text-[11px] font-medium">{cell.sheet_name} / {cell.cell_ref}</span>
                            <Badge variant="outline" className="text-[9px] h-4">
                              {(cell.confidence * 100).toFixed(0)}%
                            </Badge>
                          </div>
                          <div className="text-[10px] text-muted-foreground">
                            {cell.entity_name} · {cell.field_name}
                          </div>
                          <div className="text-xs text-foreground">{String(cell.value)}</div>
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
          <TabsContent value="context" className="flex-1 overflow-hidden m-0">
            <ScrollArea className="h-full">
              <div className="p-3 space-y-3 text-xs">
                <div className="space-y-1">
                  <span className="text-muted-foreground">document_set_id</span>
                  <p className="font-mono text-[10px] break-all">{currentDocumentSetId ?? '未设置'}</p>
                </div>
                <Separator />
                <div className="space-y-1">
                  <span className="text-muted-foreground">已上传文档 ({uploadedDocIds.length})</span>
                  {uploadedDocuments.length === 0 ? (
                    <p className="text-muted-foreground">无。请先在工作台上传文档。</p>
                  ) : (
                    uploadedDocuments.map((d) => (
                      <div key={d.document.doc_id} className="flex items-center gap-1.5 py-0.5">
                        <StatusDot status={d.status} />
                        <span className="truncate">{d.document.file_name}</span>
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

          {/* ── Trace tab ── */}
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
    </div>
  );
}

/* ── Helpers ── */

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

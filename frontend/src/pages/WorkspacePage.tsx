import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import { FileUp, FolderOpen, FileText, FileType, Table2, Code, RefreshCw, Trash2, Eye, CheckSquare, Square } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from '@/components/ui/resizable';
import FilePreview from '@/components/FilePreview';
import { useUiStore } from '@/stores/uiStore';
import {
  getTaskStatus,
  uploadDocumentBatch,
  listDocuments,
  getDocumentFacts,
  deleteDocument,
  batchDeleteDocuments,
  type DocumentResponse,
  type FactResponse,
} from '@/services';

export default function WorkspacePage() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [documents, setDocuments] = useState<DocumentResponse[]>([]);
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);
  const [facts, setFacts] = useState<FactResponse[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const addUploadedDocuments = useUiStore((s) => s.addUploadedDocuments);
  const currentDocumentSetId = useUiStore((s) => s.currentDocumentSetId);
  const uploadedDocuments = useUiStore((s) => s.uploadedDocuments);
  const upsertTaskSnapshot = useUiStore((s) => s.upsertTaskSnapshot);
  const removeUploadedDocument = useUiStore((s) => s.removeUploadedDocument);

  const selectedDoc = useMemo(() => documents.find((d) => d.doc_id === selectedDocId), [documents, selectedDocId]);

  // Load documents on mount
  useEffect(() => {
    listDocuments().then(setDocuments).catch(() => {});
  }, []);

  // Also refresh when uploads happen
  useEffect(() => {
    if (uploadedDocuments.length > 0) {
      listDocuments().then(setDocuments).catch(() => {});
    }
  }, [uploadedDocuments.length]);

  // Load facts when a document is selected
  useEffect(() => {
    if (!selectedDocId) {
      setFacts([]);
      return;
    }
    getDocumentFacts(selectedDocId).then(setFacts).catch(() => {});
  }, [selectedDocId]);

  const handleUpload = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files ?? []);
      if (!files.length) return;
      setIsUploading(true);
      try {
        const res = await uploadDocumentBatch(files, currentDocumentSetId ?? undefined);
        const entries = res.items.map((item) => ({
          taskId: item.task_id,
          status: item.status,
          fileSizeText: '',
          document: item.document,
        }));
        addUploadedDocuments(entries, res.document_set_id);
        const tasks = await Promise.all(res.items.map((item) => getTaskStatus(item.task_id)));
        tasks.forEach(upsertTaskSnapshot);
        toast.success(`${files.length} 份文档已上传`);
        const docs = await listDocuments();
        setDocuments(docs);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : '上传失败');
      } finally {
        setIsUploading(false);
        if (fileInputRef.current) fileInputRef.current.value = '';
      }
    },
    [addUploadedDocuments, currentDocumentSetId, upsertTaskSnapshot],
  );

  const handleRefresh = useCallback(async () => {
    const docs = await listDocuments();
    setDocuments(docs);
    toast.info('文档列表已刷新');
  }, []);

  const handleDelete = useCallback(
    async (docId: string, fileName: string) => {
      if (!window.confirm(`确定删除文档「${fileName}」？\n关联的解析块和事实将一并删除且不可恢复。`)) return;
      try {
        await deleteDocument(docId);
        removeUploadedDocument(docId);
        setDocuments((prev) => prev.filter((d) => d.doc_id !== docId));
        setSelectedIds((prev) => { const next = new Set(prev); next.delete(docId); return next; });
        if (selectedDocId === docId) {
          setSelectedDocId(null);
        }
        toast.success(`已删除：${fileName}`);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : '删除失败');
      }
    },
    [removeUploadedDocument, selectedDocId],
  );

  const toggleSelect = useCallback((docId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(docId)) next.delete(docId);
      else next.add(docId);
      return next;
    });
  }, []);

  const toggleSelectAll = useCallback(() => {
    setSelectedIds((prev) =>
      prev.size === documents.length
        ? new Set()
        : new Set(documents.map((d) => d.doc_id)),
    );
  }, [documents]);

  const handleBatchDelete = useCallback(async () => {
    if (selectedIds.size === 0) return;
    if (!window.confirm(`确定删除选中的 ${selectedIds.size} 份文档？\n关联的解析块和事实将一并删除且不可恢复。`)) return;
    try {
      await batchDeleteDocuments(Array.from(selectedIds));
      selectedIds.forEach((id) => removeUploadedDocument(id));
      setDocuments((prev) => prev.filter((d) => !selectedIds.has(d.doc_id)));
      if (selectedDocId && selectedIds.has(selectedDocId)) setSelectedDocId(null);
      toast.success(`已批量删除 ${selectedIds.size} 份文档`);
      setSelectedIds(new Set());
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '批量删除失败');
    }
  }, [selectedIds, removeUploadedDocument, selectedDocId]);

  return (
    <ResizablePanelGroup className="h-full">
      {/* ── Left: File Tree ── */}
      <ResizablePanel defaultSize={20} minSize={12}>
      <div className="flex h-full flex-col bg-card">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <span className="text-sm font-medium flex items-center gap-1.5">
            <FolderOpen className="h-4 w-4 text-primary" />
            文档管理
          </span>
          <div className="flex gap-1">
            {selectedIds.size > 0 && (
              <TooltipProvider delayDuration={300}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive" onClick={handleBatchDelete}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>批量删除 ({selectedIds.size})</TooltipContent>
                </Tooltip>
              </TooltipProvider>
            )}
            <TooltipProvider delayDuration={300}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="ghost" size="icon" className="h-7 w-7" onClick={handleRefresh}>
                    <RefreshCw className="h-3.5 w-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>刷新</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
        </div>

        {/* Upload zone */}
        <div className="px-3 py-2">
          <input ref={fileInputRef} type="file" multiple accept=".docx,.md,.txt,.xlsx,.pdf" className="hidden" onChange={handleUpload} />
          <Button variant="outline" size="sm" className="w-full gap-1.5" disabled={isUploading} onClick={() => fileInputRef.current?.click()}>
            <FileUp className="h-3.5 w-3.5" />
            {isUploading ? '上传中…' : '上传文档'}
          </Button>
        </div>

        <Separator />

        {/* File list */}
        <ScrollArea className="flex-1">
          <div className="p-2 space-y-0.5">
            {documents.length === 0 && (
              <p className="px-2 py-4 text-center text-xs text-muted-foreground">暂无文档</p>
            )}
            {documents.length > 0 && (
              <button
                onClick={toggleSelectAll}
                className="flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-[10px] text-muted-foreground hover:text-foreground transition-colors"
              >
                {selectedIds.size === documents.length ? <CheckSquare className="h-3 w-3" /> : <Square className="h-3 w-3" />}
                {selectedIds.size === documents.length ? '取消全选' : '全选'}
              </button>
            )}
            {documents.map((doc) => (
              <div
                key={doc.doc_id}
                className={`group flex w-full min-w-0 items-center gap-1.5 overflow-hidden rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-muted ${
                  selectedDocId === doc.doc_id ? 'bg-muted font-medium' : ''
                }`}
              >
                <span
                  role="checkbox"
                  aria-checked={selectedIds.has(doc.doc_id)}
                  tabIndex={0}
                  className="shrink-0 cursor-pointer text-muted-foreground hover:text-foreground"
                  onClick={(e) => { e.stopPropagation(); toggleSelect(doc.doc_id); }}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleSelect(doc.doc_id); } }}
                >
                  {selectedIds.has(doc.doc_id) ? <CheckSquare className="h-3.5 w-3.5 text-primary" /> : <Square className="h-3.5 w-3.5" />}
                </span>
                <button
                  className="flex flex-1 min-w-0 items-center gap-2"
                  onClick={() => setSelectedDocId(doc.doc_id)}
                >
                  <FileIcon docType={doc.doc_type} />
                  <span className="flex-1 truncate">{doc.file_name}</span>
                  <StatusDot status={doc.status} />
                </button>
                <span
                  role="button"
                  tabIndex={0}
                  className="shrink-0 rounded p-0.5 text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDelete(doc.doc_id, doc.file_name);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.stopPropagation();
                      handleDelete(doc.doc_id, doc.file_name);
                    }
                  }}
                >
                  <Trash2 className="h-3 w-3" />
                </span>
              </div>
            ))}
          </div>
        </ScrollArea>

        {currentDocumentSetId && (
          <div className="border-t px-3 py-1.5 text-[10px] text-muted-foreground truncate">
            批次: {currentDocumentSetId}
          </div>
        )}
      </div>
      </ResizablePanel>

      <ResizableHandle withHandle />

      {/* ── Middle: Document Preview ── */}
      <ResizablePanel defaultSize={50} minSize={25}>
      <div className="flex h-full flex-col">
        <div className="flex items-center gap-2 border-b px-4 py-2">
          <Eye className="h-4 w-4 text-muted-foreground" />
          <span className="text-xs text-muted-foreground">文档预览</span>
          {selectedDoc && <Badge variant="secondary" className="text-[10px]">{selectedDoc.doc_type.toUpperCase()}</Badge>}
        </div>
        {!selectedDoc ? (
          <div className="flex flex-1 items-center justify-center text-muted-foreground text-sm p-8">
            选择左侧文档查看内容
          </div>
        ) : (
          <div className="flex-1 overflow-hidden">
            <FilePreview docId={selectedDoc.doc_id} docType={selectedDoc.doc_type} />
          </div>
        )}
      </div>
      </ResizablePanel>

      <ResizableHandle withHandle />

      {/* ── Right: Parse Results ── */}
      <ResizablePanel defaultSize={30} minSize={15}>
      <div className="flex h-full flex-col bg-card">
        <Tabs defaultValue="facts" className="flex h-full flex-col">
          <div className="border-b px-3">
            <TabsList className="h-9 w-full grid grid-cols-3">
              <TabsTrigger value="facts" className="text-xs">事实</TabsTrigger>
              <TabsTrigger value="json" className="text-xs">JSON</TabsTrigger>
              <TabsTrigger value="info" className="text-xs">信息</TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="facts" className="flex-1 overflow-hidden m-0">
            <ScrollArea className="h-full">
              <div className="p-3 space-y-2">
                {!selectedDocId && <p className="text-xs text-muted-foreground">选择文档查看事实</p>}
                {facts.length === 0 && selectedDocId && <p className="text-xs text-muted-foreground">无事实数据</p>}
                {facts.map((f) => (
                  <Card key={f.fact_id} className="p-0 shadow-none">
                    <CardContent className="p-2.5 space-y-1">
                      <div className="flex justify-between items-start">
                        <span className="text-xs font-medium">{f.entity_name}</span>
                        <Badge variant={f.is_canonical ? 'default' : 'outline'} className="text-[9px] h-4">
                          {(f.confidence * 100).toFixed(0)}%
                        </Badge>
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {f.field_name}: <span className="text-foreground">{f.value_num ?? f.value_text}</span>
                        {f.unit && <span className="ml-0.5">{f.unit}</span>}
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </ScrollArea>
          </TabsContent>

          <TabsContent value="json" className="flex-1 overflow-hidden m-0">
            <ScrollArea className="h-full">
              <pre className="p-3 text-[10px] leading-relaxed whitespace-pre-wrap">
                {selectedDocId && facts.length > 0
                  ? JSON.stringify(
                      facts.map((f) => ({
                        entity: f.entity_name,
                        field: f.field_name,
                        value: f.value_num ?? f.value_text,
                        unit: f.unit,
                        confidence: f.confidence,
                      })),
                      null,
                      2,
                    )
                  : '选择文档查看 JSON'}
              </pre>
            </ScrollArea>
          </TabsContent>

          <TabsContent value="info" className="flex-1 overflow-hidden m-0">
            <ScrollArea className="h-full">
              <div className="p-3 space-y-3 text-xs">
                {selectedDoc ? (
                  <>
                    <InfoRow label="文件名" value={selectedDoc.file_name} />
                    <InfoRow label="类型" value={selectedDoc.doc_type.toUpperCase()} />
                    <InfoRow label="状态" value={selectedDoc.status} />
                    <InfoRow label="文档 ID" value={selectedDoc.doc_id} />
                    <InfoRow label="事实数" value={String(facts.length)} />
                    <Separator />
                    <InfoRow label="上传时间" value={selectedDoc.upload_time} />
                  </>
                ) : (
                  <p className="text-muted-foreground">选择文档查看信息</p>
                )}
              </div>
            </ScrollArea>
          </TabsContent>
        </Tabs>
      </div>
      </ResizablePanel>
    </ResizablePanelGroup>
  );
}

function FileIcon({ docType }: { docType: string }) {
  switch (docType) {
    case 'xlsx':
      return <Table2 className="h-3.5 w-3.5 text-green-600 shrink-0" />;
    case 'md':
      return <Code className="h-3.5 w-3.5 text-blue-600 shrink-0" />;
    case 'pdf':
      return <FileType className="h-3.5 w-3.5 text-red-600 shrink-0" />;
    default:
      return <FileText className="h-3.5 w-3.5 text-muted-foreground shrink-0" />;
  }
}

function StatusDot({ status }: { status: string }) {
  const color = status === 'parsed' ? 'bg-green-500' : status === 'parsing' ? 'bg-amber-400' : status === 'failed' ? 'bg-red-500' : 'bg-gray-300';
  return <span className={`inline-block h-2 w-2 rounded-full ${color}`} />;
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-2">
      <span className="text-muted-foreground shrink-0">{label}</span>
      <span className="text-right truncate font-mono">{value}</span>
    </div>
  );
}

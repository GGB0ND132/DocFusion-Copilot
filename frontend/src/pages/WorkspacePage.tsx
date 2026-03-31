import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import { FileUp, FolderOpen, FileText, Table2, Code, ChevronRight, RefreshCw, Trash2, Eye } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useUiStore } from '@/stores/uiStore';
import {
  getTaskStatus,
  uploadDocumentBatch,
  listDocuments,
  getDocumentBlocks,
  getDocumentFacts,
  type DocumentResponse,
  type BlockResponse,
  type FactResponse,
} from '@/services';

export default function WorkspacePage() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [documents, setDocuments] = useState<DocumentResponse[]>([]);
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);
  const [blocks, setBlocks] = useState<BlockResponse[]>([]);
  const [facts, setFacts] = useState<FactResponse[]>([]);
  const [loadingBlocks, setLoadingBlocks] = useState(false);
  const [isUploading, setIsUploading] = useState(false);

  const addUploadedDocuments = useUiStore((s) => s.addUploadedDocuments);
  const currentDocumentSetId = useUiStore((s) => s.currentDocumentSetId);
  const uploadedDocuments = useUiStore((s) => s.uploadedDocuments);
  const upsertTaskSnapshot = useUiStore((s) => s.upsertTaskSnapshot);
  const clearFileCache = useUiStore((s) => s.clearFileCache);

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

  // Load blocks & facts when a document is selected
  useEffect(() => {
    if (!selectedDocId) {
      setBlocks([]);
      setFacts([]);
      return;
    }
    setLoadingBlocks(true);
    Promise.all([getDocumentBlocks(selectedDocId), getDocumentFacts(selectedDocId)])
      .then(([b, f]) => {
        setBlocks(b);
        setFacts(f);
      })
      .catch(() => {})
      .finally(() => setLoadingBlocks(false));
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

  return (
    <div className="flex h-full">
      {/* ── Left: File Tree ── */}
      <div className="flex w-64 flex-col border-r bg-card">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <span className="text-sm font-medium flex items-center gap-1.5">
            <FolderOpen className="h-4 w-4 text-primary" />
            文档管理
          </span>
          <div className="flex gap-1">
            <TooltipProvider delayDuration={300}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="ghost" size="icon" className="h-7 w-7" onClick={handleRefresh}>
                    <RefreshCw className="h-3.5 w-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>刷新</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="ghost" size="icon" className="h-7 w-7" onClick={clearFileCache}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>清空缓存</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
        </div>

        {/* Upload zone */}
        <div className="px-3 py-2">
          <input ref={fileInputRef} type="file" multiple accept=".docx,.md,.txt,.xlsx" className="hidden" onChange={handleUpload} />
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
            {documents.map((doc) => (
              <button
                key={doc.doc_id}
                onClick={() => setSelectedDocId(doc.doc_id)}
                className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-muted ${
                  selectedDocId === doc.doc_id ? 'bg-muted font-medium' : ''
                }`}
              >
                <FileIcon docType={doc.doc_type} />
                <span className="flex-1 truncate">{doc.file_name}</span>
                <StatusDot status={doc.status} />
              </button>
            ))}
          </div>
        </ScrollArea>

        {currentDocumentSetId && (
          <div className="border-t px-3 py-1.5 text-[10px] text-muted-foreground truncate">
            批次: {currentDocumentSetId}
          </div>
        )}
      </div>

      {/* ── Middle: Document Preview ── */}
      <div className="flex flex-1 flex-col border-r">
        <div className="flex items-center gap-2 border-b px-4 py-2">
          <Eye className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm font-medium">文档预览</span>
          {selectedDoc && <Badge variant="secondary" className="text-[10px]">{selectedDoc.doc_type.toUpperCase()}</Badge>}
        </div>
        <ScrollArea className="flex-1">
          {!selectedDoc ? (
            <div className="flex h-full items-center justify-center text-muted-foreground text-sm p-8">
              选择左侧文档查看内容
            </div>
          ) : loadingBlocks ? (
            <div className="flex h-full items-center justify-center text-muted-foreground text-sm p-8">
              加载中…
            </div>
          ) : (
            <div className="p-4 space-y-2">
              {blocks.length === 0 ? (
                <p className="text-sm text-muted-foreground">该文档暂无解析内容（可能仍在解析中）。</p>
              ) : (
                blocks.map((block) => (
                  <div key={block.block_id} className="rounded-md border p-3 text-sm">
                    <div className="flex items-center gap-2 mb-1">
                      <Badge variant="outline" className="text-[10px]">{block.block_type}</Badge>
                      {block.section_path.length > 0 && (
                        <span className="text-[10px] text-muted-foreground flex items-center gap-0.5">
                          {block.section_path.map((s, i) => (
                            <span key={i} className="flex items-center gap-0.5">
                              {i > 0 && <ChevronRight className="h-2.5 w-2.5" />}
                              {s}
                            </span>
                          ))}
                        </span>
                      )}
                    </div>
                    <p className="whitespace-pre-wrap text-xs leading-relaxed">{block.text}</p>
                  </div>
                ))
              )}
            </div>
          )}
        </ScrollArea>
      </div>

      {/* ── Right: Parse Results ── */}
      <div className="flex w-80 flex-col bg-card">
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
                    <InfoRow label="解析块数" value={String(blocks.length)} />
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
    </div>
  );
}

function FileIcon({ docType }: { docType: string }) {
  switch (docType) {
    case 'xlsx':
      return <Table2 className="h-3.5 w-3.5 text-green-600 shrink-0" />;
    case 'md':
      return <Code className="h-3.5 w-3.5 text-blue-600 shrink-0" />;
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

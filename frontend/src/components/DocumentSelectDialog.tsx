import { useCallback, useEffect, useMemo, useState } from 'react';
import { FileSpreadsheet, Search } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { SuggestDocumentCandidate } from '@/services/types';

interface DocumentSelectDialogProps {
  open: boolean;
  candidates: SuggestDocumentCandidate[];
  templateName: string;
  fieldNames: string[];
  onConfirm: (selectedDocIds: string[]) => void;
  onCancel: () => void;
}

export default function DocumentSelectDialog({
  open,
  candidates,
  templateName,
  fieldNames,
  onConfirm,
  onCancel,
}: DocumentSelectDialogProps) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [searchQuery, setSearchQuery] = useState('');

  // Reset state when dialog opens
  useEffect(() => {
    if (open) {
      setSelectedIds(new Set());
      setSearchQuery('');
    }
  }, [open]);

  const filteredCandidates = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return candidates;
    return candidates.filter((c) => c.file_name.toLowerCase().includes(q));
  }, [candidates, searchQuery]);

  const allFilteredSelected = filteredCandidates.length > 0 &&
    filteredCandidates.every((c) => selectedIds.has(c.doc_id));

  const toggleSelectAll = useCallback(() => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allFilteredSelected) {
        for (const c of filteredCandidates) next.delete(c.doc_id);
      } else {
        for (const c of filteredCandidates) next.add(c.doc_id);
      }
      return next;
    });
  }, [filteredCandidates, allFilteredSelected]);

  const toggle = useCallback((docId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(docId)) next.delete(docId);
      else next.add(docId);
      return next;
    });
  }, []);

  const handleConfirm = useCallback(() => {
    onConfirm(Array.from(selectedIds));
  }, [selectedIds, onConfirm]);

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onCancel(); }}>
      <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FileSpreadsheet className="h-5 w-5" />
            选择源文档
          </DialogTitle>
          <DialogDescription>
            模板 <strong>{templateName}</strong> 需要以下字段的数据，请选择用于回填的源文档：
          </DialogDescription>
          {fieldNames.length > 0 && (
            <div className="flex flex-wrap gap-1 pt-1">
              {fieldNames.slice(0, 12).map((f) => (
                <Badge key={f} variant="outline" className="text-[10px]">{f}</Badge>
              ))}
              {fieldNames.length > 12 && (
                <Badge variant="outline" className="text-[10px]">+{fieldNames.length - 12}</Badge>
              )}
            </div>
          )}
        </DialogHeader>

        <div className="relative -mx-6 px-6 py-2">
          <Search className="absolute left-8 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <Input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="搜索文档名称…"
            className="h-8 pl-8 text-xs"
          />
        </div>

        <ScrollArea className="flex-1 min-h-0 -mx-6 px-6">
          <div className="space-y-2 py-2">
            {candidates.length === 0 && (
              <p className="text-sm text-muted-foreground py-4 text-center">
                没有找到可用的已解析源文档。请先上传并完成解析。
              </p>
            )}
            {candidates.length > 0 && (
              <label className="flex items-center gap-3 rounded-lg border border-dashed p-2.5 cursor-pointer hover:bg-muted/50 transition-colors">
                <Checkbox
                  checked={allFilteredSelected}
                  onCheckedChange={toggleSelectAll}
                  className="mt-0"
                />
                <span className="text-xs text-muted-foreground">
                  {allFilteredSelected ? '取消全选' : '全选'}
                  {searchQuery.trim() ? ` (过滤后 ${filteredCandidates.length} 项)` : ` (共 ${candidates.length} 项)`}
                </span>
              </label>
            )}
            {filteredCandidates.map((c) => (
              <label
                key={c.doc_id}
                className={`flex items-start gap-3 rounded-lg border p-3 cursor-pointer transition-colors ${
                  selectedIds.has(c.doc_id)
                    ? 'border-primary bg-primary/5'
                    : 'border-border hover:bg-muted/50'
                }`}
              >
                <Checkbox
                  checked={selectedIds.has(c.doc_id)}
                  onCheckedChange={() => toggle(c.doc_id)}
                  className="mt-0.5"
                />
                <div className="flex-1 min-w-0 space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium truncate">{c.file_name}</span>
                  </div>
                </div>
              </label>
            ))}
            {candidates.length > 0 && filteredCandidates.length === 0 && (
              <p className="text-sm text-muted-foreground py-4 text-center">
                没有匹配的文档，请调整搜索关键词。
              </p>
            )}
          </div>
        </ScrollArea>

        <DialogFooter className="gap-2 sm:gap-0">
          <Button variant="outline" onClick={onCancel}>
            取消
          </Button>
          <Button onClick={handleConfirm} disabled={selectedIds.size === 0}>
            确认选择 ({selectedIds.size})
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

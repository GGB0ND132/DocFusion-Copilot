import { useCallback, useEffect, useState } from 'react';
import { FileSpreadsheet, Star } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Badge } from '@/components/ui/badge';
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

  // Auto-select recommended candidates on open
  useEffect(() => {
    if (open && candidates.length > 0) {
      const recommended = new Set(
        candidates.filter((c) => c.recommended).map((c) => c.doc_id),
      );
      setSelectedIds(recommended.size > 0 ? recommended : new Set());
    }
  }, [open, candidates]);

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

        <ScrollArea className="flex-1 min-h-0 -mx-6 px-6">
          <div className="space-y-2 py-2">
            {candidates.length === 0 && (
              <p className="text-sm text-muted-foreground py-4 text-center">
                没有找到可用的已解析源文档。请先上传并完成解析。
              </p>
            )}
            {candidates.map((c) => (
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
                    {c.recommended && (
                      <Badge variant="default" className="text-[9px] h-4 gap-0.5 shrink-0">
                        <Star className="h-2.5 w-2.5" /> 推荐
                      </Badge>
                    )}
                    <span className="text-[10px] text-muted-foreground ml-auto shrink-0">
                      匹配度 {Math.round(c.score * 100)}%
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {c.field_hits.length > 0 && (
                      <span className="text-[10px] text-muted-foreground">
                        字段命中: {c.field_hits.slice(0, 5).join('、')}
                        {c.field_hits.length > 5 ? ` +${c.field_hits.length - 5}` : ''}
                      </span>
                    )}
                  </div>
                  {c.entity_hits.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      <span className="text-[10px] text-muted-foreground">
                        实体命中: {c.entity_hits.slice(0, 5).join('、')}
                        {c.entity_hits.length > 5 ? ` +${c.entity_hits.length - 5}` : ''}
                      </span>
                    </div>
                  )}
                </div>
              </label>
            ))}
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

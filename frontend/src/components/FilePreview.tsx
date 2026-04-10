import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { getDocumentRawUrl, getDocumentBlocks } from '@/services';
import type { BlockResponse } from '@/services/types';
import { Badge } from '@/components/ui/badge';

interface FilePreviewProps {
  docId: string;
  docType: string;
}

export default function FilePreview({ docId, docType }: FilePreviewProps) {
  const rawUrl = getDocumentRawUrl(docId);

  switch (docType) {
    case 'md':
      return <MarkdownPreview url={rawUrl} />;
    case 'txt':
      return <TextPreview url={rawUrl} />;
    case 'pdf':
      return <PdfPreview url={rawUrl} />;
    case 'docx':
    case 'doc':
    case 'xlsx':
    case 'xls':
      return <BlocksPreview docId={docId} docType={docType} rawUrl={rawUrl} />;
    default:
      return (
        <div className="flex h-full items-center justify-center text-muted-foreground text-sm p-8">
          该文件类型（{docType}）暂不支持在线预览。
          <a href={rawUrl} download className="ml-2 text-primary underline">下载文件</a>
        </div>
      );
  }
}

function TextPreview({ url }: { url: string }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setText(null);
    setError(null);
    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.text();
      })
      .then(setText)
      .catch((err) => setError(err.message));
  }, [url]);

  if (error) return <p className="p-4 text-sm text-destructive">加载失败：{error}</p>;
  if (text === null) return <p className="p-4 text-sm text-muted-foreground">加载中…</p>;
  return (
    <pre className="p-4 text-xs leading-relaxed whitespace-pre-wrap break-words overflow-auto h-full">
      {text}
    </pre>
  );
}

function MarkdownPreview({ url }: { url: string }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setText(null);
    setError(null);
    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.text();
      })
      .then(setText)
      .catch((err) => setError(err.message));
  }, [url]);

  if (error) return <p className="p-4 text-sm text-destructive">加载失败：{error}</p>;
  if (text === null) return <p className="p-4 text-sm text-muted-foreground">加载中…</p>;
  return (
    <div className="prose prose-sm max-w-none dark:prose-invert p-4 overflow-auto h-full">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

function PdfPreview({ url }: { url: string }) {
  return (
    <iframe
      src={url}
      title="PDF Preview"
      className="w-full h-full border-0"
    />
  );
}

function BlocksPreview({ docId, docType, rawUrl }: { docId: string; docType: string; rawUrl: string }) {
  const [blocks, setBlocks] = useState<BlockResponse[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setBlocks(null);
    setError(null);
    getDocumentBlocks(docId)
      .then(setBlocks)
      .catch((err) => setError(err instanceof Error ? err.message : '加载失败'));
  }, [docId]);

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2 text-sm text-muted-foreground p-8">
        <p>解析内容加载失败：{error}</p>
        <a href={rawUrl} download className="text-primary underline">下载原文件</a>
      </div>
    );
  }
  if (blocks === null) {
    return <p className="p-4 text-sm text-muted-foreground">加载中…</p>;
  }
  if (blocks.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2 text-sm text-muted-foreground p-8">
        <p>该文档暂无解析内容。</p>
        <a href={rawUrl} download className="text-primary underline">下载原文件</a>
      </div>
    );
  }

  const tableBlocks = blocks.filter((b) => b.block_type === 'table_row');
  const textBlocks = blocks.filter((b) => b.block_type !== 'table_row');

  return (
    <div className="overflow-auto h-full p-4 space-y-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground mb-2">
        <Badge variant="outline" className="text-[9px]">{docType.toUpperCase()}</Badge>
        <span>{blocks.length} 个解析块</span>
        <a href={rawUrl} download className="ml-auto text-primary underline text-[10px]">下载原文件</a>
      </div>

      {/* Render text blocks (headings, paragraphs) */}
      {textBlocks.map((block) => (
        <div key={block.block_id} className="text-sm">
          {block.block_type === 'heading' ? (
            <h3 className="font-semibold text-foreground">
              {block.section_path.length > 0 && (
                <span className="text-[10px] text-muted-foreground mr-1">{block.section_path.join(' › ')}</span>
              )}
              {block.text}
            </h3>
          ) : (
            <p className="text-muted-foreground leading-relaxed whitespace-pre-wrap">{block.text}</p>
          )}
        </div>
      ))}

      {/* Render table blocks as HTML table */}
      {tableBlocks.length > 0 && (
        <div className="rounded border overflow-x-auto">
          <table className="w-full text-xs">
            <tbody>
              {tableBlocks.map((block) => {
                const rowValues = block.metadata?.row_values as Record<string, string> | undefined;
                if (!rowValues) {
                  return (
                    <tr key={block.block_id} className="border-b last:border-0">
                      <td className="px-2 py-1 text-muted-foreground whitespace-pre-wrap">{block.text}</td>
                    </tr>
                  );
                }
                return (
                  <tr key={block.block_id} className="border-b last:border-0">
                    {Object.values(rowValues).map((val, i) => (
                      <td key={i} className="px-2 py-1 border-r last:border-0">{String(val)}</td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

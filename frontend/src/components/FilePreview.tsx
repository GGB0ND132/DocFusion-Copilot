import { useEffect, useState, useCallback, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Document, Page, pdfjs } from 'react-pdf';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';
import { getDocumentRawUrl, getDocumentBlocks } from '@/services';
import type { BlockResponse } from '@/services/types';
import { Badge } from '@/components/ui/badge';

// Configure PDF.js worker from CDN to match bundled version
pdfjs.GlobalWorkerOptions.workerSrc = `https://unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;

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
  const containerRef = useRef<HTMLDivElement>(null);
  const [numPages, setNumPages] = useState(0);
  const [pageNum, setPageNum] = useState(1);
  const [width, setWidth] = useState<number>(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const el = containerRef.current;
    const update = () => setWidth(el.clientWidth - 32);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2 text-sm text-muted-foreground p-8">
        <p>PDF 加载失败：{error}</p>
        <a href={url} download className="text-primary underline">下载原文件</a>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="h-full overflow-auto bg-muted/30 flex flex-col items-center py-4 relative">
      <Document
        file={url}
        onLoadSuccess={({ numPages: n }) => {
          setNumPages(n);
          setPageNum(1);
          setError(null);
        }}
        onLoadError={(err) => setError(err?.message ?? '未知错误')}
        loading={<p className="p-4 text-sm text-muted-foreground">PDF 加载中…</p>}
        error={<p className="p-4 text-sm text-destructive">PDF 加载失败</p>}
      >
        {numPages > 0 && width > 0 && (
          <Page
            pageNumber={pageNum}
            width={width}
            renderAnnotationLayer={false}
            renderTextLayer={true}
          />
        )}
      </Document>

      {numPages > 1 && (
        <div className="sticky bottom-2 mt-2 flex items-center gap-2 bg-background/90 backdrop-blur px-3 py-1.5 rounded-full border shadow text-xs">
          <button
            className="px-2 py-0.5 rounded hover:bg-muted disabled:opacity-40"
            disabled={pageNum <= 1}
            onClick={() => setPageNum((p) => Math.max(1, p - 1))}
          >
            上一页
          </button>
          <span className="tabular-nums">
            {pageNum} / {numPages}
          </span>
          <button
            className="px-2 py-0.5 rounded hover:bg-muted disabled:opacity-40"
            disabled={pageNum >= numPages}
            onClick={() => setPageNum((p) => Math.min(numPages, p + 1))}
          >
            下一页
          </button>
          <a href={url} download className="ml-2 text-primary underline">下载</a>
        </div>
      )}
    </div>
  );
}

const PAGE_SIZE = 50;

function BlocksPreview({ docId, docType, rawUrl }: { docId: string; docType: string; rawUrl: string }) {
  const [blocks, setBlocks] = useState<BlockResponse[] | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const loadPage = useCallback((p: number) => {
    setBlocks(null);
    setError(null);
    getDocumentBlocks(docId, { limit: PAGE_SIZE, offset: p * PAGE_SIZE })
      .then((res) => {
        setBlocks(res.items);
        setTotal(res.total);
        setPage(p);
      })
      .catch((err) => setError(err instanceof Error ? err.message : '加载失败'));
  }, [docId]);

  useEffect(() => { loadPage(0); }, [loadPage]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

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
  if (total === 0) {
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
        <span>{total} 个解析块</span>
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

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 pt-2 text-xs">
          <button
            className="px-2 py-1 rounded border disabled:opacity-40"
            disabled={page === 0}
            onClick={() => loadPage(page - 1)}
          >
            上一页
          </button>
          <span className="text-muted-foreground">
            {page + 1} / {totalPages}
          </span>
          <button
            className="px-2 py-1 rounded border disabled:opacity-40"
            disabled={page >= totalPages - 1}
            onClick={() => loadPage(page + 1)}
          >
            下一页
          </button>
        </div>
      )}
    </div>
  );
}

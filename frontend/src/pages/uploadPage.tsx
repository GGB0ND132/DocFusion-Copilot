import { useMemo, useRef, useState } from 'react';
import AppButton from '@/components/AppButton';
import { getTaskStatus, uploadDocumentBatch } from '@/services';
import { useUiStore } from '@/stores/uiStore';
import EmptyStateCard from '@/components/EmptyStateCard';
import ErrorStateCard from '@/components/ErrorStateCard';
import LoadingStateCard from '@/components/LoadingStateCard';

const acceptedTypes = ['DOCX', 'MD', 'TXT', 'XLSX'];

export default function UploadPage() {
	const documentInputRef = useRef<HTMLInputElement | null>(null);
	const templateInputRef = useRef<HTMLInputElement | null>(null);
	const [isUploading, setIsUploading] = useState(false);
	const [uploadError, setUploadError] = useState<string | null>(null);
	const uploadedDocuments = useUiStore((state) => state.uploadedDocuments);
	const currentDocumentSetId = useUiStore((state) => state.currentDocumentSetId);
	const selectedTemplateName = useUiStore((state) => state.selectedTemplateName);
	const addUploadedDocuments = useUiStore((state) => state.addUploadedDocuments);
	const setSelectedTemplateFile = useUiStore((state) => state.setSelectedTemplateFile);
	const upsertTaskSnapshot = useUiStore((state) => state.upsertTaskSnapshot);
	const pushToast = useUiStore((state) => state.pushToast);
	const clearFileCache = useUiStore((state) => state.clearFileCache);

	const uploadedItems = useMemo(
		() => [
			...uploadedDocuments.map((item) => ({
				name: item.document.file_name,
				size: item.fileSizeText,
				status: item.status,
				role: typeof item.document.metadata.document_role === 'string' ? item.document.metadata.document_role : 'source_document',
			})),
			...(selectedTemplateName
				? [
					{
						name: selectedTemplateName,
						size: '待提交',
						status: '模板已选择',
						role: 'template_file',
					},
				]
				: []),
		],
		[uploadedDocuments, selectedTemplateName],
	);

	async function handleDocumentSelected(event: React.ChangeEvent<HTMLInputElement>) {
		const files = Array.from(event.target.files ?? []);
		if (!files.length) {
			return;
		}

		setUploadError(null);
		setIsUploading(true);

		try {
			const response = await uploadDocumentBatch(files, currentDocumentSetId ?? undefined);
			const entries = response.items.map((item) => {
				const matchingFile = files.find((file) => file.name === item.document.file_name);
				return {
					taskId: item.task_id,
					status: item.status,
					fileSizeText: formatFileSize(matchingFile?.size ?? 0),
					document: item.document,
				};
			});
			addUploadedDocuments(entries, response.document_set_id);

			const tasks = await Promise.all(response.items.map((item) => getTaskStatus(item.task_id)));
			tasks.forEach((task) => upsertTaskSnapshot(task));
			pushToast({
				title: '文档批次上传成功',
				message: `${files.length} 份文档已进入解析队列，批次 ${response.document_set_id} 已建立。`,
				tone: 'success',
			});
		} catch (error) {
			const message = error instanceof Error ? error.message : '文档上传失败。';
			setUploadError(message);
			pushToast({
				title: '上传失败',
				message,
				tone: 'error',
			});
		} finally {
			setIsUploading(false);
			event.target.value = '';
		}
	}

	function handleTemplateSelected(event: React.ChangeEvent<HTMLInputElement>) {
		const file = event.target.files?.[0] ?? null;
		setSelectedTemplateFile(file);
		if (file) {
			pushToast({
				title: '模板已选择',
				message: `${file.name} 已缓存，可前往结果页提交回填。`,
				tone: 'info',
			});
		}
		event.target.value = '';
	}

	return (
		<div className="grid gap-6 xl:grid-cols-[1.4fr_0.9fr]">
			<section className="glass-panel rounded-[28px] border border-white/70 p-6 shadow-card">
				<div className="flex items-center justify-between gap-4">
					<div>
						<div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">步骤 01</div>
						<h3 className="mt-2 text-2xl font-semibold text-ink">上传文档与模板</h3>
					</div>
					<div className="flex gap-3">
						<AppButton
							size="sm"
							variant="ghost"
							onClick={() => {
								clearFileCache();
								pushToast({ title: '缓存已清除', message: '前端文档缓存、任务快照和模板文件已全部清空。', tone: 'info' });
							}}
						>
							清除缓存
						</AppButton>
					</div>
				</div>

				<input ref={documentInputRef} type="file" multiple className="hidden" onChange={handleDocumentSelected} />
				<input ref={templateInputRef} type="file" className="hidden" onChange={handleTemplateSelected} />

				<div className="mt-6 rounded-[28px] border-2 border-dashed border-amber-300 bg-gradient-to-br from-amber-50 to-white p-8">
					<div className="mx-auto max-w-xl text-center">
						<div className="text-sm font-semibold uppercase tracking-[0.3em] text-clay">Drop Zone</div>
						<h4 className="mt-4 text-3xl font-semibold text-ink">把比赛测试文档拖进这里</h4>
						<p className="mt-3 text-sm leading-7 text-slate-600">源文档会走批量上传接口并自动绑定 document_set_id。若测试集中包含 README.txt 这类提示词文件，后端会将其识别为 instruction 文档，保留内容但不参与事实抽取和模板匹配。</p>

						<div className="mt-6 flex flex-wrap justify-center gap-2">
							{acceptedTypes.map((type) => (
								<span key={type} className="rounded-full bg-white px-3 py-1 text-xs font-semibold text-slate-600 shadow-sm">
									{type}
								</span>
							))}
						</div>

						<div className="mt-8 flex flex-wrap justify-center gap-3">
							<AppButton
								onClick={() => documentInputRef.current?.click()}
								variant="accent"
							>
								选择原始文档批次
							</AppButton>
							<AppButton
								onClick={() => templateInputRef.current?.click()}
								variant="secondary"
							>
								选择模板文件
							</AppButton>
						</div>

						{isUploading ? <div className="mt-6"><LoadingStateCard title="文档批次正在上传" description="文件正在发送到 upload-batch 接口，请等待后端返回 document_set_id 与任务编号。" /></div> : null}
						{uploadError ? <div className="mt-6"><ErrorStateCard title="上传失败" description={uploadError} /></div> : null}
						{selectedTemplateName ? (
							<div className="mt-4 rounded-2xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">已选择模板：{selectedTemplateName}</div>
						) : null}
						{currentDocumentSetId ? (
							<div className="mt-4 rounded-2xl bg-slate-900 px-4 py-3 text-sm text-white">当前 document_set_id：{currentDocumentSetId}</div>
						) : null}
					</div>
				</div>

				<div className="mt-6 grid gap-4 md:grid-cols-3">
					<MiniCard title="原始文档区" value={`${uploadedDocuments.length} 份`} desc="源文档和提示词 txt 都会入队解析" />
					<MiniCard title="模板文件区" value={selectedTemplateName ? '1 份' : '0 份'} desc="模板在结果页触发回填" />
					<MiniCard title="当前批次" value={currentDocumentSetId ?? '未创建'} desc="上传批次与模板回填通过 document_set_id 串联" />
				</div>

				<div className="mt-6 rounded-[24px] border border-white/70 bg-white/75 p-5">
					<div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-400">当前建议流程</div>
					<div className="mt-4 grid gap-3 md:grid-cols-3">
						<FlowPill index="01" title="上传比赛文档批次" desc="docx、md、xlsx 和测试集里的 txt 会一起入队" />
						<FlowPill index="02" title="缓存模板文件" desc="模板先在前端保存，等待结果页提交" />
						<FlowPill index="03" title="带批次发起回填" desc="README.txt 这类提示词文件不会进入回填候选集" />
					</div>
				</div>
			</section>

			<section className="space-y-6">
				<div className="glass-panel rounded-[28px] border border-white/70 p-6 shadow-card">
					<div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">上传清单</div>
					<div className="mt-4 space-y-3">
						{uploadedItems.length ? (
							uploadedItems.map((file) => (
							<div key={file.name} className="rounded-2xl border border-white/80 bg-white/80 px-4 py-4">
								<div className="flex items-center justify-between gap-4">
									<div>
										<div className="text-sm font-semibold text-ink">{file.name}</div>
										<div className="mt-1 text-xs text-slate-500">{file.size}</div>
									</div>
									<div className="flex flex-col items-end gap-2">
										<span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">{file.status}</span>
										{file.role === 'prompt_instruction' ? (
											<span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-800">提示词 TXT</span>
										) : null}
									</div>
								</div>
							</div>
							))
						) : (
							<EmptyStateCard title="还没有上传内容" description="先上传原始文档，或者至少先选择一个模板文件，右侧清单会自动更新。" />
						)}
					</div>
				</div>

				<div className="glass-panel rounded-[28px] border border-white/70 p-6 shadow-card">
					<div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">联调提示</div>
					<ul className="mt-4 space-y-3 text-sm leading-7 text-slate-600">
						<li>上传入口当前面向比赛测试集，默认走 POST /api/v1/documents/upload-batch。</li>
						<li>README.txt 这类提示词文件会被解析并保留，但不会写入事实抽取结果，也不会进入模板匹配候选集。</li>
						<li>模板文件仍只在前端缓存，等结果页再调用 POST /api/v1/templates/fill。</li>
						<li>上传成功后会自动拉取一次任务状态，并保存当前批次的 document_set_id。</li>
					</ul>
				</div>
			</section>
		</div>
	);
}

function FlowPill({ index, title, desc }: { index: string; title: string; desc: string }) {
	return (
		<div className="rounded-2xl bg-slate-50 px-4 py-4">
			<div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">{index}</div>
			<div className="mt-3 text-base font-semibold text-ink">{title}</div>
			<div className="mt-2 text-sm leading-6 text-slate-500">{desc}</div>
		</div>
	);
}

function formatFileSize(bytes: number): string {
	if (bytes < 1024) {
		return `${bytes} B`;
	}
	if (bytes < 1024 * 1024) {
		return `${(bytes / 1024).toFixed(1)} KB`;
	}
	return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function MiniCard({ title, value, desc }: { title: string; value: string; desc: string }) {
	return (
		<div className="rounded-2xl border border-white/80 bg-white/85 px-4 py-4">
			<div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-400">{title}</div>
			<div className="mt-3 text-2xl font-semibold text-ink">{value}</div>
			<div className="mt-2 text-sm text-slate-500">{desc}</div>
		</div>
	);
}

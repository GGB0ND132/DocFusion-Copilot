import { useEffect, useMemo, useState } from 'react';
import AppButton from '@/components/AppButton';
import EmptyStateCard from '@/components/EmptyStateCard';
import ErrorStateCard from '@/components/ErrorStateCard';
import LoadingStateCard from '@/components/LoadingStateCard';
import StatusBadge from '@/components/StatusBadge';
import { getTaskStatus } from '@/services';
import { getTaskTypeLabel } from '@/services/taskTypes';
import { useUiStore } from '@/stores/uiStore';

export default function TaskStatusPage() {
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [taskFilter, setTaskFilter] = useState<'all' | '文档解析' | '模板回填'>('all');
  const taskSnapshots = useUiStore((state) => state.taskSnapshots);
  const latestDocumentTaskId = useUiStore((state) => state.latestDocumentTaskId);
  const latestTemplateTaskId = useUiStore((state) => state.latestTemplateTaskId);
  const upsertTaskSnapshot = useUiStore((state) => state.upsertTaskSnapshot);

  useEffect(() => {
    const activeTaskIds = Object.values(taskSnapshots)
      .filter((task) => !['succeeded', 'completed', 'success', 'failed'].includes(task.status))
      .map((task) => task.task_id);

    if (!activeTaskIds.length) {
      return undefined;
    }

    const timer = window.setInterval(async () => {
      try {
        const tasks = await Promise.all(activeTaskIds.map((taskId) => getTaskStatus(taskId)));
        tasks.forEach((task) => upsertTaskSnapshot(task));
      } catch {
        window.clearInterval(timer);
      }
    }, 4000);

    return () => window.clearInterval(timer);
  }, [taskSnapshots, upsertTaskSnapshot]);

  const taskItems = useMemo(() => {
    const mappedTasks = Object.values(taskSnapshots)
      .sort((left, right) => new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime())
      .map((task) => ({
        id: task.task_id,
        name:
          String(task.result.output_file_name ?? task.result.file_name ?? task.result.template_name ?? task.task_id),
        type: getTaskTypeLabel(task.task_type),
        status: mapTaskStatus(task.status),
        progress: Math.round(task.progress * 100),
        updatedAt: formatTime(task.updated_at),
        detail: task.error ?? task.message,
      }));

    if (taskFilter === 'all') {
      return mappedTasks;
    }

    return mappedTasks.filter((task) => task.type === taskFilter);
  }, [taskFilter, taskSnapshots]);

  const taskOverview = useMemo(() => {
    const tasks = Object.values(taskSnapshots);
    return {
      total: tasks.length,
      completed: tasks.filter((task) => ['succeeded', 'completed', 'success'].includes(task.status)).length,
      processing: tasks.filter((task) => ['queued', 'pending', 'running', 'processing'].includes(task.status)).length,
      failed: tasks.filter((task) => ['failed', 'warning'].includes(task.status)).length,
    };
  }, [taskSnapshots]);

  async function handleRefresh() {
    const knownTaskIds = [latestDocumentTaskId, latestTemplateTaskId, ...Object.keys(taskSnapshots)].filter(
      (taskId): taskId is string => Boolean(taskId),
    );

    if (!knownTaskIds.length) {
      setRefreshError('当前还没有可查询的任务，请先上传文档或提交模板回填。');
      return;
    }

    setIsRefreshing(true);
    setRefreshError(null);

    try {
      const uniqueTaskIds = Array.from(new Set(knownTaskIds));
      const tasks = await Promise.all(uniqueTaskIds.map((taskId) => getTaskStatus(taskId)));
      tasks.forEach((task) => upsertTaskSnapshot(task));
    } catch (error) {
      setRefreshError(error instanceof Error ? error.message : '任务状态刷新失败。');
    } finally {
      setIsRefreshing(false);
    }
  }

  return (
    <div className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
      <section className="glass-panel rounded-[28px] border border-white/70 p-6 shadow-card">
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">步骤 02</div>
            <h3 className="mt-2 text-2xl font-semibold text-ink">任务状态总览</h3>
          </div>
          <AppButton
            onClick={handleRefresh}
            variant="secondary"
            loading={isRefreshing}
            loadingText="刷新中..."
          >
            刷新状态
          </AppButton>
        </div>

        {isRefreshing && !taskItems.length ? (
          <div className="mt-4">
            <LoadingStateCard title="正在刷新任务状态" description="前端正在拉取当前已知任务的最新进度，请稍候。" />
          </div>
        ) : null}

        {refreshError ? <div className="mt-4"><ErrorStateCard title="任务刷新失败" description={refreshError} /></div> : null}

        <div className="mt-4 grid gap-3 md:grid-cols-4">
          <OverviewCard label="任务总数" value={String(taskOverview.total)} />
          <OverviewCard label="已完成" value={String(taskOverview.completed)} />
          <OverviewCard label="处理中" value={String(taskOverview.processing)} />
          <OverviewCard label="异常/待确认" value={String(taskOverview.failed)} />
        </div>

        <div className="mt-5 flex flex-wrap gap-2">
          {(['all', '文档解析', '模板回填'] as const).map((item) => (
            <AppButton
              key={item}
              onClick={() => setTaskFilter(item)}
              size="sm"
              variant={taskFilter === item ? 'primary' : 'secondary'}
            >
              {item === 'all' ? '全部任务' : item}
            </AppButton>
          ))}
        </div>

        <div className="mt-6 space-y-4">
          {taskItems.length ? (
            taskItems.map((task) => (
              <article key={task.id} className="rounded-[24px] border border-white/80 bg-white/85 p-5">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-400">{task.type}</div>
                    <h4 className="mt-2 text-xl font-semibold text-ink">{task.name}</h4>
                    <p className="mt-2 text-sm leading-7 text-slate-600">{task.detail}</p>
                  </div>
                  <div className="flex flex-wrap items-center gap-3">
                    <StatusBadge status={task.status} />
                    <span className="text-sm text-slate-500">{task.updatedAt}</span>
                  </div>
                </div>

                <div className="mt-5">
                  <div className="flex items-center justify-between text-sm text-slate-500">
                    <span>进度</span>
                    <span>{task.progress}%</span>
                  </div>
                  <div className="mt-2 h-3 rounded-full bg-slate-100">
                    <div
                      className="h-3 rounded-full bg-gradient-to-r from-teal to-ember"
                      style={{ width: `${task.progress}%` }}
                    />
                  </div>
                </div>
              </article>
            ))
          ) : (
            <EmptyStateCard title="没有匹配的任务" description="先在上传页创建解析任务，或切换筛选条件查看其他任务。" />
          )}
        </div>
      </section>

      <section className="space-y-6">
        <div className="glass-panel rounded-[28px] border border-white/70 p-6 shadow-card">
          <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">状态说明</div>
          <div className="mt-4 space-y-4 text-sm leading-7 text-slate-600">
            <p>当前页面已接 GET /api/v1/tasks/{'{task_id}'}，刷新时会拉取已知任务的最新状态。</p>
            <p>文档解析任务完成后，可以进入结果页提交模板回填任务。</p>
            <p>当前页面已经对未完成任务启用自动轮询，手动刷新用于立即重拉全部已知任务。</p>
          </div>
        </div>

        <div className="glass-panel rounded-[28px] border border-white/70 p-6 shadow-card">
          <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">推荐接口字段</div>
          <ul className="mt-4 space-y-2 text-sm leading-7 text-slate-600">
            <li>task_id</li>
            <li>task_type</li>
            <li>status</li>
            <li>progress</li>
            <li>updated_at</li>
            <li>message</li>
          </ul>
        </div>
      </section>
    </div>
  );
}

function mapTaskStatus(status: string): 'queued' | 'processing' | 'completed' | 'warning' {
  if (status === 'queued' || status === 'pending') {
    return 'queued';
  }
  if (status === 'running' || status === 'processing') {
    return 'processing';
  }
  if (status === 'succeeded' || status === 'completed' || status === 'success') {
    return 'completed';
  }
  return 'warning';
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function OverviewCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-white/80 bg-white/85 px-4 py-4">
      <div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">{label}</div>
      <div className="mt-3 text-2xl font-semibold text-ink">{value}</div>
    </div>
  );
}

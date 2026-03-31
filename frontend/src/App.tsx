import { Navigate, Route, Routes } from 'react-router-dom';
import { Toaster } from 'sonner';
import AppShell from '@/layouts/AppShell';
import WorkspacePage from '@/pages/WorkspacePage';
import AgentPage from '@/pages/AgentPage';

export default function App() {
  return (
    <>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/workspace" replace />} />
          <Route path="/workspace" element={<WorkspacePage />} />
          <Route path="/agent" element={<AgentPage />} />
        </Route>
      </Routes>
      <Toaster position="top-right" richColors closeButton />
    </>
  );
}
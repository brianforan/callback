import { Center, Loader } from '@mantine/core';
import { Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom';

import { PostingInsights } from './components/PostingInsights';
import { CompanyInsights, CompanyPage } from './components/CompanyInsights';
import { RejectionInsights } from './components/RejectionInsights';
import { RejectionReportPage } from './components/RejectionReport';
import { ResumeInsights } from './components/ResumeInsights';
import { VariantInsights } from './components/VariantInsights';
import { Shell } from './components/Shell';
import { useMe } from './lib/queries';
import { AdminPage } from './pages/AdminPage';
import { AdminBoardsTab } from './pages/AdminBoardsTab';
import { AdminSettingsTab } from './pages/AdminSettingsTab';
import { AdminUsersTab } from './pages/AdminUsersTab';
import { ApplicationsPage } from './pages/ApplicationsPage';
import { DashboardPage } from './pages/DashboardPage';
import { InsightsPage } from './pages/InsightsPage';
import { ListingsPage } from './pages/ListingsPage';
import { LoginPage } from './pages/LoginPage';
import { FactSheetEditorPage } from './pages/FactSheetEditorPage';
import { ResumeEditorPage } from './pages/ResumeEditorPage';
import { ResumesPage } from './pages/ResumesPage';
import { ScoutLayout } from './pages/ScoutLayout';
import { ScoutLeadsTab } from './pages/ScoutLeadsTab';
import { ScoutRunsTab } from './pages/ScoutRunsTab';
import { ScoutSearchTab } from './pages/ScoutPage';
import { ScoutTrackingTab } from './pages/ScoutTrackingTab';
import { SpendTab } from './pages/SpendPage';
import { ScoutReportPage } from './pages/ScoutReportPage';

/** Scout was called SmartSearch; its old links, bookmarks included, land on the same page. */
function RenamedToScout() {
  const { pathname, search } = useLocation();
  return <Navigate to={pathname.replace(/^\/smartsearch/, '/scout') + search} replace />;
}

function RequireAuth() {
  const { data: user, isPending, isError } = useMe();
  if (isPending) {
    return (
      <Center h="100dvh">
        <Loader />
      </Center>
    );
  }
  if (isError || !user) return <Navigate to="/login" replace />;
  return (
    <Shell user={user}>
      <Outlet />
    </Shell>
  );
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/applications" element={<ApplicationsPage />} />
        <Route path="/listings" element={<ListingsPage />} />
        {/* Tracked postings now live in Job listings, under its tracking filter. */}
        <Route path="/postings" element={<Navigate to="/listings?tracking=tracked" replace />} />
        <Route path="/scout" element={<ScoutLayout />}>
          <Route index element={<Navigate to="search" replace />} />
          <Route path="leads" element={<ScoutLeadsTab />} />
          <Route path="tracking" element={<ScoutTrackingTab />} />
          <Route path="search" element={<ScoutSearchTab />} />
          <Route path="runs" element={<ScoutRunsTab />} />
        </Route>
        <Route path="/scout/runs/:runId" element={<ScoutReportPage />} />
        <Route path="/smartsearch/*" element={<RenamedToScout />} />
        <Route path="/admin" element={<AdminPage />}>
          <Route index element={<Navigate to="users" replace />} />
          <Route path="users" element={<AdminUsersTab />} />
          <Route path="spend" element={<SpendTab />} />
          <Route path="boards" element={<AdminBoardsTab />} />
          <Route path="settings" element={<AdminSettingsTab />} />
        </Route>
        {/* Spend now lives in the Admin panel. */}
        <Route path="/spend" element={<Navigate to="/admin/spend" replace />} />
        <Route path="/resumes" element={<ResumesPage />} />
        {/* A blank scratch editor, or one opened on a saved resume's source. */}
        <Route path="/resumes/editor" element={<ResumeEditorPage />} />
        <Route path="/resumes/editor/:id" element={<ResumeEditorPage />} />
        <Route path="/resumes/fact-sheet" element={<FactSheetEditorPage />} />
        <Route path="/insights" element={<InsightsPage />}>
          <Route index element={<Navigate to="resumes" replace />} />
          <Route path="resumes" element={<ResumeInsights />} />
          <Route path="variants" element={<VariantInsights />} />
          <Route path="postings" element={<PostingInsights />} />
          <Route path="rejections" element={<RejectionInsights />} />
          <Route path="rejections/:applicationId" element={<RejectionReportPage />} />
          <Route path="companies" element={<CompanyInsights />} />
          <Route path="companies/:companyId" element={<CompanyPage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

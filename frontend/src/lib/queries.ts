import { notifications } from '@mantine/notifications';
import {
  keepPreviousData,
  MutationCache,
  QueryCache,
  QueryClient,
  useMutation,
  useQuery,
} from '@tanstack/react-query';
import { useEffect, useRef } from 'react';

import { ApiError, request } from './api';
import { clearAccountData } from './cache';
import type {
  AdminUser,
  AppSetting,
  Application,
  ApplicationPayload,
  CatalogStats,
  Communication,
  CommunicationPayload,
  CompanyCard,
  CompanyDetail,
  CompanyResearchView,
  FactSheet,
  HiddenListing,
  HideListingPayload,
  KeywordTrends,
  LeadState,
  ListingSearch,
  NextStep,
  Overview,
  Plan,
  Posting,
  PostingChanges,
  PostingDetail,
  PostingPayload,
  PostingTrends,
  Profile,
  Profiles,
  RejectAnalysisStatus,
  RejectionSummary,
  Resume,
  ResumeDetail,
  ResumeInsights,
  Role,
  Scout,
  ScoutLeadItem,
  ScoutReport,
  ScoutRun,
  ScoutSettings,
  ScoutSettingsPayload,
  SkillTermPayload,
  Skills,
  Spend,
  TailorJob,
  TailorStatus,
  TopUp,
  User,
  VariantReport,
  VariantRules,
} from './types';

function handleError(error: Error) {
  if (error instanceof ApiError && error.status === 401) {
    // The session ended. Whoever signs in next mustn't see this account's data.
    queryClient.setQueryData(['me'], null);
    clearAccountData(queryClient);
    return;
  }
  notifications.show({ color: 'red', title: 'Something went wrong', message: error.message });
}

export const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError: handleError }),
  mutationCache: new MutationCache({
    onError: (error, _variables, _context, mutation) => {
      if (!mutation.meta?.inlineError) handleError(error);
    },
  }),
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
    },
  },
});

function refreshData() {
  for (const key of [
    'applications',
    'resumes',
    'overview',
    'resume-insights',
    'variant-report',
    'keyword-trends',
    'postings',
    'posting-trends',
    'listings',
    'scout-leads',
    // Communications go with their application, so deleting or renaming one changes these.
    'next-steps',
    'communications',
    // Marking an application rejected queues its analysis.
    'reject-analysis',
    'rejection-summary',
    // And its company's research.
    'company-research',
  ]) {
    queryClient.invalidateQueries({ queryKey: [key] });
  }
}

/** Signed in: nothing cached for a previous account is kept. */
function signedIn(user: User) {
  clearAccountData(queryClient);
  queryClient.setQueryData(['me'], user);
}

export function useMe() {
  return useQuery<User | null>({
    queryKey: ['me'],
    queryFn: async () => {
      try {
        return await request<User>('/api/auth/me');
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
  });
}

export function useLogin() {
  return useMutation({
    mutationFn: (credentials: { email: string; password: string }) =>
      request<User>('/api/auth/login', { method: 'POST', body: JSON.stringify(credentials) }),
    onSuccess: signedIn,
    // The form shows login failures inline, so they skip the global toast.
    meta: { inlineError: true },
  });
}

export function useAuthConfig() {
  return useQuery({
    queryKey: ['auth-config'],
    queryFn: () => request<{ registration_enabled: boolean }>('/api/auth/config'),
  });
}

export function useRegister() {
  return useMutation({
    mutationFn: (credentials: { email: string; password: string }) =>
      request<User>('/api/auth/register', { method: 'POST', body: JSON.stringify(credentials) }),
    onSuccess: signedIn,
    // The form shows sign-up failures inline, so they skip the global toast.
    meta: { inlineError: true },
  });
}

export function useLogout() {
  return useMutation({
    mutationFn: () => request<void>('/api/auth/logout', { method: 'POST' }),
    onSuccess: () => {
      queryClient.setQueryData(['me'], null);
      clearAccountData(queryClient);
    },
  });
}

export function useProfiles() {
  return useQuery({
    queryKey: ['profiles'],
    queryFn: () => request<Profiles>('/api/profiles'),
  });
}

/** After switching, everything but the login is fetched again for the new profile. */
function showProfile(profiles: Profiles) {
  queryClient.setQueryData(['profiles'], profiles);
  queryClient.resetQueries({
    predicate: (query) => query.queryKey[0] !== 'me' && query.queryKey[0] !== 'profiles',
  });
}

function activateProfile(id: string) {
  return request<Profiles>(`/api/profiles/${id}/activate`, { method: 'POST' });
}

export function useActivateProfile() {
  return useMutation({ mutationFn: activateProfile, onSuccess: showProfile });
}

/** Makes a profile and switches to it, since a new profile is made to be filled in. */
export function useCreateProfile() {
  return useMutation({
    mutationFn: async ({ name, softwareSkills }: { name: string; softwareSkills: boolean }) => {
      const profile = await request<Profile>('/api/profiles', {
        method: 'POST',
        body: JSON.stringify({ name, software_skills: softwareSkills }),
      });
      return activateProfile(profile.id);
    },
    onSuccess: showProfile,
  });
}

export function useRenameProfile() {
  return useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) =>
      request<Profile>(`/api/profiles/${id}`, { method: 'PATCH', body: JSON.stringify({ name }) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['profiles'] }),
  });
}

export function useDeleteProfile() {
  return useMutation({
    mutationFn: async (id: string) => {
      await request<void>(`/api/profiles/${id}`, { method: 'DELETE' });
      // Deleting the profile in use moves this browser to the first one, so read it again.
      return request<Profiles>('/api/profiles');
    },
    onSuccess: showProfile,
  });
}

export function useApplications() {
  return useQuery({
    queryKey: ['applications'],
    queryFn: () => request<Application[]>('/api/applications'),
  });
}

export function useApplication(applicationId: string | undefined) {
  return useQuery({
    queryKey: ['applications', applicationId],
    queryFn: () => request<Application>(`/api/applications/${applicationId}`),
    enabled: Boolean(applicationId),
  });
}

export function useCreateApplication() {
  return useMutation({
    mutationFn: (payload: ApplicationPayload) =>
      request<Application>('/api/applications', { method: 'POST', body: JSON.stringify(payload) }),
    onSuccess: refreshData,
  });
}

export function useUpdateApplication() {
  return useMutation({
    mutationFn: ({ id, changes }: { id: string; changes: Partial<ApplicationPayload> }) =>
      request<Application>(`/api/applications/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(changes),
      }),
    onSuccess: refreshData,
  });
}

export function useDeleteApplication() {
  return useMutation({
    mutationFn: (id: string) => request<void>(`/api/applications/${id}`, { method: 'DELETE' }),
    onSuccess: refreshData,
  });
}

const PDF_POLL_MS = 5_000;

/** Every resume. Given an id, polls until that resume's PDF is made or has failed. */
export function useResumes(waitForPdf: string | null = null) {
  return useQuery({
    queryKey: ['resumes'],
    queryFn: () => request<Resume[]>('/api/resumes'),
    refetchInterval: (query) => {
      const resume = query.state.data?.find((item) => item.id === waitForPdf);
      return resume && resume.pdf_pages === null && resume.pdf_error === null
        ? PDF_POLL_MS
        : false;
    },
  });
}

export function useUploadResume() {
  return useMutation({
    // profileId saves into that profile instead of the active one, as an editor does for the
    // resume it opened, since another tab may have switched profiles since.
    mutationFn: ({ file, label, profileId }: { file: File; label: string; profileId?: string }) => {
      const body = new FormData();
      body.append('file', file);
      if (label.trim()) body.append('label', label.trim());
      if (profileId) body.append('profile_id', profileId);
      return request<Resume>('/api/resumes', { method: 'POST', body });
    },
    onSuccess: refreshData,
  });
}

export function useDeleteResume() {
  return useMutation({
    mutationFn: (id: string) => request<void>(`/api/resumes/${id}`, { method: 'DELETE' }),
    onSuccess: refreshData,
  });
}

export function useSetVariant() {
  return useMutation({
    mutationFn: ({
      id,
      isVariant,
      rules,
    }: {
      id: string;
      isVariant: boolean;
      rules: VariantRules;
    }) =>
      request<Resume>(`/api/resumes/${id}/variant`, {
        method: 'PUT',
        body: JSON.stringify({ is_variant: isVariant, variant_rules: rules }),
      }),
    onSuccess: refreshData,
  });
}

export function useUploadBaseline() {
  return useMutation({
    mutationFn: ({
      file,
      label,
      replaces,
    }: {
      file: File;
      label: string;
      replaces?: string;
    }) => {
      const body = new FormData();
      body.append('file', file);
      if (label.trim()) body.append('label', label.trim());
      if (replaces) body.append('replaces', replaces);
      return request<Resume>('/api/resumes/baselines', { method: 'POST', body });
    },
    onSuccess: refreshData,
  });
}

/** Saves edited LaTeX over a baseline, keeping its id. Its PDF is then compiled again. */
export function useSaveResumeSource() {
  return useMutation({
    mutationFn: ({ id, source }: { id: string; source: string }) =>
      request<Resume>(`/api/resumes/${id}/source`, {
        method: 'PUT',
        body: JSON.stringify({ source_tex: source }),
      }),
    onSuccess: (saved, { source }) => {
      // The editor shows the saved text at once, rather than the old text until the refetch.
      queryClient.setQueryData<ResumeDetail>(['resumes', saved.id], {
        ...saved,
        source_tex: source,
      });
      refreshData();
    },
  });
}

export function useVariantReport() {
  return useQuery({
    queryKey: ['variant-report'],
    queryFn: () => request<VariantReport>('/api/analytics/variants'),
  });
}

export function fetchResumeDetail(id: string) {
  return request<ResumeDetail>(`/api/resumes/${id}`);
}

export function useResumeDetail(id: string | null) {
  return useQuery({
    queryKey: ['resumes', id],
    queryFn: () => fetchResumeDetail(id!),
    enabled: id !== null,
  });
}

export function usePostings() {
  return useQuery({ queryKey: ['postings'], queryFn: () => request<Posting[]>('/api/postings') });
}

const POSTING_TEXT_POLL_MS = 4_000;

/** One posting, polled while the worker reads its text from its link. */
export function usePostingDetail(id: string | null) {
  return useQuery({
    queryKey: ['postings', id],
    queryFn: () => request<PostingDetail>(`/api/postings/${id}`),
    enabled: id !== null,
    refetchInterval: (query) =>
      query.state.data?.text_status === 'pending' ? POSTING_TEXT_POLL_MS : false,
  });
}

/** One posting's detail when it's needed for an action: from the cache, or fetched once. */
export function loadPostingDetail(id: string): Promise<PostingDetail> {
  return queryClient.ensureQueryData({
    queryKey: ['postings', id],
    queryFn: () => request<PostingDetail>(`/api/postings/${id}`),
  });
}

export function useCreatePosting() {
  return useMutation({
    mutationFn: (payload: PostingPayload) =>
      request<PostingDetail>('/api/postings', { method: 'POST', body: JSON.stringify(payload) }),
    onSuccess: refreshData,
  });
}

export function useUpdatePosting() {
  return useMutation({
    mutationFn: ({ id, changes }: { id: string; changes: PostingChanges }) =>
      request<PostingDetail>(`/api/postings/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(changes),
      }),
    onSuccess: refreshData,
  });
}

/** Try again to get a posting's text: from the catalog, else by reading its link. */
export function useFetchPostingText() {
  return useMutation({
    mutationFn: (id: string) =>
      request<PostingDetail>(`/api/postings/${id}/fetch-text`, { method: 'POST' }),
    onSuccess: refreshData,
  });
}

/** Text pasted into a posting saved without any. */
export function useSavePostingText() {
  return useMutation({
    mutationFn: ({ id, text }: { id: string; text: string }) =>
      request<PostingDetail>(`/api/postings/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ raw_text: text }),
      }),
    onSuccess: refreshData,
  });
}

export function useReparsePosting() {
  return useMutation({
    mutationFn: (id: string) =>
      request<PostingDetail>(`/api/postings/${id}/reparse`, { method: 'POST' }),
    onSuccess: refreshData,
  });
}

export function useStagePosting() {
  return useMutation({
    mutationFn: ({ id, resumeId, notes }: { id: string; resumeId: string; notes: string }) =>
      request<PostingDetail>(`/api/postings/${id}/stage`, {
        method: 'POST',
        body: JSON.stringify({ resume_id: resumeId, notes }),
      }),
    onSuccess: refreshData,
  });
}

export function useUnstagePosting() {
  return useMutation({
    mutationFn: (id: string) =>
      request<PostingDetail>(`/api/postings/${id}/stage`, { method: 'DELETE' }),
    onSuccess: refreshData,
  });
}

const TAILOR_POLL_MS = 3_000;

export function isTailoring(job: TailorJob | null | undefined): boolean {
  return job?.status === 'queued' || job?.status === 'running';
}

/** The posting's latest tailoring, polled while it runs. A finished one refreshes the posting. */
export function useTailorStatus(postingId: string | null) {
  const query = useQuery({
    queryKey: ['tailor', postingId],
    queryFn: () => request<TailorStatus>(`/api/postings/${postingId}/tailor`),
    enabled: postingId !== null,
    refetchInterval: (current) => (isTailoring(current.state.data?.job) ? TAILOR_POLL_MS : false),
  });
  const status = query.data?.job?.status;
  const previous = useRef(status);
  useEffect(() => {
    const was = previous.current;
    previous.current = status;
    if (was && was !== status && status === 'done') refreshData();
  }, [status]);
  return query;
}

export function useStartTailoring() {
  return useMutation({
    mutationFn: (postingId: string) =>
      request<TailorStatus>(`/api/postings/${postingId}/tailor`, { method: 'POST' }),
    onSuccess: (status, postingId) => queryClient.setQueryData(['tailor', postingId], status),
  });
}

const REJECT_POLL_MS = 4_000;

/** An application's rejection analysis, polled while it waits or runs. */
export function useRejectAnalysis(applicationId: string | null, enabled = true) {
  return useQuery({
    queryKey: ['reject-analysis', applicationId],
    queryFn: () =>
      request<RejectAnalysisStatus>(`/api/applications/${applicationId}/reject-analysis`),
    enabled: enabled && applicationId !== null,
    refetchInterval: (current) => {
      const status = current.state.data?.analysis?.status;
      return status === 'queued' || status === 'running' || current.state.data?.researching
        ? REJECT_POLL_MS
        : false;
    },
  });
}

/** Queue the analysis again, with the current resume and fact sheet. */
const RESEARCH_POLL_MS = 5_000;

/** An application's company research, polled while a snapshot waits or runs. */
export function useCompanyResearch(applicationId: string) {
  return useQuery({
    queryKey: ['company-research', applicationId],
    queryFn: () =>
      request<CompanyResearchView>(`/api/applications/${applicationId}/company-research`),
    refetchInterval: (current) => (current.state.data?.active ? RESEARCH_POLL_MS : false),
  });
}

/** Researched companies the user applied to. */
export function useMyCompanies() {
  return useQuery({
    queryKey: ['companies', 'mine'],
    queryFn: () => request<CompanyCard[]>('/api/companies/mine'),
  });
}

export function useCompanyDetail(companyId: string | undefined) {
  return useQuery({
    queryKey: ['companies', companyId],
    queryFn: () => request<CompanyDetail>(`/api/companies/${companyId}`),
    enabled: Boolean(companyId),
  });
}

export function useStartCompanyResearch() {
  return useMutation({
    mutationFn: (applicationId: string) =>
      request<CompanyResearchView>(`/api/applications/${applicationId}/company-research`, {
        method: 'POST',
      }),
    onSuccess: (view, applicationId) => {
      queryClient.setQueryData(['company-research', applicationId], view);
    },
  });
}

export function useRunRejectAnalysis() {
  return useMutation({
    mutationFn: (applicationId: string) =>
      request<RejectAnalysisStatus>(`/api/applications/${applicationId}/reject-analysis`, {
        method: 'POST',
      }),
    onSuccess: (status, applicationId) => {
      queryClient.setQueryData(['reject-analysis', applicationId], status);
      queryClient.invalidateQueries({ queryKey: ['rejection-summary'] });
      // A report asked for researches its company first.
      queryClient.invalidateQueries({ queryKey: ['company-research', applicationId] });
    },
  });
}

/** The active profile's rejections added up. Polled while any analysis waits or runs. */
export function useRejectionSummary() {
  return useQuery({
    queryKey: ['rejection-summary'],
    queryFn: () => request<RejectionSummary>('/api/reject-analyses/summary'),
    refetchInterval: (current) =>
      (current.state.data?.waiting ?? 0) > 0 ? REJECT_POLL_MS * 2 : false,
  });
}

/** Queue an analysis for every earlier rejection that has none. */
export function useBackfillRejectAnalyses() {
  return useMutation({
    mutationFn: () =>
      request<{ queued: number }>('/api/reject-analyses/backfill', { method: 'POST' }),
    onSuccess: () => {
      // Each queued report researches its company first.
      for (const key of ['rejection-summary', 'reject-analysis', 'company-research']) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
    },
  });
}

export function useFactSheet() {
  return useQuery({
    queryKey: ['fact-sheet'],
    queryFn: () => request<FactSheet>('/api/fact-sheet'),
  });
}

export function useSaveFactSheet() {
  return useMutation({
    // Saved to the profile the draft was loaded from, even if another tab switched since.
    mutationFn: ({ content, profileId }: { content: string; profileId: string }) =>
      request<FactSheet>('/api/fact-sheet', {
        method: 'PUT',
        body: JSON.stringify({ content, profile_id: profileId }),
      }),
    onSuccess: (saved) => queryClient.setQueryData(['fact-sheet'], saved),
  });
}

export function useSkills() {
  return useQuery({
    queryKey: ['skills'],
    queryFn: () => request<Skills>('/api/skills'),
  });
}

/** Saved postings are read again with the new skills, so what's built on them is fetched again. */
function skillsChanged(skills: Skills) {
  queryClient.setQueryData(['skills'], skills);
  queryClient.invalidateQueries({ queryKey: ['profiles'] });
  refreshData();
}

export function useSetSoftwareSkills() {
  return useMutation({
    mutationFn: (enabled: boolean) =>
      request<Skills>('/api/skills', {
        method: 'PATCH',
        body: JSON.stringify({ software_skills: enabled }),
      }),
    onSuccess: skillsChanged,
  });
}

export function useAddSkill() {
  return useMutation({
    mutationFn: (payload: SkillTermPayload) =>
      request<Skills>('/api/skills/terms', { method: 'POST', body: JSON.stringify(payload) }),
    onSuccess: skillsChanged,
  });
}

export function useEditSkill() {
  return useMutation({
    mutationFn: ({ id, ...payload }: SkillTermPayload & { id: string }) =>
      request<Skills>(`/api/skills/terms/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(payload),
      }),
    onSuccess: skillsChanged,
  });
}

export function useDeleteSkill() {
  return useMutation({
    mutationFn: (id: string) => request<Skills>(`/api/skills/terms/${id}`, { method: 'DELETE' }),
    onSuccess: skillsChanged,
  });
}

export function useScout() {
  return useQuery({
    queryKey: ['scout'],
    queryFn: () => request<Scout>('/api/scout'),
  });
}

export function useSaveScout() {
  return useMutation({
    mutationFn: (payload: ScoutSettingsPayload) =>
      request<ScoutSettings>('/api/scout/settings', {
        method: 'PUT',
        body: JSON.stringify(payload),
      }),
    onSuccess: (settings) =>
      queryClient.setQueryData<Scout>(['scout'], (current) =>
        current ? { ...current, settings } : current,
      ),
  });
}

const SCOUT_POLL_MS = 4_000;

export function isSearching(run: ScoutRun | undefined): boolean {
  return run?.status === 'queued' || run?.status === 'running';
}

/** Whether any of these leads has a brief on the way, so the page keeps checking for it. */
function writingBrief(leads: { brief_status: string }[] | undefined): boolean {
  return Boolean(leads?.some((lead) => ['queued', 'running'].includes(lead.brief_status)));
}

/** Recent runs, newest first, polled while one waits or runs. A finished one refreshes the rest. */
export function useScoutRuns() {
  const query = useQuery({
    queryKey: ['scout-runs'],
    queryFn: () => request<ScoutRun[]>('/api/scout/runs'),
    refetchInterval: (current) =>
      isSearching(current.state.data?.[0]) ||
      Boolean(current.state.data?.some((run) => writingBrief(run.leads)))
        ? SCOUT_POLL_MS
        : false,
  });
  const status = query.data?.[0]?.status;
  const previous = useRef(status);
  useEffect(() => {
    const was = previous.current;
    previous.current = status;
    // The allowance changes when a run ends, so the page's figures are read again. The run also
    // saved a posting for each lead, which the sidebar count and Job listings show.
    if (was && was !== status) {
      queryClient.invalidateQueries({ queryKey: ['scout'] });
      refreshData();
    }
  }, [status]);
  return query;
}

export function useScoutReport(runId: string | undefined) {
  return useQuery({
    queryKey: ['scout-report', runId],
    queryFn: () => request<ScoutReport>(`/api/scout/runs/${runId}/report`),
    enabled: runId !== undefined,
    refetchInterval: (current) =>
      isSearching(current.state.data?.run) || writingBrief(current.state.data?.run.leads)
        ? SCOUT_POLL_MS
        : false,
  });
}

function refreshCommunications(applicationId: string) {
  queryClient.invalidateQueries({ queryKey: ['communications', applicationId] });
  queryClient.invalidateQueries({ queryKey: ['next-steps'] });
  queryClient.invalidateQueries({ queryKey: ['overview'] });
  queryClient.invalidateQueries({ queryKey: ['scout-leads'] });
}

export function useCommunications(applicationId: string | null) {
  return useQuery({
    queryKey: ['communications', applicationId],
    queryFn: () =>
      request<Communication[]>(`/api/applications/${applicationId}/communications`),
    enabled: applicationId !== null,
  });
}

export function useAddCommunication(applicationId: string) {
  return useMutation({
    mutationFn: (payload: CommunicationPayload) =>
      request<Communication>(`/api/applications/${applicationId}/communications`, {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    onSuccess: () => refreshCommunications(applicationId),
  });
}

export function useUpdateCommunication(applicationId: string) {
  return useMutation({
    mutationFn: ({ id, changes }: { id: string; changes: Partial<CommunicationPayload> }) =>
      request<Communication>(`/api/communications/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(changes),
      }),
    onSuccess: () => refreshCommunications(applicationId),
  });
}

export function useDeleteCommunication(applicationId: string) {
  return useMutation({
    mutationFn: (id: string) => request<void>(`/api/communications/${id}`, { method: 'DELETE' }),
    onSuccess: () => refreshCommunications(applicationId),
  });
}

export function useNextSteps() {
  return useQuery({
    queryKey: ['next-steps'],
    queryFn: () => request<NextStep[]>('/api/communications/next-steps'),
  });
}

/** Ticks off a next step from a list that spans applications, such as the Dashboard's. */
export function useFinishNextStep() {
  return useMutation({
    mutationFn: ({ id }: { id: string; applicationId: string }) =>
      request<Communication>(`/api/communications/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ next_step_done: true }),
      }),
    onSuccess: (_result, { applicationId }) => refreshCommunications(applicationId),
  });
}

export function useAdminUsers() {
  return useQuery({
    queryKey: ['admin-users'],
    queryFn: () => request<AdminUser[]>('/api/admin/users'),
  });
}

export function useAdminCatalog() {
  return useQuery({
    queryKey: ['admin-catalog'],
    queryFn: () => request<CatalogStats>('/api/admin/catalog'),
  });
}

export function useUpdateAdminUser() {
  return useMutation({
    mutationFn: ({
      id,
      ...change
    }: {
      id: string;
      role?: Role;
      plan?: Plan;
      override_limits?: boolean;
    }) =>
      request<AdminUser>(`/api/admin/users/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(change),
      }),
    onSuccess: (user) => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] });
      // Your own plan decides what Scout lets you do.
      const me = queryClient.getQueryData<User | null>(['me']);
      if (me && me.id === user.id) {
        queryClient.setQueryData(['me'], { ...me, role: user.role, plan: user.plan });
        // Your plan and limits show on many pages, so everything is read again.
        queryClient.invalidateQueries();
      }
    },
  });
}

export function useAppConfig() {
  return useQuery({
    queryKey: ['admin-config'],
    queryFn: () => request<AppSetting[]>('/api/admin/config'),
  });
}

export function useUpdateAppConfig() {
  return useMutation({
    mutationFn: ({ key, value }: { key: string; value: number }) =>
      request<AppSetting[]>(`/api/admin/config/${key}`, {
        method: 'PUT',
        body: JSON.stringify({ value }),
      }),
    onSuccess: (settings) => {
      queryClient.setQueryData(['admin-config'], settings);
      // The limits show on many pages.
      queryClient.invalidateQueries({ predicate: (query) => query.queryKey[0] !== 'admin-config' });
    },
  });
}

export function useResetAppConfig() {
  return useMutation({
    mutationFn: (key: string) =>
      request<AppSetting[]>(`/api/admin/config/${key}`, { method: 'DELETE' }),
    onSuccess: (settings) => {
      queryClient.setQueryData(['admin-config'], settings);
      queryClient.invalidateQueries({ predicate: (query) => query.queryKey[0] !== 'admin-config' });
    },
  });
}

export function useSpend() {
  return useQuery({ queryKey: ['spend'], queryFn: () => request<Spend>('/api/admin/spend') });
}

export function useAddTopUp() {
  return useMutation({
    mutationFn: (payload: { amount_usd: number; added_on: string; note: string }) =>
      request<TopUp>('/api/admin/spend/topups', { method: 'POST', body: JSON.stringify(payload) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['spend'] }),
  });
}

export function useDeleteTopUp() {
  return useMutation({
    mutationFn: (id: string) => request<void>(`/api/admin/spend/topups/${id}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['spend'] }),
  });
}

const BRIEF_POLL_MS = 3_000;

export function useScoutLeads() {
  return useQuery({
    queryKey: ['scout-leads'],
    queryFn: () => request<ScoutLeadItem[]>('/api/scout/leads'),
    // A brief being written appears on its own when it's done.
    refetchInterval: (query) => (writingBrief(query.state.data) ? BRIEF_POLL_MS : false),
  });
}

/** Queue the brief for a Companies lead. Asking twice charges once. */
export function useRequestBrief() {
  return useMutation({
    mutationFn: ({ runId, leadId }: { runId: string; leadId: string }) =>
      request<ScoutLeadItem>(`/api/scout/runs/${runId}/leads/${leadId}/brief`, {
        method: 'POST',
      }),
    onSuccess: (item) => {
      const leads = queryClient.getQueryData<ScoutLeadItem[]>(['scout-leads']);
      if (leads) {
        queryClient.setQueryData(
          ['scout-leads'],
          leads.map((lead) => (lead.run_id === item.run_id && lead.id === item.id ? item : lead)),
        );
      }
      // A run's report and the Search tab show the same lead from their own data, so they're
      // read again too, and keep checking until the brief is there.
      queryClient.invalidateQueries({ queryKey: ['scout-runs'] });
      queryClient.invalidateQueries({ queryKey: ['scout-report', item.run_id] });
      queryClient.invalidateQueries({ queryKey: ['scout'] });
    },
  });
}

export function useSetLeadState() {
  return useMutation({
    mutationFn: ({
      runId,
      leadId,
      state,
      reason = '',
    }: {
      runId: string;
      leadId: string;
      state: LeadState;
      reason?: string;
    }) =>
      request<ScoutLeadItem>(`/api/scout/runs/${runId}/leads/${leadId}/state`, {
        method: 'POST',
        body: JSON.stringify({ state, reason }),
      }),
    // Dismissing removes a posting and restoring makes one, so everything posting-based reloads.
    onSuccess: (item, { runId, leadId }) => {
      const leads = queryClient.getQueryData<ScoutLeadItem[]>(['scout-leads']);
      const before = leads?.find((lead) => lead.run_id === runId && lead.id === leadId);
      // The lead is shown as it is now, and a posting it no longer has is dropped first, so the
      // refresh doesn't refetch a posting that was just removed and report its 404 as an error.
      if (leads) {
        queryClient.setQueryData(
          ['scout-leads'],
          leads.map((lead) => (lead === before ? item : lead)),
        );
      }
      if (before?.posting_id && before.posting_id !== item.posting_id) {
        queryClient.removeQueries({ queryKey: ['postings', before.posting_id], exact: true });
      }
      // Not refreshData: its 'postings' prefix refetched the saved posting of every lead on the
      // page at once, which tripped the API rate limit. Other postings only go stale, and the
      // list and counts the removed or remade posting is in reload.
      queryClient.invalidateQueries({ queryKey: ['postings'], refetchType: 'none' });
      queryClient.invalidateQueries({ queryKey: ['postings'], exact: true });
      for (const key of [
        'overview',
        'posting-trends',
        'keyword-trends',
        'listings',
        'scout-leads',
      ]) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
      queryClient.invalidateQueries({ queryKey: ['scout-runs'] });
      queryClient.invalidateQueries({ queryKey: ['hidden-listings'] });
    },
  });
}

export function useStartScout() {
  return useMutation({
    mutationFn: (label: string | null) =>
      request<ScoutRun>('/api/scout/runs', {
        method: 'POST',
        body: JSON.stringify({ label }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['scout-runs'] });
      queryClient.invalidateQueries({ queryKey: ['scout'] });
    },
  });
}

export function useTrackLead() {
  return useMutation({
    mutationFn: ({ runId, leadId }: { runId: string; leadId: string }) =>
      request<ScoutRun>(`/api/scout/runs/${runId}/leads/${leadId}/track`, {
        method: 'POST',
      }),
    onSuccess: (run) => {
      queryClient.setQueryData<ScoutRun[]>(['scout-runs'], (runs) =>
        runs?.map((item) => (item.id === run.id ? run : item)),
      );
      queryClient.invalidateQueries({ queryKey: ['scout-report', run.id] });
      refreshData();
    },
  });
}

export function useDeletePosting() {
  return useMutation({
    mutationFn: (id: string) => request<void>(`/api/postings/${id}`, { method: 'DELETE' }),
    onSuccess: (_result, id) => {
      // Removed first, so the refresh doesn't refetch a posting that no longer exists.
      queryClient.removeQueries({ queryKey: ['postings', id], exact: true });
      refreshData();
    },
  });
}

const LISTINGS_POLL_MS = 5_000;

/**
 * The same search fetched again in the background while the server refreshes older results.
 * It never replaces what's on screen by itself; the page decides when to show it.
 */
export function useListingUpdate(search: string, enabled: boolean) {
  return useQuery({
    queryKey: ['listings-update', search],
    queryFn: () => request<ListingSearch>(`/api/listings?${search}`),
    enabled,
    refetchInterval: (query) =>
      query.state.data && !query.state.data.refreshing ? false : LISTINGS_POLL_MS,
  });
}

export function showListingUpdate(search: string, update: ListingSearch) {
  queryClient.setQueryData(['listings', search], update);
}

export function useListings(search: string, enabled: boolean) {
  return useQuery({
    queryKey: ['listings', search],
    queryFn: () => request<ListingSearch>(`/api/listings?${search}`),
    enabled,
    // Keeps the current page on screen while a new filter or page loads.
    placeholderData: keepPreviousData,
    // A slow board keeps fetching on the server, so check back until it has finished.
    refetchInterval: (query) =>
      query.state.data?.sources.some((source) => source.state === 'pending')
        ? LISTINGS_POLL_MS
        : false,
  });
}

function refreshHidden() {
  queryClient.invalidateQueries({ queryKey: ['listings'] });
  queryClient.invalidateQueries({ queryKey: ['hidden-listings'] });
}

export function useHiddenListings() {
  return useQuery({
    queryKey: ['hidden-listings'],
    queryFn: () => request<HiddenListing[]>('/api/listings/hidden'),
  });
}

export function useHideListing() {
  return useMutation({
    mutationFn: (payload: HideListingPayload) =>
      request<HiddenListing>('/api/listings/hidden', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    onSuccess: refreshHidden,
  });
}

/** A plain function as well as a hook, so an Undo in a notification works after its row is gone. */
export async function unhideListing(id: string): Promise<void> {
  await request<void>(`/api/listings/hidden/${id}`, { method: 'DELETE' });
  refreshHidden();
}

export function useUnhideListing() {
  return useMutation({ mutationFn: unhideListing });
}

export function usePostingTrends() {
  return useQuery({
    queryKey: ['posting-trends'],
    queryFn: () => request<PostingTrends>('/api/analytics/postings'),
  });
}

export function useKeywordTrends() {
  return useQuery({
    queryKey: ['keyword-trends'],
    queryFn: () => request<KeywordTrends>('/api/analytics/keywords'),
  });
}

export function useIgnoreTerm() {
  return useMutation({
    mutationFn: (term: string) =>
      request<void>('/api/keywords/ignored', { method: 'POST', body: JSON.stringify({ term }) }),
    onSuccess: refreshData,
  });
}

export function useRestoreTerm() {
  return useMutation({
    mutationFn: (term: string) =>
      request<void>(`/api/keywords/ignored?term=${encodeURIComponent(term)}`, { method: 'DELETE' }),
    onSuccess: refreshData,
  });
}

export function useOverview() {
  return useQuery({
    queryKey: ['overview'],
    queryFn: () => request<Overview>('/api/analytics/overview'),
  });
}

export function useResumeInsights() {
  return useQuery({
    queryKey: ['resume-insights'],
    queryFn: () => request<ResumeInsights>('/api/analytics/resumes'),
  });
}

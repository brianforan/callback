export type ApplicationStatus =
  | 'applied'
  | 'screening'
  | 'interview'
  | 'offer'
  | 'rejected'
  | 'ghosted'
  | 'hiring_paused'
  | 'withdrawn';

export type Role = 'member' | 'admin';

export interface User {
  id: string;
  email: string;
  role: Role;
  plan: Plan;
  /** Sees the Admin panel. */
  is_admin: boolean;
}

/** One user as the Admin panel lists them. */
export interface AdminUser extends User {
  /** No daily caps on model calls for this user. */
  override_limits: boolean;
  created_at: string;
}

/** One setting in Admin -> Settings that tunes what Callback spends on the API. */
export interface AppSetting {
  key: string;
  label: string;
  help: string;
  unit: string;
  kind: 'int' | 'float';
  minimum: number;
  maximum: number;
  default: number;
  value: number;
  /** False when it's at its default. */
  saved: boolean;
  updated_at: string | null;
  updated_by: string | null;
}

/** One place the Companies catalog's listings come from. */
export interface CatalogSourceStat {
  source: string;
  name: string;
  listings_recorded: number;
  kept: number;
  last_kept_at: string | null;
  companies: number;
}

export interface CatalogStats {
  companies: number;
  agencies: number;
  jobs: number;
  sources: CatalogSourceStat[];
}

/** One side of a job search, such as engineering or accounting, with its own resumes. */
export interface Profile {
  id: string;
  name: string;
  /** Whether the built-in software skills are part of this profile's. */
  software_skills: boolean;
  created_at: string;
}

/** A skill the user added to a profile, with other ways postings write it. */
export interface SkillTerm {
  id: string;
  name: string;
  aliases: string[];
  /** How many saved postings name it. */
  postings: number;
}

export interface Skills {
  profile_id: string;
  software_skills: boolean;
  software_count: number;
  terms: SkillTerm[];
  max_terms: number;
}

export interface SkillTermPayload {
  name: string;
  aliases: string[];
}

export interface Profiles {
  profiles: Profile[];
  /** The profile this browser is using. */
  active_id: string;
}

export interface VariantRules {
  role_families: string[];
  levels: PostingLevel[];
  focus_skills: string[];
  deal_breakers: string[];
  allow_clearance: boolean;
  max_years: number | null;
  min_score: number;
}

export interface Resume {
  id: string;
  profile_id: string;
  label: string;
  filename: string;
  word_count: number;
  created_at: string;
  application_count: number;
  is_variant: boolean;
  variant_rules: VariantRules;
  // 'pdf' for an uploaded PDF: analysed from its text, but not edited or tailored.
  source_format: 'tex' | 'pdf';
  // Set once the worker has compiled it. pdf_error when it didn't compile.
  pdf_pages: number | null;
  pdf_error: string | null;
}

export interface ResumeDetail extends Resume {
  source_tex: string;
}

export interface StatusEvent {
  status: ApplicationStatus;
  occurred_at: string;
}

export interface Application {
  from_scout: boolean;
  id: string;
  company: string;
  role_title: string;
  job_url: string | null;
  location: string | null;
  source: string | null;
  status: ApplicationStatus;
  applied_on: string;
  notes: string | null;
  resume_id: string | null;
  resume_label: string | null;
  recommended_resume_id: string | null;
  recommended_resume_label: string | null;
  posting_id: string | null;
  /** The profile it was logged under. Null once that profile is deleted. */
  profile_id: string | null;
  created_at: string;
  updated_at: string;
  events: StatusEvent[];
}

export interface ApplicationPayload {
  company: string;
  role_title: string;
  job_url: string | null;
  location: string | null;
  source: string | null;
  status: ApplicationStatus;
  applied_on: string;
  resume_id: string | null;
  posting_id?: string | null;
  notes: string | null;
  posting_text?: string | null;
  referrer?: string | null;
}

export interface Totals {
  applications: number;
  active: number;
  this_week: number;
  callbacks: number;
  callback_rate: number;
  interviews: number;
  offers: number;
  median_days_to_callback: number | null;
}

export interface FunnelStage {
  stage: string;
  count: number;
}

export interface WeeklyPoint {
  week_start: string;
  applications: number;
  callbacks: number;
}

export interface SourcePerformance {
  source: string;
  applications: number;
  callbacks: number;
  callback_rate: number;
}

export interface ResumePerformance {
  resume_id: string;
  label: string;
  uploaded_at: string;
  applications: number;
  callbacks: number;
  interviews: number;
  callback_rate: number;
}

export interface StaleApplication {
  id: string;
  company: string;
  role_title: string;
  applied_on: string;
  days_waiting: number;
  last_contact_on: string | null;
}

export interface Overview {
  totals: Totals;
  funnel: FunnelStage[];
  weekly: WeeklyPoint[];
  sources: SourcePerformance[];
  resumes: ResumePerformance[];
  stale: StaleApplication[];
}

export type Verdict = 'met' | 'weak' | 'missing' | 'unknown';
export type EvidenceLevel = 'experience' | 'project' | 'listed' | 'none';

export interface SkillDemand {
  name: string;
  category: string;
  required_in: number;
  postings: number;
  evidence: EvidenceLevel;
  months: number;
  verdict: Verdict;
}

export interface ResumeEvidence {
  resume_id: string;
  label: string;
  uploaded_at: string;
  sections: string[];
  roles: number;
  years: number | null;
  degree: string | null;
  backed: string[];
  listed_only: string[];
}

export interface VariantFit {
  posting_id: string;
  company: string;
  title: string;
  score: number;
  passes: boolean;
  reasons: string[];
  best: boolean;
  knocked_out: boolean;
  missing_required: string[];
  weak_required: string[];
}

export interface VariantSummary {
  resume_id: string;
  label: string;
  rules: VariantRules;
  evidence: ResumeEvidence;
  passes: number;
  best_for: number;
  fits: VariantFit[];
}

export interface VariantReport {
  postings: number;
  variants: VariantSummary[];
  overlaps: { first: string; second: string; postings: number }[];
  uncovered: {
    posting_id: string;
    company: string;
    title: string;
    closest_variant: string | null;
    closest_score: number | null;
    reasons: string[];
  }[];
}

export interface ResumeInsights {
  latest_resume: string | null;
  postings: number;
  demand: SkillDemand[];
  resumes: ResumeEvidence[];
}

export type PostingLevel =
  | 'intern'
  | 'junior'
  | 'mid'
  | 'senior'
  | 'staff'
  | 'principal'
  | 'manager';

export interface PostingTechnology {
  name: string;
  category: string;
  requirement: 'required' | 'nice_to_have' | 'mentioned';
}

export interface PostingApplication {
  id: string;
  status: ApplicationStatus;
  applied_on: string;
}

export interface StackCoverage {
  resume_label: string;
  required_total: number;
  required_matched: number;
  missing_required: string[];
  nice_total: number;
  nice_matched: number;
  missing_nice: string[];
  coverage: number | null;
}

export interface Posting {
  from_scout: boolean;
  id: string;
  company: string;
  title: string;
  url: string | null;
  location: string | null;
  source: string | null;
  source_kind: string | null;
  referred: boolean;
  referrer: string | null;
  level: PostingLevel | null;
  role_family: string | null;
  years_required: number | null;
  salary_min: number | null;
  salary_max: number | null;
  work_mode: string | null;
  employment_type: string | null;
  corrected_fields: string[];
  seen_on: string;
  updated_at: string;
  technologies: PostingTechnology[];
  applications: PostingApplication[];
  coverage: StackCoverage | null;
  /** Its skills were read before the skill terms last changed, and are being read again. */
  skills_updating: boolean;
  placeholder: boolean;
  staged_at: string | null;
  staged_resume_id: string | null;
  staged_resume_label: string | null;
  staged_notes: string | null;
  /** The profile it was saved under. Null once that profile is deleted. */
  profile_id: string | null;
  /** Reading the text from the link, for a posting saved without any. Null when it wasn't. */
  text_status: PostingTextStatus | null;
  /** Why the read failed, in plain words. */
  text_error: string | null;
}

export type PostingTextStatus = 'pending' | 'fetched' | 'failed';

export interface RelatedPosting {
  id: string;
  company: string;
  title: string;
  level: PostingLevel | null;
  shared_stack: string[];
  stack_overlap: number;
  likely_repeat: boolean;
}

export interface MatchPart {
  label: string;
  score: number;
  weight: number;
  detail: string;
}

export interface RequirementCheck {
  kind: 'skill' | 'years' | 'degree' | 'clearance' | 'citizenship';
  name: string;
  requirement: 'required' | 'nice_to_have' | 'mentioned' | 'knockout';
  verdict: Verdict;
  detail: string;
}

export interface SimilarApplication {
  company: string;
  title: string;
  status: ApplicationStatus;
  similarity: number;
}

export interface MatchEstimate {
  score: number;
  confidence: 'low' | 'medium' | 'high';
  applications: number;
  resumes: number;
  parts: MatchPart[];
  checklist: RequirementCheck[];
  knocked_out: boolean;
  resume_years: number | null;
  matched_required: string[];
  weak_required: string[];
  missing_required: string[];
  missing_nice: string[];
  faded_required: string[];
  latest_resume: string;
  best_resume: {
    id: string;
    label: string;
    required_matched: number;
    required_weak: number;
    required_total: number;
    nice_matched: number;
    nice_total: number;
  };
  callback: {
    rate: number;
    similar_applications: number;
    similar_callbacks: number;
    applications: number;
    callbacks: number;
    closest: SimilarApplication[];
  } | null;
}

export interface BaselinePick {
  resume_id: string;
  label: string;
  score: number;
  covered: boolean;
  reasons: string[];
  mix: { name: string; baseline: string }[];
}

export interface PostingDetail extends Posting {
  raw_text: string;
  related: RelatedPosting[];
  match: MatchEstimate | null;
  baseline: BaselinePick | null;
  staged_match: MatchEstimate | null;
  tailor_advice: TailorAdvice | null;
}

export interface TailorGap {
  name: string;
  requirement: 'required' | 'nice_to_have';
  verdict: 'met' | 'weak' | 'missing';
  source: 'fact_sheet' | 'baseline';
  baseline: string | null;
}

/** Whether tailoring could improve on the suggested baseline, by plain rules. */
export interface TailorAdvice {
  ready: boolean;
  missing: string | null;
  gaps: TailorGap[];
  unfixable: string[];
}

export interface FactSheet {
  /** The profile it belongs to, so an editor saves its draft back there. */
  profile_id: string;
  content: string;
  updated_at: string | null;
}

export interface PostingPayload {
  company: string;
  title: string;
  url: string | null;
  location: string | null;
  source: string | null;
  raw_text: string | null;
  referrer: string | null;
  seen_on: string;
}

export interface PostingChanges {
  company: string;
  title: string;
  url: string | null;
  location: string | null;
  source: string | null;
  seen_on: string;
  level: string | null;
  role_family: string | null;
  years_required: number | null;
  salary_min: number | null;
  salary_max: number | null;
  work_mode: string | null;
  employment_type: string | null;
  source_kind: string | null;
  referred: boolean;
  referrer: string | null;
  technologies: string[];
}

export interface Listing {
  from_scout: boolean;
  id: string;
  source: string;
  company: string;
  title: string;
  // Only a saved posting entered by hand can lack one.
  url: string | null;
  location: string | null;
  description: string;
  published_on: string | null;
  level: PostingLevel | null;
  role_family: string | null;
  years_required: number | null;
  salary_min: number | null;
  salary_max: number | null;
  work_mode: string | null;
  employment_type: string | null;
  technologies: PostingTechnology[];
  posting_id: string | null;
  application_status: ApplicationStatus | null;
  match: MatchEstimate | null;
  hidden: { id: string; kind: HiddenKind } | null;
  baseline: BaselinePick | null;
  staged: boolean;
  staged_resume_id: string | null;
  tailor_advice: TailorAdvice | null;
}

export type HiddenKind = 'listing' | 'company';

export interface HiddenListing {
  id: string;
  kind: HiddenKind;
  company: string;
  title: string | null;
  url: string | null;
  created_at: string;
}

export interface HideListingPayload {
  kind: HiddenKind;
  company: string;
  title?: string;
  url?: string;
}

export interface ListingSource {
  id: string;
  name: string;
  site: string;
  state: 'ok' | 'failed' | 'needs_setup' | 'pending';
  listings: number;
  fetched_at: string | null;
  message: string | null;
  missing_settings: string[];
  setup_url: string | null;
  // Older results are shown while newer ones are fetched.
  refreshing: boolean;
}

export type ListingSort = 'match' | 'newest' | 'salary' | 'relevance';

export interface ListingSearch {
  listings: Listing[];
  total: number;
  unfiltered_total: number;
  page: number;
  page_size: number;
  sort: ListingSort;
  technologies: LabelCount[];
  has_profile: boolean;
  sources: ListingSource[];
  hidden_total: number;
  // How many results each tracking state would show on its own.
  tracking_counts?: Partial<Record<ListingTracking, number>>;
  // Some boards' results are older ones shown while newer are fetched.
  refreshing: boolean;
}

export type ListingTracking = 'untracked' | 'tracked' | 'staged' | ApplicationStatus;

export interface LabelCount {
  label: string;
  count: number;
}

export interface PostingWeek {
  week_start: string;
  postings: number;
  referred: number;
  levels: Record<string, number>;
  median_years: number | null;
  median_salary: number | null;
  median_coverage: number | null;
}

export interface ShareWeek {
  week_start: string;
  shares: Record<string, number>;
}

export interface StackRow {
  name: string;
  category: string;
  postings: number;
  share: number;
  required: number;
  first_seen: string;
  last_seen: string;
}

export interface StackGap {
  name: string;
  category: string;
  required_in: number;
  missing_in: number;
}

export interface CompanyRow {
  company: string;
  postings: number;
  applications: number;
  last_applied: string | null;
}

export interface PostingTrends {
  total_postings: number;
  applied_postings: number;
  levels: LabelCount[];
  role_families: LabelCount[];
  source_kinds: LabelCount[];
  referrals: SourcePerformance[];
  weekly: PostingWeek[];
  stack: StackRow[];
  stack_weekly: ShareWeek[];
  companies: CompanyRow[];
  scored_postings: number;
  median_coverage: number | null;
  gaps: StackGap[];
  coverage_callbacks: SourcePerformance[];
  recommendation_callbacks: SourcePerformance[];
}

export interface KeywordRow {
  term: string;
  applications: number;
  share: number;
  mentions: number;
  first_used: string;
  last_used: string;
  early_share: number | null;
  recent_share: number | null;
}

export interface KeywordTrends {
  applications: number;
  min_applications: number;
  most_used: KeywordRow[];
  faded: KeywordRow[];
  rising: KeywordRow[];
  chart_terms: string[];
  weekly: ShareWeek[];
  ignored: string[];
}

export type TailorJobStatus = 'queued' | 'running' | 'done' | 'failed';

export interface TailorJob {
  id: string;
  /** Null once the posting is deleted. */
  posting_id: string | null;
  status: TailorJobStatus;
  attempts: number;
  baseline_id: string | null;
  baseline_label: string | null;
  result_resume_id: string | null;
  result_resume_label: string | null;
  has_pdf: boolean;
  reasons: string[];
  changelog: string[];
  gaps: string[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface TailorStatus {
  job: TailorJob | null;
  used_today: number;
  /** Null when the admin lifted this user's daily caps. */
  daily_limit: number | null;
}

export type Plan = 'free' | 'premium';
export type FitMode = 'wide' | 'balanced' | 'close';
/** Scout has one engine now, stored as 'companies'. Older runs may name the others. */
export type Engine = 'agent' | 'pipeline' | 'companies';
export type AgencyMode = 'include' | 'find_employer' | 'skip';
export type Relocation = 'none' | 'metro' | 'anywhere';
export type WorkMode = 'remote' | 'hybrid' | 'onsite';

/** One engine's range and wording for a limit. */
export interface ScoutLimitRange {
  label: string;
  help: string;
  minimum: number;
  maximum: number;
  default: number;
}

export interface ScoutLimit {
  key: string;
  label: string;
  help: string;
  unit: string;
  minimum: number;
  maximum: number;
  default: number;
  by_engine?: Partial<Record<Engine, ScoutLimitRange>>;
}

export interface ScoutSettingsPayload {
  description: string;
  /** Titles to search under: phrases the profile typed, each matched exactly. */
  title_families: string[];
  work_modes: WorkMode[];
  location: string;
  posted_within_days: number | null;
  fit_mode: FitMode;
  /** Accepted by the server and ignored: every run uses the one engine. */
  engine: Engine;
  agency_mode: AgencyMode;
  relocation: Relocation;
  /** Technologies a job must use, any one of them. Empty for no stack rule. */
  must_have: string[];
  /** Technologies that rule a job out when it requires them. */
  rule_out: string[];
  /** Levels wanted. Empty for any level except intern and manager. */
  levels: PostingLevel[];
  /** Where a run stops, by limit key, within each limit's range. */
  limits: Record<string, number>;
}

/** The work Scout read from the profile's baselines, fact sheet and description. */
export interface ScoutWorkBoundary {
  work: string;
  /** Searched under when no titles are picked and there's no Must use list. */
  titles: string[];
  /** The person rewrote the work line, so runs keep it. */
  edited: boolean;
  /** Edited, and what it was read from changed since. */
  changed: boolean;
}

export interface ScoutSettings extends ScoutSettingsPayload {
  updated_at: string | null;
  /** Null until a run reads it. */
  work_boundary: ScoutWorkBoundary | null;
}

/** What a user's finished runs of one engine cost. */
export interface ScoutEngineHistory {
  runs: number;
  leads: number;
  /** Pages read, or for Companies postings checked. */
  pages: number;
  cost_usd: number;
}

export interface Scout {
  plan: Plan;
  history?: Partial<Record<Engine, ScoutEngineHistory>>;
  /** What the user may still start today. A daily cap is null when the admin lifted it. */
  allowance: {
    runs_today: number;
    daily_runs: number | null;
    briefs_today: number;
    daily_briefs: number | null;
    /** The most one run may spend: a safety stop, not an estimate. */
    run_max_usd: number;
    resets_at: string;
  };
  limits: ScoutLimit[];
  posted_within_options: number[];
  description_max_length: number;
  /** The posting parser's technology names, offered for Must use and Rule out. */
  technology_names: string[];
  settings: ScoutSettings;
}

export interface ScoutLead {
  id: string;
  company: string;
  title: string;
  url: string;
  location: string;
  work_mode: string;
  posted: string;
  fit_summary: string;
  what_they_want: string[];
  you_match: string[];
  you_lack: string[];
  requirements: string[];
  company_notes: string;
  fit: string;
  agency: string;
  /** The agency's listing, when the agent found the employer's own posting and kept that. */
  agency_url: string;
  company_website: string;
  company_linkedin: string;
  salary: string;
  level: string;
  employment_type: string;
  offer_summary: string;
  how_to_apply: string[];
  application_tips: string[];
  sources: string[];
  posting_id: string | null;
  state: string;
  decline_reason: string;
  /** Companies leads get their brief on request. Empty when the run wrote it. */
  brief_status: '' | 'none' | 'queued' | 'running' | 'done' | 'failed';
  brief_error: string;
}

export type ScoutStepKind =
  | 'note'
  | 'search'
  | 'results'
  | 'fetch'
  | 'lead'
  | 'skipped'
  | 'error'
  | 'stop';

export interface ScoutStep {
  kind: ScoutStepKind;
  detail: string;
  request: number;
}

export type ScoutRunStatus = 'queued' | 'running' | 'done' | 'failed';

export interface ScoutRun {
  id: string;
  status: ScoutRunStatus;
  /** Given when the run was started. Null for runs started without one. */
  label: string | null;
  description: string;
  engine: Engine;
  target_leads: number | null;
  /** The most the run could spend, or null for runs from before the dollar ceiling. */
  budget_usd: number | null;
  cost_usd: number;
  searches: number;
  pages: number;
  stop_code: string | null;
  stop_message: string | null;
  leads: ScoutLead[];
  steps: ScoutStep[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface ScoutReport {
  run: ScoutRun;
  minutes_used: number | null;
  requests: number;
  nudges: number;
  limits: {
    key: string;
    label: string;
    used: number;
    limit: number;
    unit: string;
    stopped_run: boolean;
  }[];
  cost_usd: number;
  cost_per_lead_usd: number | null;
  costs: { key: string; label: string; usd: number; share: number }[];
  queries: {
    request: number;
    query: string;
    results: number | null;
    new: number | null;
    repeated: boolean;
  }[];
  pages: { request: number; url: string; host: string; became_lead: boolean }[];
  refused: string[];
  errors: { name: string; count: number }[];
  lead_hosts: { name: string; count: number }[];
  findings: { level: 'good' | 'info' | 'warning'; title: string; detail: string }[];
}

export type LeadState = 'new' | 'later' | 'dismissed' | 'expired';

export interface ScoutLeadItem extends ScoutLead {
  run_id: string;
  found_at: string;
  /** The label of the run the lead is listed under. */
  label: string | null;
  state: LeadState;
  decline_reason: string;
  application: {
    id: string;
    status: ApplicationStatus;
    applied_on: string;
    resume_label: string | null;
    communications: number;
    last_contact_on: string | null;
    next_step: string;
    next_step_due: string | null;
  } | null;
}

export type CommunicationKind = 'email' | 'call' | 'interview' | 'message' | 'note';
export type Direction = 'inbound' | 'outbound' | 'none';

export interface CommunicationPayload {
  kind: CommunicationKind;
  direction: Direction;
  occurred_on: string;
  contact: string;
  summary: string;
  next_step: string;
  next_step_due: string | null;
  next_step_done: boolean;
}

export interface Communication extends CommunicationPayload {
  id: string;
  application_id: string;
  created_at: string;
  updated_at: string;
}

export interface NextStep {
  communication_id: string;
  application_id: string;
  company: string;
  role_title: string;
  next_step: string;
  next_step_due: string | null;
  overdue: boolean;
}

export interface SpendTokens {
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  web_search_requests: number;
}

export interface SpendJob {
  id: string;
  kind: 'scout' | 'tailoring' | 'rejection_analysis' | 'company_research';
  email: string;
  created_at: string;
  status: string;
  stop: string | null;
  leads: number;
  measured_usd: number;
  measured: SpendTokens;
  billed:
    | (SpendTokens & {
        window_start: string;
        window_end: string;
        shared_with: number;
        checked_at: string;
      })
    | null;
  requests: (SpendTokens & {
    request: number;
    stage: string;
    model: string;
    web_fetch_requests: number;
    seconds: number;
    cost_usd: number;
  })[];
}

export interface TopUp {
  id: string;
  amount_usd: number;
  added_on: string;
  note: string;
}

export interface Spend {
  setup: {
    usage_synced_at: string | null;
    cost_synced_at: string | null;
    last_error: string;
    api_key_filter: boolean;
    workspace_filter: boolean;
  };
  balance: {
    topups_usd: number;
    billed_since_usd: number;
    balance_usd: number;
    since: string | null;
    topups: TopUp[];
  };
  quote: {
    runs_measured: number;
    median_run_usd: number | null;
    mean_run_usd: number | null;
    p90_run_usd: number | null;
    max_run_usd: number | null;
    median_per_lead_usd: number | null;
    median_tailoring_usd: number | null;
    budgets: { budget_usd: number; runs: number; leads: number; tailorings: number }[];
  };
  days: { day: string; measured_usd: number; billed_usd: number | null }[];
  users: {
    email: string;
    runs: number;
    tailorings: number;
    /** Rejection reports and company research snapshots their rejections started. */
    analyses: number;
    research: number;
    leads: number;
    measured_usd: number;
    median_run_usd: number | null;
    per_lead_usd: number | null;
  }[];
  jobs: SpendJob[];
}

/** What to do about a named requirement the resume sent didn't fully meet. */
export type GapAction = 'add_to_resume' | 'show_in_a_role' | 'borrow' | 'limited' | 'learn';
export type RejectStage = 'applied' | 'screening' | 'interview' | 'offer';

export interface TechnologyGap {
  name: string;
  requirement: 'required' | 'nice_to_have' | 'mentioned';
  verdict: Verdict;
  detail: string;
  action: GapAction;
  note: string;
}

export interface ExperienceGap {
  area: string;
  need: string;
  evidence: string;
  suggestion: string;
}

export interface FocusArea {
  kind: 'technology' | 'experience';
  title: string;
  why: string;
  action: string;
}

export interface RejectAnalysis {
  id: string;
  application_id: string;
  status: TailorJobStatus;
  stage: RejectStage | null;
  score: number | null;
  resume_id: string | null;
  resume_label: string | null;
  checklist: RequirementCheck[];
  technology_gaps: TechnologyGap[];
  summary: string;
  strengths: string[];
  experience_gaps: ExperienceGap[];
  focus_areas: FocusArea[];
  /** The company's typical engineer beside the resume. Empty before company research. */
  comparison: ComparisonLine[];
  /** A rough estimate; null for reports from before estimates. */
  estimate: Estimate | null;
  /** Why the written part is missing, when it is. */
  note: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export type EstimateBand = 'likely' | 'possibly' | 'unlikely' | 'cant_tell';

export interface Estimate {
  band: EstimateBand;
  reasons: string[];
}

export interface ComparisonLine {
  measure: 'years' | 'seniority' | 'industries' | 'employer_kinds';
  typical: string;
  yours: string;
  note: string;
}

/** One line about the company, with its source: a research page, or Callback's catalog. */
export interface ContextLine {
  label: string;
  text: string;
  source_url: string | null;
  source_title: string | null;
  as_of: string | null;
}

export interface CompanyContext {
  company: string | null;
  researched_at: string | null;
  lines: ContextLine[];
}

export interface RepeatCompany {
  company: string;
  rejections: number;
  what_it_does: string | null;
  size: string | null;
  recent_events: string[];
  researched_at: string | null;
}

export interface RejectAnalysisStatus {
  analysis: RejectAnalysis | null;
  /** This company now, for a rejected application; worked out when the report is opened. */
  company: CompanyContext | null;
  /** The company is being researched; a waiting report runs once that's done. */
  researching: boolean;
  used_today: number;
  /** Null when the admin lifted this user's daily caps. */
  daily_limit: number | null;
  /** Why it can't be run again now, or null when it can. */
  rerun_blocked: string | null;
}

export interface RejectedTechnology {
  name: string;
  required: number;
  nice_to_have: number;
  mentioned: number;
  total: number;
  missing: number;
  weak: number;
  action: GapAction;
  note: string;
}

export interface RejectedExperience {
  area: string;
  count: number;
  examples: { application_id: string; company: string; role_title: string; need: string }[];
}

export interface RejectionRow {
  application_id: string;
  company: string;
  role_title: string;
  applied_on: string;
  status: TailorJobStatus;
  stage: RejectStage | null;
  score: number | null;
  top_focus: string | null;
  finished_at: string | null;
}

export interface RejectionSummary {
  rejections: number;
  analyzed: number;
  waiting: number;
  not_analyzed: number;
  stages: Record<RejectStage, number>;
  average_score: number | null;
  technologies: RejectedTechnology[];
  experience: RejectedExperience[];
  recent: RejectionRow[];
  used_today: number;
  /** Null when the admin lifted this user's daily caps. */
  daily_limit: number | null;
  /** Finished reports in each rough-estimate band. */
  estimates: Partial<Record<EstimateBand, number>>;
  repeat_companies: RepeatCompany[];
}

/** Where a research fact came from. Facts cite these by index. */
export interface ResearchSource {
  url: string;
  title: string;
  published_on: string;
}

export type ResearchConfidence = 'stated' | 'estimated' | 'unknown';

/** One research snapshot of a company, after Callback's checks. 0 and '' mean unknown. */
export interface CompanyResearch {
  id: string;
  status: 'queued' | 'running' | 'done' | 'failed';
  note: string | null;
  sources: ResearchSource[];
  profile: {
    what_it_does?: string;
    industry?: string;
    hq_city?: string;
    hq_country?: string;
    founded_year?: number;
    website_domain?: string;
    source?: number;
  };
  ownership: { kind?: string; ticker?: string; exchange?: string; parent?: string; source?: number };
  funding: {
    stage?: string;
    total_raised_usd?: number;
    source?: number;
    rounds?: { date: string; round: string; amount_usd: number; source: number }[];
  };
  size: { headcount?: number; as_of?: string; trend?: string; source?: number };
  events: {
    date: string;
    kind: string;
    detail: string;
    count: number;
    percent: number;
    source: number;
  }[];
  engineering: {
    stack?: { name: string; evidence: string; source: number }[];
    eng_blog?: boolean;
    open_source?: boolean;
    team_size?: number;
    source?: number;
  };
  workforce: {
    typical_years_low?: number;
    typical_years_high?: number;
    seniority_mix?: string;
    prior_industries?: string[];
    prior_employer_kinds?: string[];
    cs_degree_percent?: number;
    team_locations?: string[];
    remote_policy?: string;
    source?: number;
  };
  interviewing: {
    stages?: string[];
    take_home?: string;
    typical_weeks?: number;
    weighs?: string[];
    source?: number;
  };
  confidence: Record<string, ResearchConfidence>;
  searches: number;
  created_at: string;
  finished_at: string | null;
}

/** An application's company research, as the drawer shows it. */
export interface CompanyResearchView {
  company: string | null;
  /** For a link to the company's page; null with company. */
  company_id: string | null;
  agency: boolean;
  latest: CompanyResearch | null;
  active: 'queued' | 'running' | null;
  failure: string | null;
  used_today: number;
  /** Null when the admin lifted this user's daily caps. */
  daily_limit: number | null;
  /** Why research can't start now, or null when it can. */
  blocked: string | null;
}

/** A researched company the user applied to, in brief. */
export interface CompanyCard {
  id: string;
  name: string;
  domain: string | null;
  researched_at: string | null;
  snapshots: number;
  ownership: string | null;
  stage: string | null;
  headcount: number | null;
  trend: string | null;
  recent_events: string[];
  can_see_hiring: boolean;
  open_roles: number | null;
  open_engineering: number | null;
}

export interface HiringWeek {
  week_start: string;
  open: number;
  open_engineering: number;
  opened: number;
  closed: number;
}

/** One company's page: every snapshot, newest first, and its weekly hiring. */
export interface CompanyDetail extends CompanyCard {
  history: CompanyResearch[];
  weeks: HiringWeek[];
  median_days_open: number | null;
  reposts_last_year: number;
}

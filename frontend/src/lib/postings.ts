import type { PostingLevel } from './types';

function humanize(value: string): string {
  const spaced = value.replaceAll('_', ' ');
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function options(values: string[]) {
  return values.map((value) => ({ value, label: humanize(value) }));
}

// Ordered from most junior to most senior, which is also the stacking order in the level chart.
export const LEVELS: PostingLevel[] = [
  'intern',
  'junior',
  'mid',
  'senior',
  'staff',
  'principal',
  'manager',
];

export const LEVEL_OPTIONS = options(LEVELS);

// Cooler to warmer as seniority rises, so a table column of levels reads at a glance.
const LEVEL_COLORS: Record<PostingLevel, string> = {
  intern: 'gray',
  junior: 'cyan',
  mid: 'blue',
  senior: 'indigo',
  staff: 'violet',
  principal: 'grape',
  manager: 'pink',
};

export function levelColor(level: string | null | undefined): string {
  return LEVEL_COLORS[level as PostingLevel] ?? 'gray';
}

// Short enough for a narrow table column. Manager is a separate track, not a rung above principal.
const LEVEL_CODES: Record<PostingLevel, string> = {
  intern: 'L0',
  junior: 'L1',
  mid: 'L2',
  senior: 'L3',
  staff: 'L4',
  principal: 'L5',
  manager: 'M',
};

export function levelCode(level: PostingLevel): string {
  return LEVEL_CODES[level];
}
export const ROLE_FAMILY_OPTIONS = options([
  'full_stack',
  'frontend',
  'backend',
  'mobile',
  'devops',
  'data',
  'machine_learning',
  'security',
  'qa',
  'management',
  'general_software',
  'other',
]);
export const WORK_MODE_OPTIONS = options(['remote', 'hybrid', 'onsite']);
export const EMPLOYMENT_TYPE_OPTIONS = options([
  'full_time',
  'part_time',
  'contract',
  'internship',
]);
export const SOURCE_KIND_OPTIONS = options(['job_board', 'company_site', 'referral', 'recruiter']);

export function fieldLabel(value: string | null | undefined): string {
  return value ? humanize(value) : '–';
}

export function hasSalary(low: number | null, high: number | null): boolean {
  return low !== null || high !== null;
}

export function formatSalary(low: number | null, high: number | null): string {
  if (low === null && high === null) return '–';
  const money = (value: number) => `$${Math.round(value / 1000)}k`;
  if (low === null || high === null || low === high) return money((low ?? high) as number);
  return `${money(low)}–${money(high)}`;
}

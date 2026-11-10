import type { ApplicationStatus } from './types';

export const STATUSES: { value: ApplicationStatus; label: string; color: string }[] = [
  { value: 'applied', label: 'Applied', color: 'blue' },
  { value: 'screening', label: 'Screening', color: 'cyan' },
  { value: 'interview', label: 'Interview', color: 'violet' },
  { value: 'offer', label: 'Offer', color: 'green' },
  { value: 'rejected', label: 'Rejected', color: 'red' },
  { value: 'ghosted', label: 'Ghosted', color: 'gray' },
  { value: 'hiring_paused', label: 'Hiring paused', color: 'yellow' },
  { value: 'withdrawn', label: 'Withdrawn', color: 'orange' },
];

export const STATUS_OPTIONS = STATUSES.map(({ value, label }) => ({ value, label }));

export function statusMeta(status: string) {
  return STATUSES.find((item) => item.value === status) ?? STATUSES[0];
}

export const SOURCE_SUGGESTIONS = [
  'LinkedIn',
  'Company site',
  'Referral',
  'Recruiter',
  'Indeed',
  'Wellfound',
  'Hacker News',
];

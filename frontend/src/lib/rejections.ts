import type { ComparisonLine, EstimateBand, GapAction, RejectStage } from './types';

/** How to close a technology gap, in a few words, with a badge color. */
export const GAP_ACTIONS: Record<GapAction, { label: string; color: string }> = {
  add_to_resume: { label: 'Add to resume', color: 'green' },
  show_in_a_role: { label: 'Show in a role', color: 'teal' },
  borrow: { label: 'From another resume', color: 'blue' },
  limited: { label: 'Limited claim', color: 'yellow' },
  learn: { label: 'Learn it', color: 'red' },
};

/** The rough estimate of how much a rejection may have been about the person's profile. */
export const ESTIMATE_BANDS: Record<EstimateBand, { label: string; color: string }> = {
  likely: { label: 'Likely about your profile', color: 'orange' },
  possibly: { label: 'Possibly about your profile', color: 'yellow' },
  unlikely: { label: 'Unlikely to be about your profile', color: 'teal' },
  cant_tell: { label: "Can't tell", color: 'gray' },
};

export const ESTIMATE_ORDER: EstimateBand[] = ['likely', 'possibly', 'unlikely', 'cant_tell'];

export const MEASURE_LABELS: Record<ComparisonLine['measure'], string> = {
  years: 'Years of experience',
  seniority: 'Seniority',
  industries: 'Industries',
  employer_kinds: 'Kinds of employer',
};

export const REQUIREMENT_SHORT: Record<string, string> = {
  required: 'Required',
  nice_to_have: 'Nice to have',
  mentioned: 'Mentioned',
};

/** Where the application got to before the rejection, as a phrase. */
export const STAGE_LABELS: Record<RejectStage, string> = {
  applied: 'Rejected at the resume screen',
  screening: 'Rejected after a recruiter screen',
  interview: 'Rejected after interviewing',
  offer: 'Rejected at the offer stage',
};

export const STAGE_SHORT: Record<RejectStage, string> = {
  applied: 'Resume screen',
  screening: 'After screen',
  interview: 'After interview',
  offer: 'Offer stage',
};

const AREA_LABELS: Record<string, string> = {
  domain: 'Domain knowledge',
  scale_and_performance: 'Scale and performance',
  system_design: 'System design',
  leadership_and_mentoring: 'Leadership and mentoring',
  ownership_and_impact: 'Ownership and impact',
  cloud_and_infrastructure: 'Cloud and infrastructure',
  data: 'Data',
  security_and_compliance: 'Security and compliance',
  testing_and_quality: 'Testing and quality',
  product_and_customers: 'Product and customers',
  collaboration_and_communication: 'Collaboration and communication',
  other: 'Other experience',
};

export function areaLabel(area: string): string {
  return AREA_LABELS[area] ?? area.replace(/_/g, ' ');
}

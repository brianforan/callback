import { fieldLabel } from './postings';
import type { VariantRules } from './types';

// Matches MAX_BASELINES in backend/app/routers/resumes.py.
export const MAX_BASELINES = 3;

export interface RuleRow {
  label: string;
  value: string;
}

// The same rules as rulesSummary, one labelled row each, for the baseline cards.
export function ruleRows(rules: VariantRules): RuleRow[] {
  return [
    {
      label: 'Roles',
      value: rules.role_families.length ? rules.role_families.map(fieldLabel).join(', ') : 'Any',
    },
    {
      label: 'Levels',
      value: rules.levels.length ? rules.levels.map(fieldLabel).join(', ') : 'Any',
    },
    {
      label: 'Must name',
      value: rules.focus_skills.length ? `Any of ${rules.focus_skills.join(', ')}` : 'Nothing set',
    },
    {
      label: 'Skips',
      value: rules.deal_breakers.length ? rules.deal_breakers.join(', ') : 'Nothing set',
    },
    {
      label: 'Years asked',
      value: rules.max_years !== null ? `At most ${rules.max_years}` : 'No limit',
    },
    { label: 'Clearance', value: rules.allow_clearance ? 'Allowed' : 'Skipped' },
    { label: 'Minimum score', value: `${rules.min_score}` },
  ];
}

export function rulesSummary(rules: VariantRules): string {
  const parts = [];
  if (rules.role_families.length) parts.push(rules.role_families.map(fieldLabel).join(', '));
  if (rules.levels.length) parts.push(rules.levels.map(fieldLabel).join(', '));
  if (rules.focus_skills.length) parts.push(`names ${rules.focus_skills.join(' or ')}`);
  if (rules.deal_breakers.length) parts.push(`not ${rules.deal_breakers.join(', ')}`);
  if (rules.max_years !== null) parts.push(`at most ${rules.max_years} years`);
  if (!rules.allow_clearance) parts.push('no clearance');
  parts.push(`score ${rules.min_score}+`);
  return parts.join(' · ');
}

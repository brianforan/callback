import { Alert, Anchor, Badge, Button, Group, Loader, Stack, Text, Tooltip } from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { formatDateTime } from '../lib/format';
import { useCompanyResearch, useStartCompanyResearch } from '../lib/queries';
import type { CompanyResearch, ResearchConfidence, ResearchSource } from '../lib/types';

const LABELS: Record<string, string> = {
  public: 'Public',
  private_vc: 'Private, venture-backed',
  private_pe: 'Private, private-equity-backed',
  private_bootstrapped: 'Private, bootstrapped',
  subsidiary: 'Subsidiary',
  nonprofit: 'Nonprofit',
  government: 'Government',
  pre_seed: 'Pre-seed',
  seed: 'Seed',
  series_a: 'Series A',
  series_b: 'Series B',
  series_c: 'Series C',
  series_d_or_later: 'Series D or later',
  growth: 'Growth stage',
  not_funded: 'Not venture funded',
  growing: 'growing',
  flat: 'flat',
  shrinking: 'shrinking',
  layoff: 'Layoff',
  hiring_freeze: 'Hiring freeze',
  reorg: 'Reorganization',
  acquisition: 'Acquired a company',
  acquired: 'Was acquired',
  ipo: 'IPO',
  leadership_change: 'Leadership change',
  office_opened: 'Office opened',
  office_closed: 'Office closed',
  mostly_junior: 'mostly junior',
  mixed: 'a mix of levels',
  mostly_senior: 'mostly senior',
  big_tech: 'big tech',
  startup: 'startups',
  consulting: 'consulting',
  agency: 'agencies',
  enterprise: 'large enterprises',
  remote: 'Remote',
  hybrid: 'Hybrid',
  onsite: 'On-site',
};

const CONFIDENCE_COLOR: Record<ResearchConfidence, string> = {
  stated: 'green',
  estimated: 'yellow',
  unknown: 'gray',
};

const usd = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  notation: 'compact',
  maximumFractionDigits: 1,
});

export function label(value: string | undefined): string {
  return value ? (LABELS[value] ?? value) : '';
}

function known(value: string | undefined): value is string {
  return Boolean(value) && value !== 'unknown';
}

/** A fact's source as a small numbered link, its title and date on hover. */
function Cite({ sources, index }: { sources: ResearchSource[]; index: number | undefined }) {
  const source = index === undefined ? undefined : sources[index];
  if (!source || index === undefined) return null;
  const tip = [source.title, source.published_on].filter(Boolean).join(' · ') || source.url;
  return (
    <Tooltip label={tip} multiline maw={320}>
      <Anchor href={source.url} target="_blank" rel="noopener noreferrer" size="xs" ml={4}>
        [{index + 1}]
      </Anchor>
    </Tooltip>
  );
}

function Fact({
  title,
  confidence,
  children,
}: {
  title: string;
  confidence: ResearchConfidence | undefined;
  children: ReactNode;
}) {
  const level = confidence ?? 'unknown';
  return (
    <Stack gap={2}>
      <Group gap={6}>
        <Text size="sm" fw={600}>
          {title}
        </Text>
        <Badge size="xs" variant="light" color={CONFIDENCE_COLOR[level]} tt="none">
          {level}
        </Badge>
      </Group>
      {level === 'unknown' ? (
        <Text size="sm" c="dimmed">
          Unknown
        </Text>
      ) : (
        children
      )}
    </Stack>
  );
}

function joined(parts: (string | false | undefined | null)[]): string {
  return parts.filter(Boolean).join(' · ');
}

export function Snapshot({ research }: { research: CompanyResearch }) {
  const { sources, profile, ownership, funding, size, events, engineering, workforce } = research;
  const { interviewing, confidence } = research;
  const place = [profile.hq_city, profile.hq_country].filter(Boolean).join(', ');
  const years =
    workforce.typical_years_low && workforce.typical_years_high
      ? `${workforce.typical_years_low}–${workforce.typical_years_high} years`
      : workforce.typical_years_low
        ? `${workforce.typical_years_low}+ years`
        : '';

  return (
    <Stack gap="sm">
      <Fact title="What it is" confidence={confidence.profile}>
        <Text size="sm">
          {joined([
            profile.what_it_does,
            profile.industry,
            place && `HQ ${place}`,
            Boolean(profile.founded_year) && `founded ${profile.founded_year}`,
          ])}
          <Cite sources={sources} index={profile.source} />
        </Text>
      </Fact>

      <Fact title="Ownership and funding" confidence={confidence.ownership}>
        <Text size="sm">
          {joined([
            label(ownership.kind),
            ownership.ticker && `${ownership.ticker}${ownership.exchange ? ` on ${ownership.exchange}` : ''}`,
            ownership.parent && `part of ${ownership.parent}`,
            known(funding.stage) && label(funding.stage),
            Boolean(funding.total_raised_usd) && `${usd.format(funding.total_raised_usd ?? 0)} raised`,
          ]) || 'Unknown'}
          <Cite sources={sources} index={ownership.source} />
          <Cite sources={sources} index={funding.source} />
        </Text>
        {funding.rounds?.map((round) => (
          <Text key={`${round.date}-${round.round}`} size="xs" c="dimmed">
            {joined([round.date, round.round, Boolean(round.amount_usd) && usd.format(round.amount_usd)])}
            <Cite sources={sources} index={round.source} />
          </Text>
        ))}
      </Fact>

      <Fact title="Size" confidence={confidence.size}>
        <Text size="sm">
          {joined([
            Boolean(size.headcount) && `About ${size.headcount?.toLocaleString()} people`,
            size.as_of && `as of ${size.as_of}`,
            known(size.trend) && label(size.trend),
          ]) || 'Unknown'}
          <Cite sources={sources} index={size.source} />
        </Text>
      </Fact>

      <Fact title="Recent events" confidence={events.length ? (confidence.events ?? 'stated') : 'unknown'}>
        {events.map((event) => (
          <Text key={`${event.date}-${event.kind}-${event.detail}`} size="sm">
            {joined([
              event.date,
              label(event.kind),
              event.detail,
              Boolean(event.count) && `${event.count.toLocaleString()} people`,
              Boolean(event.percent) && `${event.percent}%`,
            ])}
            <Cite sources={sources} index={event.source} />
          </Text>
        ))}
      </Fact>

      <Fact title="Engineering" confidence={confidence.engineering}>
        {Boolean(engineering.stack?.length) && (
          <Group gap={6}>
            {engineering.stack?.map((item) => (
              <Badge key={item.name} variant="default" tt="none" fw={500}>
                {item.name}
              </Badge>
            ))}
          </Group>
        )}
        <Text size="sm">
          {joined([
            engineering.eng_blog && 'Has an engineering blog',
            engineering.open_source && 'publishes open source',
            Boolean(engineering.team_size) && `about ${engineering.team_size} engineers`,
          ])}
          <Cite sources={sources} index={engineering.source} />
        </Text>
      </Fact>

      <Fact title="Who works there" confidence={confidence.workforce}>
        <Text size="sm">
          {joined([
            years && `Engineers typically have ${years}`,
            known(workforce.seniority_mix) && label(workforce.seniority_mix),
            Boolean(workforce.prior_industries?.length) &&
              `often from ${workforce.prior_industries?.join(', ')}`,
            Boolean(workforce.prior_employer_kinds?.length) &&
              `and ${workforce.prior_employer_kinds?.map(label).join(', ')}`,
            Boolean(workforce.cs_degree_percent) && `${workforce.cs_degree_percent}% with a CS degree`,
            Boolean(workforce.team_locations?.length) && `teams in ${workforce.team_locations?.join(', ')}`,
            known(workforce.remote_policy) && label(workforce.remote_policy),
          ]) || 'Unknown'}
          <Cite sources={sources} index={workforce.source} />
        </Text>
      </Fact>

      <Fact title="Interviewing" confidence={confidence.interviewing}>
        <Text size="sm">
          {joined([
            Boolean(interviewing.stages?.length) && interviewing.stages?.join(' → '),
            interviewing.take_home === 'yes' && 'has a take-home',
            interviewing.take_home === 'no' && 'no take-home',
            Boolean(interviewing.typical_weeks) && `about ${interviewing.typical_weeks} weeks`,
            Boolean(interviewing.weighs?.length) && `weighs ${interviewing.weighs?.join(', ')}`,
          ]) || 'Unknown'}
          <Cite sources={sources} index={interviewing.source} />
        </Text>
      </Fact>

      {sources.length > 0 && (
        <Stack gap={2}>
          <Text size="sm" fw={600}>
            Sources
          </Text>
          {sources.map((source, index) => (
            <Text key={source.url} size="xs" c="dimmed" truncate>
              {index + 1}.{' '}
              <Anchor href={source.url} target="_blank" rel="noopener noreferrer" size="xs">
                {source.title || source.url}
              </Anchor>
              {source.published_on && ` · ${source.published_on}`}
            </Text>
          ))}
        </Stack>
      )}
    </Stack>
  );
}

/** Whether an application's company is researched, one under way, a button to research it, and
 * a link to the company's page. */
export function CompanyResearchPanel({ applicationId }: { applicationId: string }) {
  const view = useCompanyResearch(applicationId);
  const start = useStartCompanyResearch();
  const data = view.data;
  if (view.isPending) return <Loader size="sm" />;
  if (!data) return null;

  const latest = data.latest;
  return (
    <Stack gap="sm">
      {data.active ? (
        <Group gap="xs">
          <Loader size="xs" />
          <Text size="sm" c="dimmed">
            Researching {data.company ?? 'this company'}… this usually takes under a minute.
          </Text>
        </Group>
      ) : (
        <Group justify="space-between" align="flex-start">
          <Text size="sm" c="dimmed" style={{ flex: 1 }}>
            {latest?.finished_at
              ? `Researched ${formatDateTime(latest.finished_at)} from ${latest.sources.length} sources. Facts about a company are shared by everyone using Callback.`
              : data.agency
                ? data.blocked
                : 'Not researched yet. Research looks up what the company does, how it’s funded and hiring, and how it interviews.'}
          </Text>
          {!data.agency && (
            <Tooltip label={data.blocked} disabled={!data.blocked} multiline maw={280}>
              <Button
                size="xs"
                variant="default"
                disabled={Boolean(data.blocked)}
                loading={start.isPending}
                onClick={() => start.mutate(applicationId)}
              >
                Research this company
              </Button>
            </Tooltip>
          )}
        </Group>
      )}
      {data.failure && !data.active && (
        <Alert color="yellow" variant="light" icon={<IconInfoCircle size={16} />}>
          {data.failure}
        </Alert>
      )}
      {latest && data.company_id && (
        <Anchor component={Link} to={`/insights/companies/${data.company_id}`} size="sm">
          {data.company}'s page: hiring history and every snapshot
        </Anchor>
      )}
    </Stack>
  );
}

import {
  Alert,
  Anchor,
  Badge,
  Button,
  Divider,
  Grid,
  Group,
  Loader,
  Paper,
  SimpleGrid,
  Stack,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import { useIntersection } from '@mantine/hooks';
import {
  IconAlertTriangle,
  IconBrandLinkedin,
  IconBuildingSkyscraper,
  IconExternalLink,
  IconWorld,
} from '@tabler/icons-react';
import { useEffect, useState, type ReactNode } from 'react';

import { fieldLabel, formatSalary } from '../lib/postings';
import { usePostingDetail, useRequestBrief, useTrackLead } from '../lib/queries';
import type { PostingDetail, ScoutLead, ScoutRun } from '../lib/types';
import { ApplySteps, jobFromPosting } from './ApplySteps';
import { MatchBadge, MatchDetails } from './MatchEstimate';

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Stack gap={4}>
      <Text size="xs" fw={700} c="dimmed" tt="uppercase">
        {title}
      </Text>
      {children}
    </Stack>
  );
}

function Points({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <Block title={title}>
      {items.map((item) => (
        <Text key={item} size="sm">
          • {item}
        </Text>
      ))}
    </Block>
  );
}

/** Pay as Callback parsed it from the posting, or as the agent read it when it couldn't. */
function salaryText(lead: ScoutLead, posting: PostingDetail | undefined): string | null {
  if (posting && (posting.salary_min !== null || posting.salary_max !== null)) {
    return formatSalary(posting.salary_min, posting.salary_max);
  }
  return lead.salary || null;
}

const FIT_BADGES: Record<string, { label: string; color: string }> = {
  strong: { label: 'Strong fit', color: 'green' },
  good: { label: 'Good fit', color: 'blue' },
  stretch: { label: 'Stretch', color: 'orange' },
};

/** A lead whose posting is an agency's listing, not the employer's own. */
export function isAgencyLead(lead: ScoutLead): boolean {
  return Boolean(lead.agency.trim()) && !lead.agency_url;
}

function FitBadges({ lead }: { lead: ScoutLead }) {
  const fit = FIT_BADGES[lead.fit.trim().toLowerCase()];
  if (!fit && !lead.agency) return null;
  const foundThroughAgency = Boolean(lead.agency.trim()) && Boolean(lead.agency_url);
  return (
    <Group gap="xs">
      {fit && (
        <Badge color={fit.color} variant="light" size="lg" tt="none">
          {fit.label}
        </Badge>
      )}
      {foundThroughAgency && (
        <Tooltip
          label="Scout found the employer’s own posting for this agency listing, so you can apply directly."
          multiline
          w={260}
          withArrow
        >
          <Badge
            component="a"
            href={lead.agency_url}
            target="_blank"
            rel="noopener noreferrer nofollow"
            color="teal"
            variant="light"
            size="lg"
            tt="none"
            leftSection={<IconBuildingSkyscraper size={14} />}
            style={{ cursor: 'pointer' }}
          >
            Direct posting · found via {lead.agency}’s listing
          </Badge>
        </Tooltip>
      )}
      {isAgencyLead(lead) && (
        <Tooltip
          label="Agency jobs can pay less, and can stop you being hired directly by the company later."
          multiline
          w={260}
          withArrow
        >
          <Badge
            color="yellow"
            variant="light"
            size="lg"
            tt="none"
            leftSection={<IconAlertTriangle size={14} />}
          >
            Posted by an agency: {lead.agency}
          </Badge>
        </Tooltip>
      )}
    </Group>
  );
}

function Facts({ lead, posting }: { lead: ScoutLead; posting: PostingDetail | undefined }) {
  const level = posting?.level ?? (lead.level || null);
  const mode = posting?.work_mode ?? (lead.work_mode || null);
  const type = posting?.employment_type ?? (lead.employment_type || null);
  const salary = salaryText(lead, posting);
  const facts = [
    salary && { label: 'Pay', value: salary },
    level && { label: 'Level', value: fieldLabel(level) },
    mode && { label: 'Where', value: fieldLabel(mode) },
    type && { label: 'Type', value: fieldLabel(type) },
    lead.posted && { label: 'Posted', value: lead.posted },
  ].filter(Boolean) as { label: string; value: string }[];
  if (!facts.length) return null;
  return (
    <Group gap="xs">
      {facts.map((fact) => (
        <Badge key={fact.label} variant="default" size="lg" tt="none" fw={500}>
          <Text span size="xs" c="dimmed" mr={4}>
            {fact.label}
          </Text>
          {fact.value}
        </Badge>
      ))}
      {posting?.staged_at && !posting.applications.length && (
        <Badge color="violet" variant="light" size="lg" tt="none">
          Ready to apply
        </Badge>
      )}
    </Group>
  );
}

/** A LinkedIn company search for the name, built here, for leads with no company page found. */
function linkedinSearch(company: string): string {
  return `https://www.linkedin.com/search/results/companies/?keywords=${encodeURIComponent(company)}`;
}

/** The company's own site and its LinkedIn page, as far as the run found them. */
function CompanyLinks({ lead }: { lead: ScoutLead }) {
  const linkedin = lead.company_linkedin || linkedinSearch(lead.company);
  return (
    <Group gap="md">
      {lead.company_website && (
        <Anchor href={lead.company_website} target="_blank" rel="noopener noreferrer nofollow" size="sm">
          <Group gap={4} wrap="nowrap">
            <IconWorld size={16} style={{ flexShrink: 0 }} />
            {hostOf(lead.company_website)}
          </Group>
        </Anchor>
      )}
      <Tooltip
        label={lead.company_linkedin ? 'The company’s LinkedIn page' : 'Searches LinkedIn for the company'}
        withArrow
      >
        <Anchor href={linkedin} target="_blank" rel="noopener noreferrer nofollow" size="sm">
          <Group gap={4} wrap="nowrap">
            <IconBrandLinkedin size={16} style={{ flexShrink: 0 }} />
            {lead.company_linkedin ? 'LinkedIn' : 'Find on LinkedIn'}
          </Group>
        </Anchor>
      </Tooltip>
    </Group>
  );
}

/** A Companies lead's brief, written when asked for: the button, its progress, or why it failed. */
function BriefRequest({ runId, lead }: { runId: string; lead: ScoutLead }) {
  const brief = useRequestBrief();
  if (lead.brief_status === 'queued' || lead.brief_status === 'running') {
    return (
      <Group gap="xs">
        <Loader size="xs" />
        <Text size="sm" c="dimmed">
          Writing the brief. It appears here in a minute or so.
        </Text>
      </Group>
    );
  }
  if (lead.brief_status !== 'none' && lead.brief_status !== 'failed') return null;
  return (
    <Stack gap={6}>
      <Group gap="sm">
        <Button
          variant="light"
          size="xs"
          loading={brief.isPending}
          onClick={() => brief.mutate({ runId, leadId: lead.id })}
        >
          {lead.brief_status === 'failed' ? 'Try the brief again' : 'Write brief'}
        </Button>
        <Text size="xs" c="dimmed">
          What they want, where you match and what to lead with. About $0.08.
        </Text>
      </Group>
      {lead.brief_status === 'failed' && lead.brief_error && (
        <Text size="xs" c="red">
          {lead.brief_error}
        </Text>
      )}
    </Stack>
  );
}

/** How far below the screen a card starts loading its saved posting, so it's ready on arrival. */
const LOAD_AHEAD: IntersectionObserverInit = { rootMargin: '800px 0px' };

/**
 * One lead, laid out to apply from: the offer and fit first, then the match, then the resume to
 * send and the application itself. Leads track themselves, so the posting is already saved and
 * marked ready to apply with the best baseline.
 */
export function LeadCard({
  runId,
  lead,
  actions,
  showApply = true,
}: {
  runId: string;
  lead: ScoutLead;
  /** Buttons for moving the lead along, such as Applied, Later and Not for me. */
  actions?: ReactNode;
  /** False for a dismissed lead, which has no posting to apply from. */
  showApply?: boolean;
}) {
  // The saved posting loads once the card comes near the screen. Loading every lead's at once
  // queued the page's other requests, such as Listing expired, behind them for half a minute.
  const { ref, entry } = useIntersection<HTMLDivElement>(LOAD_AHEAD);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    if (entry?.isIntersecting) setSeen(true);
  }, [entry?.isIntersecting]);
  const { data: posting, isPending } = usePostingDetail(seen ? lead.posting_id : null);
  const track = useTrackLead();

  return (
    <Paper ref={ref} withBorder p="lg" radius="md">
      <Stack gap="md">
        <Group justify="space-between" align="flex-start" wrap="nowrap" gap="md">
          <Stack gap={4} style={{ minWidth: 0 }}>
            <Title order={4}>{lead.title}</Title>
            <Text size="sm" c="dimmed">
              {lead.company}
              {lead.location && ` · ${lead.location}`}
            </Text>
          </Stack>
          {posting && (
            <Stack gap={2} align="center" style={{ flexShrink: 0 }}>
              <MatchBadge match={posting.match} />
              <Text size="xs" c="dimmed">
                match
              </Text>
            </Stack>
          )}
        </Group>

        <FitBadges lead={lead} />
        <Facts lead={lead} posting={posting} />
        {actions}

        <Group gap="sm">
          <Button
            component="a"
            href={lead.url}
            target="_blank"
            rel="noopener noreferrer nofollow"
            rightSection={<IconExternalLink size={16} />}
          >
            Open the posting to apply
          </Button>
          <Text size="xs" c="dimmed">
            on {hostOf(lead.url)}
          </Text>
          <CompanyLinks lead={lead} />
          {!lead.posting_id && showApply && (
            <Button
              variant="light"
              loading={track.isPending}
              onClick={() => track.mutate({ runId, leadId: lead.id })}
            >
              Track
            </Button>
          )}
        </Group>

        <Grid gutter="lg">
          <Grid.Col span={{ base: 12, md: posting ? 7 : 12 }}>
            <Stack gap="md">
              {lead.fit_summary && (
                <Block title="Why it fits">
                  <Text size="sm">{lead.fit_summary}</Text>
                </Block>
              )}
              <BriefRequest runId={runId} lead={lead} />
              {(lead.offer_summary || lead.salary) && (
                <Block title="The offer">
                  {lead.salary && (
                    <Text size="sm">
                      <b>Pay:</b> {lead.salary}
                    </Text>
                  )}
                  {lead.offer_summary && <Text size="sm">{lead.offer_summary}</Text>}
                </Block>
              )}
              <Points title="What applying takes" items={lead.how_to_apply} />
              <Points title="Lead with" items={lead.application_tips} />
              {lead.company_notes && (
                <Block title="About the company">
                  <Text size="sm">{lead.company_notes}</Text>
                </Block>
              )}
              {/* In this column, beside the match details, so the two columns run about as
                  long as each other instead of leaving a gap under the brief. */}
              <Divider />
              <Points title="They want" items={lead.what_they_want} />
              <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                <Points title="You match" items={lead.you_match} />
                <Points title="You lack" items={lead.you_lack} />
              </SimpleGrid>
            </Stack>
          </Grid.Col>
          {posting && (
            <Grid.Col span={{ base: 12, md: 5 }}>
              <Block title="How your resumes match">
                <MatchDetails match={posting.match} />
              </Block>
            </Grid.Col>
          )}
        </Grid>

        {lead.posting_id && showApply && (
          <>
            <Divider />
            <Block title="Your resume and application">
              {isPending ? (
                <Loader size="sm" />
              ) : posting ? (
                <ApplySteps job={jobFromPosting(posting)} />
              ) : (
                <Text size="sm" c="dimmed">
                  The saved posting couldn't be loaded. It may have been deleted.
                </Text>
              )}
            </Block>
          </>
        )}

        {lead.sources.length > 0 && (
          <Group gap="xs">
            <Text size="xs" c="dimmed">
              Sources:
            </Text>
            {lead.sources.map((source) => (
              <Anchor
                key={source}
                href={source}
                target="_blank"
                rel="noopener noreferrer nofollow"
                size="xs"
              >
                {hostOf(source)}
              </Anchor>
            ))}
          </Group>
        )}
      </Stack>
    </Paper>
  );
}

/** What applying through a staffing or recruiting agency risks, shown above agency leads. */
export function AgencyNote() {
  return (
    <Alert
      color="yellow"
      variant="light"
      icon={<IconAlertTriangle size={18} />}
      title="These jobs were posted by staffing or recruiting agencies"
    >
      <Stack gap={4}>
        <Text size="sm">
          • They often pay less than the company would pay you directly, since the agency
          takes a cut.
        </Text>
        <Text size="sm">
          • Applying through an agency can stop the company hiring you directly later, for
          this job or others, while the agency’s agreement with them lasts.
        </Text>
        <Text size="sm">
          • Before applying, check who the employer is and whether it posts the job on its
          own careers page. The “Find the employer” setting has Scout do this for you.
        </Text>
      </Stack>
    </Alert>
  );
}

/**
 * A run's leads, with jobs posted by staffing or recruiting agencies kept apart from the ones
 * the employer posted, under a note on what applying through an agency risks.
 */
export function LeadList({ run, empty }: { run: ScoutRun; empty: ReactNode }) {
  const direct = run.leads.filter((lead) => !isAgencyLead(lead));
  const agency = run.leads.filter(isAgencyLead);
  if (!run.leads.length) return <>{empty}</>;
  return (
    <Stack gap="md">
      {direct.map((lead) => (
        <LeadCard key={lead.id} runId={run.id} lead={lead} />
      ))}
      {agency.length > 0 && (
        <Stack gap="sm">
          <Title order={4}>Through agencies ({agency.length})</Title>
          <AgencyNote />
          {agency.map((lead) => (
            <LeadCard key={lead.id} runId={run.id} lead={lead} />
          ))}
        </Stack>
      )}
    </Stack>
  );
}

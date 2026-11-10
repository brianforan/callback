import {
  Accordion,
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Stack,
  Table,
  Text,
  ThemeIcon,
  Tooltip,
} from '@mantine/core';
import { IconArrowRight, IconInfoCircle } from '@tabler/icons-react';

import { formatDateTime } from '../lib/format';
import { useRejectAnalysis, useRunRejectAnalysis } from '../lib/queries';
import {
  areaLabel,
  ESTIMATE_BANDS,
  GAP_ACTIONS,
  MEASURE_LABELS,
  REQUIREMENT_SHORT,
  STAGE_LABELS,
} from '../lib/rejections';
import type {
  CompanyContext,
  RejectAnalysis,
  RejectAnalysisStatus,
  TechnologyGap,
} from '../lib/types';
import { Checklist, VerdictBadge } from './MatchEstimate';

function scoreColor(score: number): string {
  if (score >= 75) return 'green';
  if (score >= 50) return 'blue';
  return 'gray';
}

export function GapActionBadge({ action }: { action: TechnologyGap['action'] }) {
  const meta = GAP_ACTIONS[action];
  return (
    <Badge size="sm" variant="light" color={meta.color} tt="none">
      {meta.label}
    </Badge>
  );
}

function FocusList({ analysis }: { analysis: RejectAnalysis }) {
  if (!analysis.focus_areas.length) return null;
  return (
    <Stack gap="xs">
      <Text fw={600} size="sm">
        Focus on
      </Text>
      {analysis.focus_areas.map((area, index) => (
        <Card key={`${index}-${area.title}`} withBorder padding="sm">
          <Group gap="xs" wrap="nowrap" align="flex-start">
            <ThemeIcon size="sm" radius="xl" variant="light">
              <Text size="xs" fw={700}>
                {index + 1}
              </Text>
            </ThemeIcon>
            <Stack gap={4} style={{ flex: 1 }}>
              <Group gap={6}>
                <Text size="sm" fw={600}>
                  {area.title}
                </Text>
                <Badge size="xs" variant="outline" color="gray" tt="none">
                  {area.kind === 'technology' ? 'Technology' : 'Experience'}
                </Badge>
              </Group>
              <Text size="xs" c="dimmed">
                {area.why}
              </Text>
              <Group gap={4} wrap="nowrap" align="flex-start">
                <IconArrowRight size={14} style={{ flexShrink: 0, marginTop: 2 }} />
                <Text size="sm">{area.action}</Text>
              </Group>
            </Stack>
          </Group>
        </Card>
      ))}
    </Stack>
  );
}

function TechnologyGaps({ gaps }: { gaps: TechnologyGap[] }) {
  if (!gaps.length) {
    return (
      <Text size="sm" c="dimmed">
        Every technology the posting named is backed by a role or project on this resume.
      </Text>
    );
  }
  return (
    <Table.ScrollContainer minWidth={0}>
      <Table verticalSpacing={6}>
        <Table.Tbody>
          {gaps.map((gap) => (
            <Table.Tr key={`${gap.requirement}-${gap.name}`}>
              <Table.Td w={76}>
                <VerdictBadge verdict={gap.verdict} />
              </Table.Td>
              <Table.Td>
                <Group gap={6} wrap="nowrap">
                  <Text size="sm" fw={500}>
                    {gap.name}
                  </Text>
                  <Text size="xs" c="dimmed">
                    {REQUIREMENT_SHORT[gap.requirement]}
                  </Text>
                </Group>
                <Text size="xs" c="dimmed">
                  {gap.note}
                </Text>
              </Table.Td>
              <Table.Td w={150} ta="right">
                <GapActionBadge action={gap.action} />
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

function ExperienceGaps({ analysis }: { analysis: RejectAnalysis }) {
  if (!analysis.experience_gaps.length) return null;
  return (
    <Stack gap="xs">
      <Text fw={600} size="sm">
        Experience the posting asked for
      </Text>
      {analysis.experience_gaps.map((gap, index) => (
        <Stack key={`${index}-${gap.need}`} gap={2}>
          <Group gap={6}>
            <Badge size="xs" variant="light" color="grape" tt="none">
              {areaLabel(gap.area)}
            </Badge>
            <Text size="sm" fw={500}>
              {gap.need}
            </Text>
          </Group>
          <Text size="xs" c="dimmed">
            You show: {gap.evidence}
          </Text>
          <Text size="xs">Try: {gap.suggestion}</Text>
        </Stack>
      ))}
    </Stack>
  );
}

function EstimateCard({ analysis }: { analysis: RejectAnalysis }) {
  const estimate = analysis.estimate;
  if (!estimate) return null;
  const meta = ESTIMATE_BANDS[estimate.band];
  return (
    <Card withBorder padding="sm">
      <Stack gap={6}>
        <Group gap="xs">
          <Badge variant="light" color={meta.color} tt="none">
            {meta.label}
          </Badge>
          <Text size="xs" c="dimmed">
            A rough estimate that can be wrong, not the reason you were turned down.
          </Text>
        </Group>
        {estimate.reasons.map((reason) => (
          <Text key={reason} size="sm">
            {reason}
          </Text>
        ))}
      </Stack>
    </Card>
  );
}

function ComparisonTable({ analysis }: { analysis: RejectAnalysis }) {
  if (!analysis.comparison.length) return null;
  return (
    <Stack gap="xs">
      <Text fw={600} size="sm">
        Who they hire, beside you
      </Text>
      <Table withTableBorder fz="sm" verticalSpacing={6}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th />
            <Table.Th>Typical there</Table.Th>
            <Table.Th>Your resume</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {analysis.comparison.map((line) => (
            <Table.Tr key={line.measure}>
              <Table.Td>{MEASURE_LABELS[line.measure]}</Table.Td>
              <Table.Td>{line.typical}</Table.Td>
              <Table.Td>
                {line.yours}
                {line.note && (
                  <Text size="xs" c="dimmed">
                    {line.note}
                  </Text>
                )}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Stack>
  );
}

/** This company now: research facts and catalog hiring, each with where it came from. */
export function CompanyNow({ company }: { company: CompanyContext }) {
  return (
    <Stack gap={6}>
      <Text fw={600} size="sm">
        This company now
      </Text>
      {company.lines.map((line, index) => (
        <Group key={`${line.label}-${index}`} gap={6} wrap="nowrap" align="flex-start">
          <Text size="xs" c="dimmed" w={150} style={{ flexShrink: 0 }}>
            {line.label}
          </Text>
          <Text size="sm" style={{ flex: 1 }}>
            {line.text}
            {(line.source_url || line.source_title) && (
              <Text span size="xs" c="dimmed">
                {' · '}
                {line.source_url ? (
                  <Anchor href={line.source_url} target="_blank" rel="noopener noreferrer" size="xs">
                    {line.source_title || 'source'}
                  </Anchor>
                ) : (
                  line.source_title
                )}
                {line.as_of && `, ${line.as_of}`}
              </Text>
            )}
          </Text>
        </Group>
      ))}
    </Stack>
  );
}

function Report({ analysis }: { analysis: RejectAnalysis }) {
  return (
    <Stack gap="md">
      <Group gap="xs">
        {analysis.score !== null && (
          <Badge
            variant="filled"
            color={scoreColor(analysis.score)}
            size="lg"
            tt="none"
            className="tabular"
          >
            {analysis.score} / 100
          </Badge>
        )}
        <Text size="sm" c="dimmed">
          {analysis.resume_label ? `Against ${analysis.resume_label}` : 'No resume compared'}
          {analysis.stage ? ` · ${STAGE_LABELS[analysis.stage]}` : ''}
        </Text>
      </Group>

      {analysis.note && (
        <Alert
          color={analysis.status === 'failed' ? 'yellow' : 'gray'}
          variant="light"
          icon={<IconInfoCircle size={16} />}
        >
          {analysis.note}
        </Alert>
      )}

      {analysis.summary && <Text size="sm">{analysis.summary}</Text>}

      <EstimateCard analysis={analysis} />

      <ComparisonTable analysis={analysis} />

      <FocusList analysis={analysis} />

      <Stack gap="xs">
        <Text fw={600} size="sm">
          Technologies
        </Text>
        <TechnologyGaps gaps={analysis.technology_gaps} />
      </Stack>

      <ExperienceGaps analysis={analysis} />

      {analysis.strengths.length > 0 && (
        <Stack gap={6}>
          <Text fw={600} size="sm">
            What lined up
          </Text>
          <Group gap={6}>
            {analysis.strengths.map((strength) => (
              <Badge key={strength} variant="light" color="green" tt="none">
                {strength}
              </Badge>
            ))}
          </Group>
        </Stack>
      )}

      {analysis.checklist.length > 0 && (
        <Accordion variant="contained">
          <Accordion.Item value="checklist">
            <Accordion.Control>
              <Text size="sm">Full checklist ({analysis.checklist.length})</Text>
            </Accordion.Control>
            <Accordion.Panel>
              <Checklist checks={analysis.checklist} />
            </Accordion.Panel>
          </Accordion.Item>
        </Accordion>
      )}
    </Stack>
  );
}

function Waiting({ status }: { status: RejectAnalysisStatus }) {
  const running = status.analysis?.status === 'running';
  const overLimit =
    !running && status.daily_limit !== null && status.used_today >= status.daily_limit;
  return (
    <Group gap="xs" wrap="nowrap">
      <Loader size="xs" />
      <Text size="sm" c="dimmed">
        {running
          ? 'Comparing the posting with your resume and fact sheet…'
          : status.researching
            ? 'Researching the company first, then writing the report. This usually takes a minute or two.'
            : overLimit
            ? `Today's ${status.daily_limit} analyses are used, so this one runs after midnight UTC.`
            : 'Waiting in the queue. This usually takes under a minute.'}
      </Text>
    </Group>
  );
}

/** What a rejected job asked for that the resume sent didn't show, and what to work on. */
export function RejectAnalysisPanel({ applicationId }: { applicationId: string }) {
  const { data: status, isLoading } = useRejectAnalysis(applicationId);
  const run = useRunRejectAnalysis();

  if (isLoading || !status) return <Loader size="sm" />;
  const analysis = status.analysis;

  const active = analysis?.status === 'queued' || analysis?.status === 'running';
  const runButton = (
    <Tooltip label={status.rerun_blocked} disabled={!status.rerun_blocked}>
      <Button
        size="xs"
        variant="light"
        loading={run.isPending}
        disabled={Boolean(status.rerun_blocked)}
        onClick={() => run.mutate(applicationId)}
      >
        {analysis ? 'Run again' : 'Analyze'}
      </Button>
    </Tooltip>
  );

  return (
    <Stack gap="sm">
      {!analysis && (
        <Group justify="space-between">
          <Text size="sm" c="dimmed">
            Not analyzed yet.
          </Text>
          {runButton}
        </Group>
      )}
      {active && <Waiting status={status} />}
      {analysis && !active && (
        <>
          <Report analysis={analysis} />
          <Group justify="space-between">
            <Text size="xs" c="dimmed">
              {analysis.finished_at ? `Analyzed ${formatDateTime(analysis.finished_at)}` : ''}
            </Text>
            {runButton}
          </Group>
        </>
      )}
    </Stack>
  );
}

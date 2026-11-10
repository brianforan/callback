import { Badge, Card, Grid, Group, Stack, Table, Text } from '@mantine/core';
import { Link } from 'react-router-dom';

import { rulesSummary } from '../lib/baselines';
import { useVariantReport } from '../lib/queries';
import type { VariantFit, VariantSummary } from '../lib/types';
import { FocusCard } from './Focus';
import { Empty, PageError, PageLoader } from './PageState';
import { EvidenceCard } from './ResumeInsights';

function FitStatus({ fit }: { fit: VariantFit }) {
  if (fit.best) {
    return (
      <Badge color="green" variant="light" tt="none" miw={84}>
        Best fit
      </Badge>
    );
  }
  if (fit.passes) {
    return (
      <Badge color="blue" variant="light" tt="none" miw={84}>
        Passes
      </Badge>
    );
  }
  return (
    <Badge color="gray" variant="light" tt="none" miw={84}>
      Skipped
    </Badge>
  );
}

function FitDetail({ fit }: { fit: VariantFit }) {
  if (fit.reasons.length) return <>{fit.reasons.join(' ')}</>;
  const gaps = [
    fit.missing_required.length ? `Missing ${fit.missing_required.join(', ')}.` : '',
    fit.weak_required.length ? `Only listed or short on years: ${fit.weak_required.join(', ')}.` : '',
  ].filter(Boolean);
  return <>{gaps.length ? gaps.join(' ') : 'Every required skill is met.'}</>;
}

function FitsTable({ fits }: { fits: VariantFit[] }) {
  return (
    <Table.ScrollContainer minWidth={0} mah={420}>
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Score</Table.Th>
            <Table.Th>Posting</Table.Th>
            <Table.Th>Status</Table.Th>
            <Table.Th>Why</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {fits.map((fit) => (
            <Table.Tr key={fit.posting_id}>
              <Table.Td>
                <Badge
                  variant="filled"
                  color={fit.score >= 75 ? 'green' : fit.score >= 50 ? 'blue' : 'gray'}
                  size="lg"
                  miw={44}
                  className="tabular"
                >
                  {fit.score}
                </Badge>
              </Table.Td>
              <Table.Td>
                <Text size="sm" fw={600}>
                  {fit.company}
                </Text>
                <Text size="xs" c="dimmed">
                  {fit.title}
                </Text>
              </Table.Td>
              <Table.Td>
                <FitStatus fit={fit} />
              </Table.Td>
              <Table.Td>
                <Text size="xs" c={fit.passes ? undefined : 'dimmed'}>
                  <FitDetail fit={fit} />
                </Text>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

function VariantSection({ variant, postings }: { variant: VariantSummary; postings: number }) {
  return (
    <FocusCard title={variant.label}>
      <Group justify="space-between" align="baseline" mb={4}>
        <Text fw={600}>{variant.label}</Text>
        <Text size="sm" className="tabular">
          Passes <b>{variant.passes}</b> of {postings} · best fit for <b>{variant.best_for}</b>
        </Text>
      </Group>
      <Text size="xs" c="dimmed" mb="sm">
        Rules: {rulesSummary(variant.rules)}
      </Text>
      <Grid gutter="sm">
        <Grid.Col span={{ base: 12, lg: 4 }}>
          <EvidenceCard resume={variant.evidence} />
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 8 }}>
          {variant.fits.length ? (
            <FitsTable fits={variant.fits} />
          ) : (
            <Empty title="No postings to check" hint="Save postings with their text first." />
          )}
        </Grid.Col>
      </Grid>
    </FocusCard>
  );
}

export function VariantInsights() {
  const { data: report, isPending, isError } = useVariantReport();

  if (isPending) return <PageLoader />;
  if (isError) return <PageError />;

  if (!report.variants.length) {
    return (
      <Card>
        <Empty
          title="No baselines yet"
          hint="Upload up to three baselines on the Resumes page and set the kinds of jobs each is for."
        />
        <Group justify="center">
          <Link to="/resumes">Go to Resumes</Link>
        </Group>
      </Card>
    );
  }

  return (
    <Stack>
      <Text size="xs" c="dimmed">
        Each baseline is checked on its own against your {report.postings} saved{' '}
        {report.postings === 1 ? 'posting' : 'postings'}, the way an ATS would see that resume
        alone, with no application history. Its rules then decide whether it suits the job, and
        the highest-scoring baseline that passes is the best fit.
      </Text>

      {report.variants.map((variant) => (
        <VariantSection key={variant.resume_id} variant={variant} postings={report.postings} />
      ))}

      <Grid gutter="sm">
        <Grid.Col span={{ base: 12, lg: 7 }}>
          <FocusCard title="Not covered by any baseline" h="100%">
            <Text fw={600} size="sm">
              Not covered by any baseline
            </Text>
            <Text size="xs" c="dimmed" mb="sm">
              Postings every baseline skips, with the closest baseline and why it skips them
            </Text>
            {report.uncovered.length ? (
              <Table.ScrollContainer minWidth={0} mah={360}>
                <Table>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Posting</Table.Th>
                      <Table.Th>Closest</Table.Th>
                      <Table.Th>Why</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {report.uncovered.map((posting) => (
                      <Table.Tr key={posting.posting_id}>
                        <Table.Td>
                          <Text size="sm" fw={600}>
                            {posting.company}
                          </Text>
                          <Text size="xs" c="dimmed">
                            {posting.title}
                          </Text>
                        </Table.Td>
                        <Table.Td>
                          <Text size="sm">
                            {posting.closest_variant ?? '–'}
                            {posting.closest_score !== null && ` (${posting.closest_score})`}
                          </Text>
                        </Table.Td>
                        <Table.Td>
                          <Text size="xs" c="dimmed">
                            {posting.reasons.join(' ')}
                          </Text>
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            ) : (
              <Empty title="Every posting has a baseline" />
            )}
          </FocusCard>
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 5 }}>
          <FocusCard title="Overlap" h="100%">
            <Text fw={600} size="sm">
              Overlap
            </Text>
            <Text size="xs" c="dimmed" mb="sm">
              Postings both baselines pass. A high count means the two may not need to be separate.
            </Text>
            {report.overlaps.length ? (
              <Table>
                <Table.Tbody className="tabular">
                  {report.overlaps.map((overlap) => (
                    <Table.Tr key={`${overlap.first}-${overlap.second}`}>
                      <Table.Td>
                        {overlap.first} and {overlap.second}
                      </Table.Td>
                      <Table.Td ta="right" fw={600}>
                        {overlap.postings}
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            ) : (
              <Empty title="Only one baseline" hint="Add another to compare them." />
            )}
          </FocusCard>
        </Grid.Col>
      </Grid>
    </Stack>
  );
}

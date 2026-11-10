import { Anchor, Card, Grid, Group, Loader, Stack, Text, Title } from '@mantine/core';
import { IconArrowLeft, IconExternalLink } from '@tabler/icons-react';
import { Link, useParams } from 'react-router-dom';

import { formatDay } from '../lib/format';
import { useApplication, useCompanyResearch, useRejectAnalysis } from '../lib/queries';
import { CompanyResearchPanel, Snapshot } from './CompanyResearchPanel';
import { Empty, PageError, PageLoader } from './PageState';
import { CompanyNow, RejectAnalysisPanel } from './RejectAnalysisPanel';
import { StatusBadge } from './StatusBadge';

/** The company beside the feedback: this company now, then its newest research snapshot. */
function Company({ applicationId }: { applicationId: string }) {
  const { data: status } = useRejectAnalysis(applicationId);
  const research = useCompanyResearch(applicationId);
  const latest = research.data?.latest;
  return (
    <Stack gap="md">
      {status?.company && <CompanyNow company={status.company} />}
      <CompanyResearchPanel applicationId={applicationId} />
      {research.isPending && <Loader size="sm" />}
      {latest && <Snapshot research={latest} />}
    </Stack>
  );
}

/** One rejected application's report: the feedback, and the company beside it. */
export function RejectionReportPage() {
  const { applicationId } = useParams();
  const application = useApplication(applicationId);
  if (application.isPending) return <PageLoader />;
  if (application.isError || !applicationId) return <PageError />;
  const { company, role_title, applied_on, job_url, status } = application.data;

  return (
    <Stack>
      <Anchor component={Link} to="/insights/rejections" size="sm">
        <Group gap={4}>
          <IconArrowLeft size={14} /> Rejections
        </Group>
      </Anchor>
      <Group justify="space-between" align="flex-end">
        <Stack gap={2}>
          <Title order={3}>{company}</Title>
          <Text size="sm" c="dimmed">
            {role_title} · applied {formatDay(applied_on)}
          </Text>
        </Stack>
        <Group gap="sm">
          <StatusBadge status={status} />
          {job_url && (
            <Anchor href={job_url} target="_blank" rel="noopener noreferrer" size="sm">
              <Group gap={4} wrap="nowrap">
                View posting <IconExternalLink size={14} />
              </Group>
            </Anchor>
          )}
        </Group>
      </Group>

      {status !== 'rejected' ? (
        <Card withBorder>
          <Empty
            title="No report"
            hint="This application isn't marked rejected. A report is written when it is."
          />
        </Card>
      ) : (
        <Grid>
          <Grid.Col span={{ base: 12, md: 7 }}>
            <Card withBorder>
              <Stack gap="sm">
                <Title order={5}>What this job wanted</Title>
                <RejectAnalysisPanel applicationId={applicationId} />
              </Stack>
            </Card>
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 5 }}>
            <Card withBorder>
              <Stack gap="sm">
                <Title order={5}>The company</Title>
                <Company applicationId={applicationId} />
              </Stack>
            </Card>
          </Grid.Col>
        </Grid>
      )}
    </Stack>
  );
}

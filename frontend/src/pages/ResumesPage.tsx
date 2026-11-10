import {
  ActionIcon,
  Button,
  Card,
  Group,
  Stack,
  Table,
  Tabs,
  Text,
  TextInput,
  Tooltip,
} from '@mantine/core';
import { Dropzone, type FileRejection } from '@mantine/dropzone';
import { notifications } from '@mantine/notifications';
import { IconDownload, IconEye, IconFileTypography, IconUpload, IconX } from '@tabler/icons-react';
import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { BaselinesPanel } from '../components/BaselinesPanel';
import { ConfirmDelete } from '../components/ConfirmDelete';
import { FactSheetPanel } from '../components/FactSheetPanel';
import { VariantRulesModal } from '../components/VariantRulesModal';
import { FocusCard } from '../components/Focus';
import { ResumePreviewModal } from '../components/ResumePreviewModal';
import { Empty, PageError, PageLoader } from '../components/PageState';
import { SkillsPanel } from '../components/SkillsPanel';
import { downloadFile, formatDay, pct, saveTextFile } from '../lib/format';
import {
  fetchResumeDetail,
  useDeleteResume,
  useOverview,
  useResumes,
  useUploadResume,
} from '../lib/queries';
import {
  MAX_PDF_BYTES,
  PDF_ONLY_NOTE,
  RESUME_ACCEPT,
  rejectionMessage,
  resumeDropValidator,
} from '../lib/resumeFiles';
import { PageTitle } from '../lib/sections';
import type { Resume } from '../lib/types';

const TABS = ['baselines', 'versions', 'facts', 'skills'];

export function ResumesPage() {
  const { data: resumes, isPending, isError } = useResumes();
  const { data: overview } = useOverview();
  const upload = useUploadResume();
  const remove = useDeleteResume();
  const [label, setLabel] = useState('');
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [editing, setEditing] = useState<Resume | null>(null);
  const [params] = useSearchParams();
  const [tab, setTab] = useState<string | null>(TABS.includes(params.get('tab') ?? '') ? params.get('tab') : 'baselines');

  if (isPending) return <PageLoader />;
  if (isError) return <PageError />;

  function handleDrop(files: File[]) {
    upload.mutate(
      { file: files[0], label },
      {
        onSuccess: (resume) => {
          setLabel('');
          notifications.show({ color: 'green', message: `Saved ${resume.label}` });
        },
      },
    );
  }

  async function handleDownload(resume: Resume) {
    // An uploaded PDF has no LaTeX of its own, so the PDF itself is what's downloaded.
    if (resume.source_format === 'pdf') {
      downloadFile(`/api/resumes/${resume.id}/pdf`);
      return;
    }
    try {
      const detail = await fetchResumeDetail(resume.id);
      saveTextFile(detail.filename, detail.source_tex, 'text/x-tex;charset=utf-8');
    } catch (error) {
      notifications.show({ color: 'red', message: (error as Error).message });
    }
  }

  return (
    <Stack>
      <PageTitle section="resumes" />

      <Tabs value={tab} onChange={setTab}>
        <Tabs.List>
          <Tabs.Tab value="baselines">Baselines</Tabs.Tab>
          <Tabs.Tab value="versions">All versions</Tabs.Tab>
          <Tabs.Tab value="facts">Fact sheet</Tabs.Tab>
          <Tabs.Tab value="skills">Skills</Tabs.Tab>
        </Tabs.List>
      </Tabs>

      {tab === 'facts' && <FactSheetPanel />}
      {tab === 'skills' && <SkillsPanel />}
      {tab === 'baselines' && <BaselinesPanel resumes={resumes} onEditRules={setEditing} />}
      {tab === 'versions' && (
        <>
          <Card>
            <Stack gap="sm">
              <TextInput
                size="xs"
                maw={320}
                label="Label for the next upload (optional)"
                placeholder="e.g. Platform-focused"
                maxLength={120}
                value={label}
                onChange={(event) => setLabel(event.currentTarget.value)}
              />
              <Dropzone
                onDrop={handleDrop}
                onReject={(rejections: FileRejection[]) =>
                  notifications.show({ color: 'red', message: rejectionMessage(rejections) })
                }
                maxSize={MAX_PDF_BYTES}
                validator={resumeDropValidator}
                multiple={false}
                accept={RESUME_ACCEPT}
                loading={upload.isPending}
              >
                <Group justify="center" gap="md" mih={72} style={{ pointerEvents: 'none' }}>
                  <Dropzone.Accept>
                    <IconUpload size={28} stroke={1.5} />
                  </Dropzone.Accept>
                  <Dropzone.Reject>
                    <IconX size={28} stroke={1.5} />
                  </Dropzone.Reject>
                  <Dropzone.Idle>
                    <IconFileTypography size={28} stroke={1.5} />
                  </Dropzone.Idle>
                  <div>
                    <Text size="sm" fw={500}>
                      Drop a .tex or PDF resume, or click to choose one
                    </Text>
                    <Text size="xs" c="dimmed">
                      Uploading identical content again reuses the existing version. {PDF_ONLY_NOTE}
                    </Text>
                  </div>
                </Group>
              </Dropzone>
            </Stack>
          </Card>

          <FocusCard title="Resume versions" p={0}>
            {resumes.length === 0 ? (
              <Empty
                title="No resumes yet"
                hint="Upload a version, then attach it to applications to see how it performs."
              />
            ) : (
              <Table.ScrollContainer minWidth={0}>
                <Table highlightOnHover>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Version</Table.Th>
                      <Table.Th>Baseline</Table.Th>
                      <Table.Th>Uploaded</Table.Th>
                      <Table.Th ta="right">Words</Table.Th>
                      <Table.Th ta="right">Applications</Table.Th>
                      <Table.Th ta="right">Callback rate</Table.Th>
                      <Table.Th />
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody className="tabular">
                    {resumes.map((resume) => {
                      const performance = overview?.resumes.find((row) => row.resume_id === resume.id);
                      return (
                        <Table.Tr key={resume.id}>
                          <Table.Td>
                            <Text size="md" fw={650} lineClamp={1}>
                              {resume.label}
                            </Text>
                            <Text size="xs" c="dimmed" lineClamp={1}>
                              {resume.filename}
                              {resume.source_format === 'pdf' && ' · PDF, analysis only'}
                            </Text>
                          </Table.Td>
                          <Table.Td style={{ whiteSpace: 'nowrap' }}>
                            <Tooltip
                              label={
                                resume.is_variant ? 'Edit baseline rules' : 'Use as a baseline'
                              }
                            >
                              <Button
                                size="compact-xs"
                                variant={resume.is_variant ? 'light' : 'subtle'}
                                color={resume.is_variant ? 'grape' : 'gray'}
                                miw={92}
                                onClick={() => setEditing(resume)}
                              >
                                {resume.is_variant ? 'Baseline' : 'Make baseline'}
                              </Button>
                            </Tooltip>
                          </Table.Td>
                          <Table.Td c="dimmed">{formatDay(resume.created_at)}</Table.Td>
                          <Table.Td ta="right" c="dimmed">
                            {resume.word_count}
                          </Table.Td>
                          <Table.Td ta="right" fw={600}>
                            {resume.application_count}
                          </Table.Td>
                          <Table.Td ta="right">
                            {performance && performance.applications > 0 ? (
                              <Group gap={6} justify="flex-end" wrap="nowrap">
                                <Text
                                  size="md"
                                  fw={700}
                                  c={performance.callbacks > 0 ? 'teal' : undefined}
                                >
                                  {pct(performance.callback_rate)}
                                </Text>
                                <Text size="xs" c="dimmed">
                                  {performance.callbacks}/{performance.applications}
                                </Text>
                              </Group>
                            ) : (
                              '–'
                            )}
                          </Table.Td>
                          <Table.Td>
                            <Group gap={4} justify="flex-end" wrap="nowrap">
                              <Tooltip
                                label={
                                  resume.source_format === 'pdf'
                                    ? 'View the text read from the PDF'
                                    : 'Preview or view source'
                                }
                              >
                                <ActionIcon
                                  variant="subtle"
                                  color="gray"
                                  aria-label={`View ${resume.label}`}
                                  onClick={() => setPreviewId(resume.id)}
                                >
                                  <IconEye size={16} />
                                </ActionIcon>
                              </Tooltip>
                              <Tooltip
                                label={resume.source_format === 'pdf' ? 'Download PDF' : 'Download .tex'}
                              >
                                <ActionIcon
                                  variant="subtle"
                                  color="gray"
                                  aria-label={`Download ${resume.label}`}
                                  onClick={() => handleDownload(resume)}
                                >
                                  <IconDownload size={16} />
                                </ActionIcon>
                              </Tooltip>
                              <Tooltip
                                label="Linked to applications"
                                disabled={resume.application_count === 0}
                              >
                                <span>
                                  <ConfirmDelete
                                    label="Delete"
                                    message={`Delete ${resume.label}?`}
                                    disabled={resume.application_count > 0}
                                    loading={remove.isPending}
                                    onConfirm={() => remove.mutate(resume.id)}
                                  />
                                </span>
                              </Tooltip>
                            </Group>
                          </Table.Td>
                        </Table.Tr>
                      );
                    })}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            )}
          </FocusCard>
        </>
      )}

      <VariantRulesModal resume={editing} onClose={() => setEditing(null)} />

      <ResumePreviewModal resumeId={previewId} onClose={() => setPreviewId(null)} />
    </Stack>
  );
}

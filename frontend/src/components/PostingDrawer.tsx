import {
  Alert,
  Anchor,
  Badge,
  Button,
  Checkbox,
  Divider,
  Drawer,
  Group,
  Loader,
  NumberInput,
  ScrollArea,
  Select,
  SimpleGrid,
  Stack,
  TagsInput,
  Text,
  Textarea,
  TextInput,
  Title,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { IconExternalLink, IconInfoCircle } from '@tabler/icons-react';
import { useEffect, useState } from 'react';

import { ScoutBadge } from './ScoutBadge';
import { formatDay, pct } from '../lib/format';
import {
  EMPLOYMENT_TYPE_OPTIONS,
  fieldLabel,
  LEVEL_OPTIONS,
  ROLE_FAMILY_OPTIONS,
  SOURCE_KIND_OPTIONS,
  WORK_MODE_OPTIONS,
} from '../lib/postings';
import {
  useDeletePosting,
  useFetchPostingText,
  usePostingDetail,
  useReparsePosting,
  useSavePostingText,
  useUpdatePosting,
} from '../lib/queries';
import type { PostingChanges, PostingDetail } from '../lib/types';
import { ApplySteps, jobFromPosting } from './ApplySteps';
import { ConfirmDelete } from './ConfirmDelete';
import { MatchChecklist, ResumeRecommendation } from './MatchEstimate';
import { PageError, PageLoader } from './PageState';
import { StatusBadge } from './StatusBadge';

interface FormValues {
  company: string;
  title: string;
  url: string;
  location: string;
  source: string;
  seen_on: string;
  level: string | null;
  role_family: string | null;
  years_required: number | string;
  salary_min: number | string;
  salary_max: number | string;
  work_mode: string | null;
  employment_type: string | null;
  source_kind: string | null;
  referred: boolean;
  referrer: string;
  technologies: string[];
}

// As the server allows for a posting's text.
const MAX_TEXT = 50_000;

function toFormValues(posting: PostingDetail): FormValues {
  return {
    company: posting.company,
    title: posting.title,
    url: posting.url ?? '',
    location: posting.location ?? '',
    source: posting.source ?? '',
    seen_on: posting.seen_on,
    level: posting.level,
    role_family: posting.role_family,
    years_required: posting.years_required ?? '',
    salary_min: posting.salary_min ?? '',
    salary_max: posting.salary_max ?? '',
    work_mode: posting.work_mode,
    employment_type: posting.employment_type,
    source_kind: posting.source_kind,
    referred: posting.referred,
    referrer: posting.referrer ?? '',
    technologies: posting.technologies.map((technology) => technology.name),
  };
}

function toNumber(value: number | string): number | null {
  return value === '' ? null : Number(value);
}

function toChanges(values: FormValues): PostingChanges {
  return {
    ...values,
    url: values.url.trim() || null,
    location: values.location.trim() || null,
    source: values.source.trim() || null,
    referrer: values.referrer.trim() || null,
    years_required: toNumber(values.years_required),
    salary_min: toNumber(values.salary_min),
    salary_max: toNumber(values.salary_max),
  };
}

function PostingEditor({ posting, onDeleted }: { posting: PostingDetail; onDeleted: () => void }) {
  const update = useUpdatePosting();
  const reparse = useReparsePosting();
  const remove = useDeletePosting();
  const form = useForm<FormValues>({
    initialValues: toFormValues(posting),
    validate: {
      company: (value) => (value.trim() ? null : 'Company is required'),
      title: (value) => (value.trim() ? null : 'Title is required'),
      seen_on: (value) => (value ? null : 'Pick a date'),
      url: (value) =>
        !value.trim() || /^https?:\/\//i.test(value.trim()) ? null : 'Must start with http(s)://',
    },
  });

  // The posting is fetched again whenever it changes: staged, tailored, parsed again, saved. The
  // form takes the new values only while nothing is being edited, so unsaved edits stay.
  const serverValues = JSON.stringify(toFormValues(posting));
  useEffect(() => {
    if (form.isDirty()) return;
    const values = JSON.parse(serverValues) as FormValues;
    form.setValues(values);
    form.resetDirty(values);
  }, [serverValues, form]);

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Group gap={6}>
          {posting.applications.length ? (
            posting.applications.map((application) => (
              <Group key={application.id} gap={4} wrap="nowrap">
                <StatusBadge status={application.status} />
                <Text size="xs" c="dimmed">
                  {formatDay(application.applied_on)}
                </Text>
              </Group>
            ))
          ) : (
            <Text size="sm" c="dimmed">
              Not applied
            </Text>
          )}
        </Group>
        {posting.url && (
          <Anchor href={posting.url} target="_blank" rel="noopener noreferrer" size="sm">
            <Group gap={4} wrap="nowrap">
              View posting <IconExternalLink size={14} />
            </Group>
          </Anchor>
        )}
      </Group>

      <ApplySteps key={posting.id} job={jobFromPosting(posting)} />

      <ResumeRecommendation match={posting.match} />
      <MatchChecklist match={posting.match} />
      <Divider />

      <form
        onSubmit={form.onSubmit((values) =>
          update.mutate(
            { id: posting.id, changes: toChanges(values) },
            {
              onSuccess: (saved) => {
                // What was saved is now the starting point, so the form is clean again.
                const next = toFormValues(saved);
                form.setValues(next);
                form.resetDirty(next);
                notifications.show({ color: 'green', message: 'Posting updated' });
              },
            },
          ),
        )}
      >
        <Stack gap="sm">
          <SimpleGrid cols={{ base: 1, xs: 2 }} spacing="sm">
            <TextInput label="Company" required {...form.getInputProps('company')} />
            <TextInput label="Title" required {...form.getInputProps('title')} />
            <Select label="Level" data={LEVEL_OPTIONS} clearable {...form.getInputProps('level')} />
            <Select
              label="Role type"
              data={ROLE_FAMILY_OPTIONS}
              clearable
              {...form.getInputProps('role_family')}
            />
            <NumberInput
              label="Years required"
              min={0}
              max={60}
              allowDecimal={false}
              {...form.getInputProps('years_required')}
            />
            <Select
              label="Work mode"
              data={WORK_MODE_OPTIONS}
              clearable
              {...form.getInputProps('work_mode')}
            />
            <NumberInput
              label="Salary minimum"
              min={0}
              thousandSeparator=","
              prefix="$"
              allowDecimal={false}
              {...form.getInputProps('salary_min')}
            />
            <NumberInput
              label="Salary maximum"
              min={0}
              thousandSeparator=","
              prefix="$"
              allowDecimal={false}
              {...form.getInputProps('salary_max')}
            />
            <Select
              label="Job type"
              data={EMPLOYMENT_TYPE_OPTIONS}
              clearable
              {...form.getInputProps('employment_type')}
            />
            <Select
              label="Source type"
              data={SOURCE_KIND_OPTIONS}
              clearable
              {...form.getInputProps('source_kind')}
            />
            <TextInput label="Source" {...form.getInputProps('source')} />
            <TextInput label="Location" {...form.getInputProps('location')} />
            <TextInput label="URL" {...form.getInputProps('url')} />
            <TextInput label="Seen on" type="date" required {...form.getInputProps('seen_on')} />
            <TextInput label="Referred by" {...form.getInputProps('referrer')} />
            <Checkbox
              label="Referred"
              mt="xl"
              {...form.getInputProps('referred', { type: 'checkbox' })}
            />
          </SimpleGrid>
          <TagsInput
            label="Tech stack"
            description="Press Enter to add a technology"
            {...form.getInputProps('technologies')}
          />
          {posting.corrected_fields.length > 0 && (
            <Text size="xs" c="dimmed">
              Your corrections are kept when the parser runs again:{' '}
              {posting.corrected_fields.map((field) => fieldLabel(field)).join(', ')}.
            </Text>
          )}
          <Group justify="flex-end">
            <Button
              variant="default"
              loading={reparse.isPending}
              onClick={() =>
                reparse.mutate(posting.id, {
                  onSuccess: () =>
                    notifications.show({ color: 'green', message: 'Parser ran again' }),
                })
              }
            >
              Run parser again
            </Button>
            <Button type="submit" loading={update.isPending}>
              Save changes
            </Button>
          </Group>
        </Stack>
      </form>

      <Divider />
      <Title order={5}>Resume match</Title>
      {posting.coverage ? (
        <Stack gap={6}>
          <Text size="sm">
            {posting.coverage.required_total
              ? `${posting.coverage.resume_label} names ${posting.coverage.required_matched} of ${posting.coverage.required_total} required technologies.`
              : `This posting lists no required technologies to compare with ${posting.coverage.resume_label}.`}
            {posting.coverage.nice_total > 0 &&
              ` It names ${posting.coverage.nice_matched} of ${posting.coverage.nice_total} nice-to-haves.`}
          </Text>
          {posting.coverage.missing_required.length > 0 && (
            <Group gap={6} align="center">
              <Text size="xs" c="dimmed" w={88}>
                Required, not named
              </Text>
              {posting.coverage.missing_required.map((name) => (
                <Badge key={name} variant="default" tt="none" fw={500}>
                  {name}
                </Badge>
              ))}
            </Group>
          )}
          {posting.coverage.missing_nice.length > 0 && (
            <Group gap={6} align="center">
              <Text size="xs" c="dimmed" w={88}>
                Nice to have, not named
              </Text>
              {posting.coverage.missing_nice.map((name) => (
                <Badge key={name} variant="default" tt="none" fw={500}>
                  {name}
                </Badge>
              ))}
            </Group>
          )}
        </Stack>
      ) : (
        <Text size="sm" c="dimmed">
          Link an application with a resume to compare them.
        </Text>
      )}

      <Divider />
      <Title order={5}>Stack by requirement</Title>
      {posting.skills_updating && (
        <Text size="xs" c="dimmed">
          Updating skills… Your skill terms changed, and this posting is being read again.
        </Text>
      )}
      {posting.technologies.length ? (
        <Stack gap={6}>
          {(['required', 'nice_to_have', 'mentioned'] as const).map((requirement) => {
            const names = posting.technologies.filter(
              (technology) => technology.requirement === requirement,
            );
            return names.length ? (
              <Group key={requirement} gap={6} align="center">
                <Text size="xs" c="dimmed" w={88}>
                  {fieldLabel(requirement)}
                </Text>
                {names.map((technology) => (
                  <Badge key={technology.name} variant="default" tt="none" fw={500}>
                    {technology.name}
                  </Badge>
                ))}
              </Group>
            ) : null;
          })}
        </Stack>
      ) : (
        <Text size="sm" c="dimmed">
          No known technologies found.
        </Text>
      )}

      <Divider />
      <Title order={5}>Closest postings</Title>
      {posting.related.length ? (
        <Stack gap="xs">
          {posting.related.map((related) => (
            <div key={related.id}>
              <Group gap={6}>
                <Text size="sm" fw={500}>
                  {related.company} · {related.title}
                </Text>
                {related.likely_repeat && (
                  <Badge size="xs" color="orange" variant="light">
                    Likely repeat
                  </Badge>
                )}
              </Group>
              <Text size="xs" c="dimmed">
                {fieldLabel(related.level)} · {pct(related.stack_overlap)} requirement overlap
                {related.shared_stack.length ? ` · ${related.shared_stack.join(', ')}` : ''}
              </Text>
            </div>
          ))}
        </Stack>
      ) : (
        <Text size="sm" c="dimmed">
          Add more postings to compare this one.
        </Text>
      )}

      <Divider />
      <Title order={5}>Posting text</Title>
      {posting.raw_text ? (
        <ScrollArea.Autosize mah={320}>
          <Text size="xs" style={{ whiteSpace: 'pre-wrap' }}>
            {posting.raw_text}
          </Text>
        </ScrollArea.Autosize>
      ) : (
        <MissingText posting={posting} />
      )}

      <Divider />
      <Group justify="flex-end">
        <ConfirmDelete
          label="Delete posting"
          message="Linked applications stay, but they lose their link to this posting."
          loading={remove.isPending}
          onConfirm={() => remove.mutate(posting.id, { onSuccess: onDeleted })}
        />
      </Group>
    </Stack>
  );
}

/** A posting with no text: the read from its link, and a box to paste the text instead. */
function MissingText({ posting }: { posting: PostingDetail }) {
  const fetchText = useFetchPostingText();
  const saveText = useSavePostingText();
  const [pasted, setPasted] = useState('');

  return (
    <Stack gap="sm">
      {posting.text_status === 'pending' && (
        <Group gap="xs">
          <Loader size="xs" />
          <Text size="sm" c="dimmed">
            Fetching the posting's text…
          </Text>
        </Group>
      )}
      {posting.text_status === 'failed' && (
        <Alert color="yellow" variant="light" icon={<IconInfoCircle size={16} />}>
          <Stack gap="xs" align="flex-start">
            <Text size="sm">
              Couldn't get the posting's text from its link.{' '}
              {posting.text_error ?? "The page couldn't be read."} Pasting the text below works
              too.
            </Text>
            <Button
              size="xs"
              variant="default"
              loading={fetchText.isPending}
              onClick={() => fetchText.mutate(posting.id)}
            >
              Try again
            </Button>
          </Stack>
        </Alert>
      )}
      <Textarea
        label="Paste the posting's text"
        description="Used to match the posting with your resumes, for tailoring and for a rejection's report."
        autosize
        minRows={3}
        maxRows={12}
        maxLength={MAX_TEXT}
        value={pasted}
        onChange={(event) => setPasted(event.currentTarget.value)}
      />
      <Group justify="flex-end">
        <Button
          disabled={!pasted.trim()}
          loading={saveText.isPending}
          onClick={() =>
            saveText.mutate(
              { id: posting.id, text: pasted.trim() },
              {
                onSuccess: () => {
                  setPasted('');
                  notifications.show({ color: 'green', message: 'Posting text saved' });
                },
              },
            )
          }
        >
          Save text
        </Button>
      </Group>
    </Stack>
  );
}

export function PostingDrawer({ postingId, onClose }: { postingId: string | null; onClose: () => void }) {
  const detail = usePostingDetail(postingId);

  return (
    <Drawer
      opened={postingId !== null}
      onClose={onClose}
      position="right"
      size="lg"
      title={
        detail.data ? (
          <Group gap="xs" wrap="nowrap">
            <span>{`${detail.data.company} · ${detail.data.title}`}</span>
            {detail.data.from_scout && <ScoutBadge />}
          </Group>
        ) : (
          'Posting'
        )
      }
    >
      {detail.isPending && <PageLoader />}
      {detail.isError && <PageError />}
      {detail.data && (
        <PostingEditor
          // Keyed on the posting alone: a refetch after staging or tailoring mustn't remount the
          // editor and throw away what's being typed.
          key={detail.data.id}
          posting={detail.data}
          onDeleted={onClose}
        />
      )}
    </Drawer>
  );
}

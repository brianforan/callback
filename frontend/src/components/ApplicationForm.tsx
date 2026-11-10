import {
  Autocomplete,
  Button,
  Group,
  Select,
  SimpleGrid,
  Stack,
  Textarea,
  TextInput,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';

import { todayIso } from '../lib/format';
import { useCreateApplication, usePostings, useResumes, useUpdateApplication } from '../lib/queries';
import { SOURCE_SUGGESTIONS, STATUS_OPTIONS } from '../lib/statuses';
import type { Application, ApplicationPayload, ApplicationStatus } from '../lib/types';

interface FormValues {
  company: string;
  role_title: string;
  job_url: string;
  location: string;
  source: string;
  status: ApplicationStatus;
  applied_on: string;
  resume_id: string | null;
  posting_id: string | null;
  notes: string;
  posting_text: string;
  referrer: string;
}

function toFormValues(application?: Application): FormValues {
  return {
    company: application?.company ?? '',
    role_title: application?.role_title ?? '',
    job_url: application?.job_url ?? '',
    location: application?.location ?? '',
    source: application?.source ?? '',
    status: application?.status ?? 'applied',
    applied_on: application?.applied_on ?? todayIso(),
    resume_id: application?.resume_id ?? null,
    posting_id: application?.posting_id ?? null,
    notes: application?.notes ?? '',
    posting_text: '',
    referrer: '',
  };
}

function toPayload(values: FormValues, isNew: boolean): ApplicationPayload {
  const { posting_text, referrer, ...fields } = values;
  const payload: ApplicationPayload = {
    ...fields,
    job_url: values.job_url.trim() || null,
    location: values.location.trim() || null,
    source: values.source.trim() || null,
    notes: values.notes.trim() || null,
  };
  // The API only takes posting text and a referrer when it creates the application.
  return isNew
    ? { ...payload, posting_text: posting_text.trim() || null, referrer: referrer.trim() || null }
    : payload;
}

interface Props {
  application?: Application;
  onDone?: () => void;
}

export function ApplicationForm({ application, onDone }: Props) {
  const { data: resumes = [] } = useResumes();
  const { data: postings = [] } = usePostings();
  const create = useCreateApplication();
  const update = useUpdateApplication();

  const form = useForm<FormValues>({
    initialValues: toFormValues(application),
    validate: {
      company: (value) => (value.trim() ? null : 'Company is required'),
      role_title: (value) => (value.trim() ? null : 'Role is required'),
      applied_on: (value) => (value ? null : 'Pick a date'),
      job_url: (value) =>
        !value.trim() || /^https?:\/\//i.test(value.trim()) ? null : 'Must start with http(s)://',
    },
  });

  function handleSubmit(values: FormValues) {
    const payload = toPayload(values, !application);
    const onSuccess = () => {
      notifications.show({
        color: 'green',
        message: application ? 'Application updated' : `Logged ${payload.company}`,
      });
      onDone?.();
    };
    if (application) update.mutate({ id: application.id, changes: payload }, { onSuccess });
    else create.mutate(payload, { onSuccess });
  }

  return (
    <form onSubmit={form.onSubmit(handleSubmit)}>
      <Stack gap="sm">
        <SimpleGrid cols={{ base: 1, xs: 2 }} spacing="sm">
          <TextInput label="Company" required data-autofocus {...form.getInputProps('company')} />
          <TextInput label="Role" required {...form.getInputProps('role_title')} />
          <Select
            label="Status"
            data={STATUS_OPTIONS}
            allowDeselect={false}
            {...form.getInputProps('status')}
          />
          <TextInput label="Applied on" type="date" required {...form.getInputProps('applied_on')} />
          <Select
            label="Resume"
            placeholder={resumes.length ? 'Choose a version' : 'Upload one under Resumes'}
            data={resumes.map((resume) => ({ value: resume.id, label: resume.label }))}
            clearable
            searchable
            {...form.getInputProps('resume_id')}
          />
          <Autocomplete
            label="Source"
            placeholder="Where you found it"
            data={SOURCE_SUGGESTIONS}
            {...form.getInputProps('source')}
          />
          <TextInput label="Location" placeholder="Remote, NYC…" {...form.getInputProps('location')} />
          <TextInput label="Job posting URL" placeholder="https://" {...form.getInputProps('job_url')} />
          <Select
            label="Saved posting"
            placeholder={application ? 'Not linked' : 'Creates a new one if empty'}
            data={postings.map((posting) => ({
              value: posting.id,
              label: `${posting.company} · ${posting.title}`,
            }))}
            clearable
            searchable
            {...form.getInputProps('posting_id')}
          />
          {!application && <TextInput label="Referred by" {...form.getInputProps('referrer')} />}
        </SimpleGrid>
        {!application && (
          <Textarea
            label="Posting text"
            description="Paste the full posting to fill in its level, stack and salary."
            autosize
            minRows={2}
            maxRows={8}
            {...form.getInputProps('posting_text')}
          />
        )}
        <Textarea label="Notes" autosize minRows={2} maxRows={8} {...form.getInputProps('notes')} />
        <Group justify="flex-end">
          <Button type="submit" loading={create.isPending || update.isPending}>
            {application ? 'Save changes' : 'Log application'}
          </Button>
        </Group>
      </Stack>
    </form>
  );
}

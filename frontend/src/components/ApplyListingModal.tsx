import {
  Anchor,
  Button,
  FileInput,
  Group,
  Modal,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Text,
  Textarea,
  TextInput,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { IconExternalLink, IconFileUpload } from '@tabler/icons-react';

import { formatDay, todayIso } from '../lib/format';
import {
  useApplications,
  useCreateApplication,
  useCreatePosting,
  usePostings,
  useResumes,
  useUpdateApplication,
  useUploadResume,
} from '../lib/queries';
import { RESUME_ACCEPT_INPUT, resumeFileProblem } from '../lib/resumeFiles';
import { SOURCE_SUGGESTIONS, STATUS_OPTIONS } from '../lib/statuses';
import type { Application, ApplicationStatus, Listing, Posting, Resume } from '../lib/types';
import { PageError, PageLoader } from './PageState';


interface FormValues {
  target: 'new' | 'existing';
  application_id: string | null;
  company: string;
  role_title: string;
  job_url: string;
  location: string;
  source: string;
  status: ApplicationStatus;
  applied_on: string;
  resume_mode: 'saved' | 'upload';
  resume_id: string | null;
  resume_file: File | null;
  resume_label: string;
  referrer: string;
  notes: string;
}

function blankToNull(value: string): string | null {
  return value.trim() || null;
}

// The same rule as the server's company_key, so "Acme, Inc." and "ACME" count as one company.
const COMPANY_SUFFIX =
  /\b(?:inc|incorporated|llc|ltd|limited|corp|corporation|co|company|gmbh|plc)\b\.?\s*$/;

function words(text: string): string {
  return text.split(/\s+/).filter(Boolean).join(' ');
}

function companyKey(name: string): string {
  const key = words(name.toLowerCase().replace(/[^a-z0-9&+ ]+/g, ' '));
  return words(key.replace(COMPANY_SUFFIX, '')) || key;
}

function ApplyForm({
  listing,
  resumes,
  applications,
  postings,
  onDone,
}: {
  listing: Listing;
  resumes: Resume[];
  applications: Application[];
  postings: Posting[];
  onDone: () => void;
}) {
  const upload = useUploadResume();
  const create = useCreateApplication();
  const createPosting = useCreatePosting();
  const update = useUpdateApplication();
  // A staged job already has its resume chosen, so that comes first.
  const suggested =
    listing.staged_resume_id ?? listing.match?.best_resume.id ?? resumes[0]?.id ?? null;

  // Logging an application always makes a posting, but one with no link or text is only a
  // placeholder. The server removes it on relinking, so only a real posting is worth a warning.
  const placeholders = new Set(
    postings.filter((posting) => posting.placeholder).map((posting) => posting.id),
  );
  const hasOtherPosting = (application: Application) =>
    application.posting_id !== null && !placeholders.has(application.posting_id);
  const applicationLabel = (application: Application) => {
    const label = `${application.company} · ${application.role_title} · ${formatDay(application.applied_on)}`;
    return hasOtherPosting(application) ? `${label} (linked to another posting)` : label;
  };

  // Applications already linked to this listing's posting are in sync, so they aren't offered.
  const linkable = applications
    .filter((application) => !listing.posting_id || application.posting_id !== listing.posting_id)
    .sort((a, b) => b.applied_on.localeCompare(a.applied_on));
  const sameCompany = linkable.filter(
    (application) => companyKey(application.company) === companyKey(listing.company),
  );
  const others = linkable.filter((application) => !sameCompany.includes(application));
  // An application at the same company with no real posting yet is most likely this job, logged
  // by hand.
  const likely =
    sameCompany.find((application) => !hasOtherPosting(application)) ?? sameCompany[0] ?? null;

  const form = useForm<FormValues>({
    initialValues: {
      target: likely ? 'existing' : 'new',
      application_id: likely?.id ?? null,
      company: listing.company,
      role_title: listing.title,
      job_url: listing.url ?? '',
      location: listing.location ?? '',
      source: listing.source,
      status: 'applied',
      applied_on: todayIso(),
      resume_mode: resumes.length ? 'saved' : 'upload',
      resume_id: likely?.resume_id ?? suggested,
      resume_file: null,
      resume_label: '',
      referrer: '',
      notes: '',
    },
    validate: {
      application_id: (value, values) =>
        values.target === 'existing' && !value ? 'Pick the application' : null,
      company: (value, values) =>
        values.target === 'new' && !value.trim() ? 'Company is required' : null,
      role_title: (value, values) =>
        values.target === 'new' && !value.trim() ? 'Role is required' : null,
      applied_on: (value, values) => (values.target === 'new' && !value ? 'Pick a date' : null),
      job_url: (value, values) =>
        values.target === 'existing' || !value.trim() || /^https?:\/\//i.test(value.trim())
          ? null
          : 'Must start with http(s)://',
      resume_file: (file, values) => {
        if (values.resume_mode !== 'upload' || !file) return null;
        return resumeFileProblem(file);
      },
    },
  });

  const linking = form.values.target === 'existing';
  const chosen = applications.find((application) => application.id === form.values.application_id);
  const busy = upload.isPending || create.isPending || createPosting.isPending || update.isPending;

  function chooseApplication(id: string | null) {
    form.setFieldValue('application_id', id);
    const application = applications.find((candidate) => candidate.id === id);
    if (application?.resume_id) {
      form.setFieldValue('resume_mode', 'saved');
      form.setFieldValue('resume_id', application.resume_id);
    }
  }

  async function resolveResume(values: FormValues): Promise<string | null> {
    if (values.resume_mode === 'saved') return values.resume_id;
    if (!values.resume_file) return null;
    // An identical file comes back as the resume already stored, so nothing is duplicated.
    const resume = await upload.mutateAsync({
      file: values.resume_file,
      label: values.resume_label,
    });
    return resume.id;
  }

  async function logNew(values: FormValues) {
    await create.mutateAsync({
      company: values.company.trim(),
      role_title: values.role_title.trim(),
      job_url: blankToNull(values.job_url),
      location: blankToNull(values.location),
      source: blankToNull(values.source),
      status: values.status,
      applied_on: values.applied_on,
      resume_id: await resolveResume(values),
      // A tracked listing already has a posting. Otherwise its text becomes a new one.
      posting_id: listing.posting_id,
      posting_text: listing.posting_id ? null : listing.description || null,
      referrer: blankToNull(values.referrer),
      notes: blankToNull(values.notes),
    });
  }

  async function linkExisting(application: Application, values: FormValues) {
    const resumeId = await resolveResume(values);
    let postingId = listing.posting_id;
    if (!postingId) {
      const posting = await createPosting.mutateAsync({
        company: listing.company,
        title: listing.title,
        url: listing.url,
        location: listing.location,
        source: listing.source,
        raw_text: listing.description || null,
        referrer: null,
        // The listing was seen by the day of applying, so the posting is dated to match.
        seen_on: application.applied_on,
      });
      postingId = posting.id;
    }
    await update.mutateAsync({
      id: application.id,
      changes: {
        posting_id: postingId,
        resume_id: resumeId,
        // A link already on the application is kept. One left blank takes the listing's.
        ...(application.job_url ? {} : { job_url: listing.url }),
      },
    });
  }

  async function submit(values: FormValues) {
    try {
      if (values.target === 'existing' && chosen) await linkExisting(chosen, values);
      else await logNew(values);
    } catch {
      // The query client already shows the error, and the form stays open to try again.
      return;
    }
    notifications.show({
      color: 'green',
      message:
        values.target === 'existing' && chosen
          ? `Linked the listing to your ${chosen.company} application`
          : `Logged ${values.company.trim()}`,
    });
    onDone();
  }

  return (
    <form onSubmit={form.onSubmit(submit)}>
      <Stack gap="sm">
        <Group justify="space-between" gap="xs">
          <SegmentedControl
            size="xs"
            data={[
              { value: 'new', label: 'New application' },
              { value: 'existing', label: 'Already logged', disabled: !linkable.length },
            ]}
            {...form.getInputProps('target')}
          />
          {listing.url && (
            <Anchor href={listing.url} target="_blank" rel="noopener noreferrer" size="sm">
              <Group gap={4} wrap="nowrap">
                Open the listing <IconExternalLink size={14} />
              </Group>
            </Anchor>
          )}
        </Group>

        {linking ? (
          <Stack gap={6}>
            <Text size="sm" c="dimmed">
              Links this listing to an application you already logged, so its posting, stack and
              match feed that application. The application keeps its own details.
            </Text>
            <Select
              label="Application"
              placeholder="Pick the application"
              data={[
                {
                  group: `At ${listing.company}`,
                  items: sameCompany.map((application) => ({
                    value: application.id,
                    label: applicationLabel(application),
                  })),
                },
                {
                  group: 'Other applications',
                  items: others.map((application) => ({
                    value: application.id,
                    label: applicationLabel(application),
                  })),
                },
              ].filter((group) => group.items.length)}
              searchable
              allowDeselect={false}
              error={form.errors.application_id}
              value={form.values.application_id}
              onChange={chooseApplication}
              description={
                chosen && hasOtherPosting(chosen)
                  ? 'This replaces its link to the other posting, which stays saved.'
                  : undefined
              }
            />
          </Stack>
        ) : (
          <>
            <Text size="sm" c="dimmed">
              Details come from the listing. Change anything that differs.
            </Text>
            <SimpleGrid cols={{ base: 1, xs: 2 }} spacing="sm">
              <TextInput label="Company" required {...form.getInputProps('company')} />
              <TextInput label="Role" required {...form.getInputProps('role_title')} />
              <Select
                label="Status"
                data={STATUS_OPTIONS}
                allowDeselect={false}
                {...form.getInputProps('status')}
              />
              <TextInput
                label="Applied on"
                type="date"
                required
                {...form.getInputProps('applied_on')}
              />
              <TextInput label="Location" {...form.getInputProps('location')} />
              <Select
                label="Source"
                data={[...new Set([listing.source, ...SOURCE_SUGGESTIONS])]}
                searchable
                {...form.getInputProps('source')}
              />
              <TextInput label="Job posting URL" {...form.getInputProps('job_url')} />
              <TextInput label="Referred by" {...form.getInputProps('referrer')} />
            </SimpleGrid>
          </>
        )}

        <Stack gap={6}>
          <Group justify="space-between" align="flex-end">
            <Text size="sm" fw={500}>
              Resume you sent
            </Text>
            <SegmentedControl
              size="xs"
              data={[
                { value: 'saved', label: 'Saved resume', disabled: !resumes.length },
                { value: 'upload', label: 'Upload file' },
              ]}
              {...form.getInputProps('resume_mode')}
            />
          </Group>
          {form.values.resume_mode === 'saved' ? (
            <Select
              aria-label="Saved resume"
              data={resumes.map((resume) => ({ value: resume.id, label: resume.label }))}
              placeholder="No resume"
              description={
                linking && chosen?.resume_id && form.values.resume_id === chosen.resume_id
                  ? 'The resume already on this application.'
                  : listing.match && form.values.resume_id === listing.match.best_resume.id
                    ? 'Picked because it covers the most of this job’s stack.'
                    : undefined
              }
              clearable
              searchable
              {...form.getInputProps('resume_id')}
            />
          ) : (
            <SimpleGrid cols={{ base: 1, xs: 2 }} spacing="sm">
              <FileInput
                aria-label="Resume file"
                placeholder="Choose a .tex or .pdf file"
                accept={RESUME_ACCEPT_INPUT}
                clearable
                leftSection={<IconFileUpload size={14} />}
                {...form.getInputProps('resume_file')}
              />
              <TextInput
                aria-label="Resume name"
                placeholder="Name, such as Backend v3 (optional)"
                maxLength={120}
                {...form.getInputProps('resume_label')}
              />
            </SimpleGrid>
          )}
        </Stack>

        {!linking && (
          <Textarea
            label="Notes"
            autosize
            minRows={2}
            maxRows={6}
            {...form.getInputProps('notes')}
          />
        )}
        <Group justify="flex-end">
          <Button type="submit" loading={busy}>
            {linking ? 'Link application' : 'Log application'}
          </Button>
        </Group>
      </Stack>
    </form>
  );
}

export function ApplyListingModal({
  listing,
  onClose,
}: {
  listing: Listing | null;
  onClose: () => void;
}) {
  const resumes = useResumes();
  const applications = useApplications();
  const postings = usePostings();
  const pending = resumes.isPending || applications.isPending || postings.isPending;
  return (
    <Modal
      opened={listing !== null}
      onClose={onClose}
      title={listing ? `I applied: ${listing.company} · ${listing.title}` : 'I applied'}
      size="lg"
    >
      {/* The form reads these once, to pick its defaults, so it waits for all of them. */}
      {listing && pending && <PageLoader />}
      {listing && (resumes.isError || applications.isError || postings.isError) && <PageError />}
      {listing && resumes.data && applications.data && postings.data && (
        <ApplyForm
          key={listing.id}
          listing={listing}
          resumes={resumes.data}
          applications={applications.data}
          postings={postings.data}
          onDone={onClose}
        />
      )}
    </Modal>
  );
}

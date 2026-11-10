import {
  Alert,
  Anchor,
  Button,
  Chip,
  Grid,
  Group,
  NumberInput,
  Paper,
  Progress,
  SegmentedControl,
  Stack,
  TagsInput,
  Text,
  Textarea,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconCheck, IconEye, IconLock, IconX } from '@tabler/icons-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { PageError, PageLoader } from '../components/PageState';
import { RunButton, runsLeft, runsLeftText, ScoutRuns } from '../components/ScoutRuns';
import { formatDateTime, formatUsd } from '../lib/format';
import { LEVELS, WORK_MODE_OPTIONS } from '../lib/postings';
import { useFactSheet, useResumes, useSaveScout, useScout } from '../lib/queries';
import { SECTIONS } from '../lib/sections';
import type {
  AgencyMode,
  FitMode,
  PostingLevel,
  Relocation,
  Scout,
  ScoutLimitRange,
  ScoutSettingsPayload,
  TitleFamily,
  WorkMode,
} from '../lib/types';

const COLOR = SECTIONS.scout.color;
// Matches the server's limit on a location.
const LOCATION_MAX_LENGTH = 120;

const TITLE_FAMILIES: { value: TitleFamily; label: string }[] = [
  { value: 'forward_deployed', label: 'Forward deployed engineer' },
  { value: 'solutions', label: 'Solutions engineer' },
  { value: 'implementation', label: 'Implementation engineer' },
  { value: 'internal_tools', label: 'Internal tools' },
  { value: 'business_systems', label: 'Business systems' },
  { value: 'automation', label: 'Automation engineer' },
  { value: 'integration', label: 'Integration engineer' },
];

// Scout's one engine, as the server stores it. Its ranges and past runs are keyed so.
const ENGINE = 'companies';

function rangeFor(config: Scout, key: string): ScoutLimitRange {
  const base = config.limits.find((limit) => limit.key === key);
  if (!base) throw new Error(`No Scout limit named ${key}`);
  return base.by_engine?.[ENGINE] ?? base;
}

// The limits the form shows, in this order. Every other limit keeps its saved value.
const SHOWN_LIMITS = ['target_leads', 'max_pages', 'max_searches', 'max_minutes'];

const LEVEL_LABELS: Record<PostingLevel, string> = {
  intern: 'Intern',
  junior: 'Junior',
  mid: 'Mid',
  senior: 'Senior',
  staff: 'Staff',
  principal: 'Principal',
  manager: 'Manager',
};

// Names that are also everyday words, matched only as written ("Go", not "go to").
const EVERYDAY_WORDS = new Set([
  'go',
  'rest',
  'git',
  'spring',
  'express',
  'lambda',
  'helm',
  'spark',
  'swift',
  'rust',
  'elm',
  'node',
  'oracle',
  'serverless',
  'java',
]);

/** Technologies the description names, by the same names the server knows, in order. */
function namedIn(description: string, names: string[]): string[] {
  const found = names
    .map((name) => {
      const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const flags = EVERYDAY_WORDS.has(name.toLowerCase()) ? '' : 'i';
      const match = new RegExp(`(^|[^\\w+#.])${escaped}(?![\\w+#])`, flags).exec(description);
      return match ? { name, at: match.index } : null;
    })
    .filter((item): item is { name: string; at: number } => item !== null);
  return found.sort((a, b) => a.at - b.at).map((item) => item.name);
}

// The words that rule a technology out in a description, as the server reads them: "NO MULESOFT".
const NEGATION = /\b(?:no|not|without|avoid(?:ing)?|except|exclud(?:e|ing)|never)\b[\s:]+([^.,;:!?\n()]{1,40})/gi;

/** Technologies the description rules out, so they're never suggested for Must use. */
function ruledOutIn(description: string, names: string[]): Set<string> {
  const ruled = new Set<string>();
  for (const match of description.matchAll(NEGATION)) {
    for (const name of namedIn(match[1], names)) ruled.add(name);
  }
  return ruled;
}

const FITS: { value: FitMode; label: string; hint: string }[] = [
  {
    value: 'wide',
    label: 'Wide net',
    hint: 'Keeps stretches: any job whose work matches, even if you meet only some of what it asks.',
  },
  { value: 'balanced', label: 'Balanced', hint: 'Keeps jobs you meet most of. A gap or two is fine.' },
  { value: 'close', label: 'Close fits', hint: 'Keeps only jobs you meet nearly everything for.' },
];

const RELOCATIONS: { value: Relocation; label: string; hint: string }[] = [
  {
    value: 'none',
    label: 'No',
    hint: 'Onsite and hybrid jobs must be within commuting distance of your location.',
  },
  {
    value: 'metro',
    label: 'To a major metro',
    hint: 'Onsite and hybrid jobs in about fifty large US metro areas and their suburbs count too, such as Pittsburgh, Denver or Atlanta, but not small towns.',
  },
  {
    value: 'anywhere',
    label: 'Anywhere in the US',
    hint: 'Onsite and hybrid jobs anywhere in the US count too.',
  },
];

// "Find the employer" belonged to the retired agent. Settings saved with it keep agency listings.
const AGENCY_MODES: { value: AgencyMode; label: string; hint: string }[] = [
  {
    value: 'include',
    label: 'Keep them',
    hint: 'Keeps jobs posted by staffing and recruiting agencies, listed apart from the rest.',
  },
  { value: 'skip', label: 'Skip them', hint: 'Keeps only jobs the employer posted itself.' },
];

const DESCRIPTION_PLACEHOLDER =
  'e.g. Engineering roles where I work with operations or finance teams to automate how they ' +
  'work. Laravel, remote, small to mid-size companies.';

function toPayload(settings: ScoutSettingsPayload): ScoutSettingsPayload {
  return {
    description: settings.description,
    title_families: settings.title_families,
    work_modes: settings.work_modes,
    location: settings.location,
    posted_within_days: settings.posted_within_days,
    fit_mode: settings.fit_mode,
    engine: ENGINE,
    agency_mode: settings.agency_mode === 'find_employer' ? 'include' : settings.agency_mode,
    relocation: settings.relocation,
    must_have: settings.must_have ?? [],
    rule_out: settings.rule_out ?? [],
    levels: settings.levels ?? [],
    limits: settings.limits,
  };
}

function defaultPayload(config: Scout): ScoutSettingsPayload {
  return {
    description: '',
    title_families: [],
    work_modes: [],
    location: '',
    posted_within_days: null,
    fit_mode: 'balanced',
    engine: ENGINE,
    agency_mode: 'include',
    relocation: 'none',
    must_have: [],
    rule_out: [],
    levels: [],
    limits: Object.fromEntries(
      config.limits.map((limit) => [limit.key, rangeFor(config, limit.key).default]),
    ),
  };
}

function samePayload(a: ScoutSettingsPayload, b: ScoutSettingsPayload): boolean {
  return JSON.stringify(toPayload(a)) === JSON.stringify(toPayload(b));
}

function TodayCard({
  config,
  draft,
  locked,
  onLimitChange,
}: {
  config: Scout;
  draft: ScoutSettingsPayload;
  locked: boolean;
  onLimitChange: (key: string, value: number) => void;
}) {
  const { runs_today: used, daily_runs: cap, daily_briefs: briefCap } = config.allowance;
  const left = runsLeft(config);
  const briefsLeft =
    briefCap === null ? null : Math.max(briefCap - config.allowance.briefs_today, 0);
  const past = config.history?.[ENGINE];

  return (
    <Paper withBorder p="md" radius="md">
      <Stack gap="sm">
        <Group justify="space-between">
          <Title order={5}>Today</Title>
          <Text size="xs" c="dimmed">
            Resets at midnight UTC
          </Text>
        </Group>
        <Stack gap={4}>
          <Text size="sm" fw={left === 0 ? 700 : undefined}>
            {runsLeftText(config)}
          </Text>
          {cap !== null && cap > 0 && (
            <Progress
              size="lg"
              value={Math.min(100, (used / cap) * 100)}
              color={COLOR}
              aria-label="Runs used today"
            />
          )}
          <Text size="xs" c="dimmed">
            {briefsLeft === null
              ? 'No daily limit on briefs.'
              : `${briefsLeft} of ${briefCap} ${briefCap === 1 ? 'brief' : 'briefs'} left today.`}{' '}
            A run that fails doesn&rsquo;t count. Every run stops before it spends{' '}
            {formatUsd(config.allowance.run_max_usd)}.
          </Text>
        </Stack>

        <Title order={6}>Where a run stops</Title>
        {SHOWN_LIMITS.map((key) => {
          const range = rangeFor(config, key);
          const unit = config.limits.find((limit) => limit.key === key)?.unit ?? '';
          return (
            <NumberInput
              key={key}
              label={range.label}
              description={`${range.help} From ${range.minimum} to ${range.maximum}.`}
              min={range.minimum}
              max={range.maximum}
              clampBehavior="strict"
              allowDecimal={false}
              value={draft.limits[key] ?? range.default}
              onChange={(value) => typeof value === 'number' && onLimitChange(key, value)}
              disabled={locked}
              suffix={` ${unit}`}
            />
          );
        })}

        {past && past.runs > 0 && (
          <Text size="xs" c="dimmed">
            Your {past.runs} finished {past.runs === 1 ? 'run' : 'runs'} cost{' '}
            {formatUsd(past.cost_usd / past.runs)} on average
            {past.leads > 0 ? `, ${formatUsd(past.cost_usd / past.leads)} a lead` : ''}, measured.
          </Text>
        )}
      </Stack>
    </Paper>
  );
}

function ReadsCard() {
  const factSheet = useFactSheet();
  const resumes = useResumes();
  const hasFactSheet = Boolean(factSheet.data?.content.trim());
  const baselines = resumes.data?.filter((resume) => resume.is_variant).length ?? 0;

  function line(ready: boolean, text: string, to: string, linkLabel: string) {
    return (
      <Group gap="xs" wrap="nowrap" align="flex-start">
        {/* Icons keep their size beside text long enough to wrap. */}
        {ready ? (
          <IconCheck size={16} color="var(--mantine-color-green-6)" style={{ flexShrink: 0 }} />
        ) : (
          <IconX size={16} color="var(--mantine-color-red-6)" style={{ flexShrink: 0 }} />
        )}
        <Text size="sm">
          {text}{' '}
          <Anchor component={Link} to={to} size="sm">
            {linkLabel}
          </Anchor>
        </Text>
      </Group>
    );
  }

  return (
    <Paper withBorder p="md" radius="md">
      <Stack gap="sm">
        <Title order={5}>What it reads</Title>
        {line(
          hasFactSheet,
          hasFactSheet ? 'Your fact sheet, for what you can claim.' : 'No fact sheet yet.',
          '/resumes/fact-sheet',
          hasFactSheet ? 'Edit' : 'Write one',
        )}
        {line(
          baselines > 0,
          baselines > 0
            ? `${baselines} ${baselines === 1 ? 'baseline' : 'baselines'}, for your experience.`
            : 'No baselines yet.',
          '/resumes',
          'Resumes',
        )}
        <Group gap="xs" wrap="nowrap" align="flex-start">
          <IconEye size={16} style={{ flexShrink: 0 }} />
          <Text size="xs" c="dimmed">
            Read-only. It reads job boards and companies&rsquo; own hiring pages, and searches the
            web for companies. It has no way to apply, sign in, fill in forms or send anything.
            Leads come back to you to review.
          </Text>
        </Group>
      </Stack>
    </Paper>
  );
}

function ScoutForm({ config }: { config: Scout }) {
  const save = useSaveScout();
  const saved = toPayload(config.settings);
  // Null until the first change, so the saved settings show without being copied into state.
  const [edited, setEdited] = useState<ScoutSettingsPayload | null>(null);
  const draft = edited ?? saved;
  const dirty = edited !== null && !samePayload(edited, saved);
  const locked = config.plan !== 'premium';

  function update(changes: Partial<ScoutSettingsPayload>) {
    setEdited({ ...draft, ...changes });
  }

  function handleSave() {
    save.mutate(draft, {
      onSuccess: () => {
        setEdited(null);
        notifications.show({ color: 'green', message: 'Scout settings saved' });
      },
    });
  }

  const fitHint = FITS.find((fit) => fit.value === draft.fit_mode)?.hint;
  const names = config.technology_names ?? [];
  const ruledInText = ruledOutIn(draft.description, names);
  const suggested = namedIn(draft.description, names).filter(
    (name) =>
      !ruledInText.has(name) && !draft.must_have.includes(name) && !draft.rule_out.includes(name),
  );
  const agencyHint = AGENCY_MODES.find((mode) => mode.value === draft.agency_mode)?.hint;
  const relocationHint = RELOCATIONS.find((option) => option.value === draft.relocation)?.hint;
  const windowOptions = [
    ...config.posted_within_options.map((days) => ({
      value: String(days),
      label: `${days} days`,
    })),
    { value: 'any', label: 'Any time' },
  ];

  return (
    <Grid gutter="md">
      <Grid.Col span={{ base: 12, md: 7 }}>
        <Stack gap="md">
          <Paper withBorder p="md" radius="md">
            <Stack gap="md">
              <Title order={5}>What you're looking for</Title>
              <Textarea
                label="Describe the work"
                description="In your own words. It's read with your fact sheet and baselines."
                placeholder={DESCRIPTION_PLACEHOLDER}
                autosize
                minRows={3}
                maxRows={8}
                maxLength={config.description_max_length}
                value={draft.description}
                onChange={(event) => update({ description: event.currentTarget.value })}
                disabled={locked}
              />
              <Text size="xs" c="dimmed" ta="right" mt={-12} className="tabular">
                {draft.description.length} / {config.description_max_length}
              </Text>

              <Stack gap={6}>
                <TagsInput
                  label="Must use"
                  description="Jobs must use one of these in their own work: named in the title or the requirements, not only as a nice-to-have. Leave empty for no stack rule."
                  placeholder={draft.must_have.length ? '' : 'e.g. Laravel'}
                  data={config.technology_names ?? []}
                  value={draft.must_have}
                  onChange={(values) => update({ must_have: values })}
                  maxTags={12}
                  clearable
                  disabled={locked}
                />
                {suggested.length > 0 && !locked && (
                  <Group gap={6}>
                    <Text size="xs" c="dimmed">
                      Your description names {suggested.join(', ')}.
                    </Text>
                    <Button
                      size="compact-xs"
                      variant="light"
                      color={COLOR}
                      onClick={() => update({ must_have: [...draft.must_have, ...suggested] })}
                    >
                      Add to Must use
                    </Button>
                  </Group>
                )}
              </Stack>

              <TagsInput
                label="Rule out"
                description="Jobs that require any of these are left out."
                placeholder={draft.rule_out.length ? '' : 'e.g. MuleSoft'}
                data={config.technology_names ?? []}
                value={draft.rule_out}
                onChange={(values) => update({ rule_out: values })}
                maxTags={12}
                clearable
                disabled={locked}
              />

              <Stack gap={6}>
                <Text size="sm" fw={500}>
                  Levels
                </Text>
                <Text size="xs" c="dimmed">
                  One level either side of what you pick is kept as a stretch. Pick none for any
                  level except intern and manager.
                </Text>
                <Chip.Group
                  multiple
                  value={draft.levels}
                  onChange={(values) => update({ levels: values as PostingLevel[] })}
                >
                  <Group gap={6}>
                    {LEVELS.map((level) => (
                      <Chip
                        key={level}
                        value={level}
                        size="sm"
                        color={COLOR}
                        variant="light"
                        disabled={locked}
                      >
                        {LEVEL_LABELS[level]}
                      </Chip>
                    ))}
                  </Group>
                </Chip.Group>
              </Stack>

              <Stack gap={6}>
                <Text size="sm" fw={500}>
                  Titles to search under
                </Text>
                <Text size="xs" c="dimmed">
                  This kind of work hides under many titles. With a Must use list, these rank
                  matching titles first; without one, a title must name one of them (or, with none
                  picked, be a plain engineering title).
                </Text>
                <Chip.Group
                  multiple
                  value={draft.title_families}
                  onChange={(values) => update({ title_families: values as TitleFamily[] })}
                >
                  <Group gap={6}>
                    {TITLE_FAMILIES.map(({ value, label }) => (
                      <Chip
                        key={value}
                        value={value}
                        size="sm"
                        color={COLOR}
                        variant="light"
                        disabled={locked}
                      >
                        {label}
                      </Chip>
                    ))}
                  </Group>
                </Chip.Group>
              </Stack>

              <Stack gap={6}>
                <Text size="sm" fw={500}>
                  Where
                </Text>
                <Chip.Group
                  multiple
                  value={draft.work_modes}
                  onChange={(values) => update({ work_modes: values as WorkMode[] })}
                >
                  <Group gap={6}>
                    {WORK_MODE_OPTIONS.map(({ value, label }) => (
                      <Chip
                        key={value}
                        value={value}
                        size="sm"
                        color={COLOR}
                        variant="light"
                        disabled={locked}
                      >
                        {label}
                      </Chip>
                    ))}
                  </Group>
                </Chip.Group>
                <TextInput
                  label="Location"
                  description="A city, region or country. Remote jobs must be open to someone there. Naming the US, a state (Philadelphia, PA) or a US time zone also leaves out jobs open only abroad."
                  placeholder="e.g. Philadelphia, PA or US East Coast"
                  maxLength={LOCATION_MAX_LENGTH}
                  value={draft.location}
                  onChange={(event) => update({ location: event.currentTarget.value })}
                  disabled={locked}
                  mt={6}
                />
                <Stack gap={6} mt="sm">
                  <Text size="sm" fw={500}>
                    Open to relocating
                  </Text>
                  <SegmentedControl
                    data={RELOCATIONS.map(({ value, label }) => ({ value, label }))}
                    value={draft.relocation}
                    onChange={(value) => update({ relocation: value as Relocation })}
                    disabled={locked}
                    color={COLOR}
                    fullWidth
                  />
                  <Text size="xs" c="dimmed">
                    {relocationHint}
                  </Text>
                </Stack>
              </Stack>

              <Stack gap={6}>
                <Text size="sm" fw={500}>
                  Posted within
                </Text>
                <SegmentedControl
                  data={windowOptions}
                  value={
                    draft.posted_within_days === null ? 'any' : String(draft.posted_within_days)
                  }
                  onChange={(value) =>
                    update({ posted_within_days: value === 'any' ? null : Number(value) })
                  }
                  disabled={locked}
                  color={COLOR}
                  fullWidth
                />
              </Stack>

              <Stack gap={6}>
                <Text size="sm" fw={500}>
                  How close a fit
                </Text>
                <SegmentedControl
                  data={FITS.map(({ value, label }) => ({ value, label }))}
                  value={draft.fit_mode}
                  onChange={(value) => update({ fit_mode: value as FitMode })}
                  disabled={locked}
                  color={COLOR}
                  fullWidth
                />
                <Text size="xs" c="dimmed">
                  {fitHint} This is about your gaps, never the stack: Must use stays strict. Each
                  lead is marked a strong fit, a good fit or a stretch.
                </Text>
              </Stack>

              <Stack gap={6}>
                <Text size="sm" fw={500}>
                  Agency listings
                </Text>
                <SegmentedControl
                  data={AGENCY_MODES.map(({ value, label }) => ({ value, label }))}
                  value={draft.agency_mode === 'find_employer' ? 'include' : draft.agency_mode}
                  onChange={(value) => update({ agency_mode: value as AgencyMode })}
                  disabled={locked}
                  color={COLOR}
                  fullWidth
                />
                <Text size="xs" c="dimmed">
                  {agencyHint} Agency jobs often pay less, and applying through one can stop the
                  company hiring you directly later.
                </Text>
              </Stack>
            </Stack>
          </Paper>

        </Stack>
      </Grid.Col>

      <Grid.Col span={{ base: 12, md: 5 }}>
        <Stack gap="md">
          <TodayCard
            config={config}
            draft={draft}
            locked={locked}
            onLimitChange={(key, value) => update({ limits: { ...draft.limits, [key]: value } })}
          />
          <ReadsCard />
          <Paper withBorder p="md" radius="md">
            <Stack gap="sm">
              <Group gap="xs" wrap="wrap">
                <Button
                  color={COLOR}
                  onClick={handleSave}
                  loading={save.isPending}
                  disabled={locked || !dirty}
                >
                  Save settings
                </Button>
                <Button
                  variant="subtle"
                  color="gray"
                  disabled={locked || samePayload(draft, defaultPayload(config))}
                  onClick={() => setEdited(defaultPayload(config))}
                >
                  Reset to defaults
                </Button>
              </Group>
              {config.settings.updated_at && (
                <Text size="xs" c="dimmed">
                  Last saved {formatDateTime(config.settings.updated_at)}
                </Text>
              )}
              <RunButton config={config} dirty={dirty} color={COLOR} />
            </Stack>
          </Paper>
        </Stack>
      </Grid.Col>
    </Grid>
  );
}

/** The Search tab: what to look for, where runs stop, and the runs themselves. */
export function ScoutSearchTab() {
  const { data: config, isPending, isError } = useScout();

  if (isPending) return <PageLoader />;
  if (isError || !config) return <PageError />;

  return (
    <Stack>
      <Text size="sm" c="dimmed" maw={720}>
        Describe the work you want. Scout reads every open job Callback knows of, from job
        boards and companies&rsquo; own hiring pages, leaves out in code what can&rsquo;t fit,
        and has Haiku screen and Sonnet double-check the rest against your fact sheet. A brief on
        each lead is written when you ask. Every verdict is remembered, so later runs ask less.
      </Text>
      {config.plan !== 'premium' && (
        <Alert color={COLOR} variant="light" icon={<IconLock size={16} />} maw={720}>
          Scout is part of Premium. You can look through the settings below, but saving and
          running them needs Premium.
        </Alert>
      )}
      {/* Keyed on the save time, so a fresh save resets the form to what the server stored. */}
      <ScoutForm key={config.settings.updated_at ?? 'new'} config={config} />
      {config.plan === 'premium' && <ScoutRuns />}
    </Stack>
  );
}
